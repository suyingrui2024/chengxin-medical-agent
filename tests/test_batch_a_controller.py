"""Batch A controller worker (W3): repro-first tests.

Covers the controller-owned items (EARLY-CLOSE + COMPLETION + F-12
rendering) in src/medagent/consultation.py and src/medagent/agent.py:

- single-clue urgent close must wait for a minimum fact set
  (severity + associated symptoms probed + patient goal);
- trajectory evidence guard: steady-not-improving wording is not
  worsening;
- controller auto-summary fallback (C-06/J-02): a ProposalError from
  review_summary must never kill the session in Phase.BACKGROUND;
- refusal is a recordable state (J-01), not unknown and not invented,
  and model-defined decline options reach the patient as quick replies;
- free-text summary confirmation and self-check skip/cap (D-03/D-04);
- emergency/urgent rendering (R-07/P-11/T-15/F-12) via the scan layer's
  confirmed_trigger_labels helper and the clarification gate for
  semantic_combo-only hits (C-01/C-04);
- neutral conclusion fallback and goal_line consumption at close
  (vocab layer, W2).

Each test is written against the intended post-fix behaviour; on the
pre-fix baseline the controller-path tests fail (urgent close,
ProposalError out of submit, rejected refusal, clarification-less
emergency close, bare "下面是已采集的信息" promise).
"""

import unittest
from copy import deepcopy

from medagent.agent_tools import ProposalError
from medagent.consultation import UserInput
from medagent.domain import (
    AgentReply,
    Assertion,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.events import Event
from medagent.runtime import build_runtime
from tests.test_agent import cmd, fact
from tests.test_agent_wave2_engine import make_engine, record


def _events_of(events, session_id, event_type):
    return [
        event
        for event in events.for_session(session_id)
        if event.event_type == event_type
    ]


def _trajectory_only_state(engine, state):
    """Chief + worsening trajectory, nothing else that gates urgent close."""
    # Every evidence string must be a verbatim substring of the turn
    # text, or the validator partially records the batch.
    text = "我自己腰疼，没有危险信号，近一个月比半年前发得勤了"
    record(
        engine,
        state,
        text,
        [
            fact("consult_for", "self", "我自己"),
            fact("chief_complaint", "腰疼", "腰疼"),
            fact("emergency_screen", None, "没有危险信号", "absent"),
            fact("trajectory", "worsening", "发得勤了"),
        ],
    )
    return text


class UrgentSingleClueGateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_trajectory_only_does_not_close_on_next_turn(self):
        _trajectory_only_state(self.engine, self.state)
        self.assertEqual(self.state.risk.level, RiskLevel.URGENT)
        # Ranker front-load: sex/age/onset precede severity in catalog
        # order, so they must already be recorded for severity to be
        # the pick. Gate facts (severity/associated/goal) are untouched,
        # so the single-clue gate still defers on the next turn.
        record(
            self.engine,
            self.state,
            "男，32岁，两天前开始",
            [
                fact("sex", "male", "男"),
                fact("age_years", 32, "32岁"),
                fact("onset", "两天前开始", "两天前开始"),
            ],
        )
        self.backend.commands = [cmd("ask_question", question_id="severity")]
        reply = self.engine.submit(
            self.state.session_id, UserInput("近一个月比半年前发得勤了")
        )
        self.assertNotEqual(self.state.phase, Phase.CLOSED)
        self.assertNotEqual(reply.kind, "urgent")
        self.assertEqual(reply.question_id, "severity")

    def test_trajectory_recorded_same_turn_does_not_close_immediately(self):
        record(
            self.engine,
            self.state,
            "我自己腰疼，没有危险信号",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
            ],
        )
        # Ranker front-load: sex/age/onset precede severity in catalog
        # order, so they must already be recorded for severity to be
        # the pick once trajectory lands in the measured turn.
        record(
            self.engine,
            self.state,
            "男，32岁，两天前开始",
            [
                fact("sex", "male", "男"),
                fact("age_years", 32, "32岁"),
                fact("onset", "两天前开始", "两天前开始"),
            ],
        )
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("trajectory", "worsening", "发得勤了")],
            ),
            cmd("ask_question", question_id="severity"),
        ]
        reply = self.engine.submit(
            self.state.session_id, UserInput("近一个月比半年前发得勤了")
        )
        self.assertNotEqual(self.state.phase, Phase.CLOSED)
        self.assertNotEqual(reply.kind, "urgent")
        self.assertEqual(reply.question_id, "severity")

    def test_severity_only_does_not_close_on_next_turn(self):
        record(
            self.engine,
            self.state,
            "我自己腰疼，没有危险信号，8分",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("severity", 8, "8分"),
            ],
        )
        self.assertEqual(self.state.risk.level, RiskLevel.URGENT)
        # Ranker pick at this state is sex (first catalog-order
        # candidate). Front-loading to patient_goal is impossible
        # without flipping the gate: reaching patient_goal as the pick
        # requires associated_symptoms recorded/asked, which together
        # with recorded severity would leave only patient_goal missing,
        # so asking it would pass the gate and close urgent — the
        # opposite of what this test asserts. The gate assertions below
        # (not closed, not urgent) are unchanged.
        self.backend.commands = [cmd("ask_question", question_id="sex")]
        reply = self.engine.submit(self.state.session_id, UserInput("腰疼8分"))
        self.assertNotEqual(self.state.phase, Phase.CLOSED)
        self.assertNotEqual(reply.kind, "urgent")
        self.assertEqual(reply.question_id, "sex")

    def test_gate_passes_once_minimum_set_is_present(self):
        record(
            self.engine,
            self.state,
            "我自己腰疼，没有危险信号，近一个月比半年前发得勤了，"
            "6分，腿麻，想知道要不要去检查",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("trajectory", "worsening", "发得勤了"),
                fact("severity", 6, "6分"),
                fact("associated_symptoms", "腿麻", "腿麻"),
                fact("patient_goal", "想知道要不要去检查", "想知道要不要去检查"),
            ],
        )
        self.assertEqual(self.state.risk.level, RiskLevel.URGENT)
        self.assertEqual(self.engine._urgent_gate_missing(self.state), [])
        reply = self.engine._urgent_or_defer(self.state, "t-urgent")
        self.assertIsNotNone(reply)
        self.assertEqual(reply.kind, "urgent")

    def test_gate_missing_lists_the_unprobed_fields(self):
        _trajectory_only_state(self.engine, self.state)
        missing = self.engine._urgent_gate_missing(self.state)
        self.assertIn("severity", missing)
        self.assertIn("associated_symptoms", missing)
        self.assertIn("patient_goal", missing)
        self.assertIsNone(self.engine._urgent_or_defer(self.state, "t-urgent"))


class TrajectoryGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_steady_not_improving_is_not_recorded_as_worsening(self):
        text = "就是慢慢变成现在这样的，一直没怎么好转。"
        record(
            self.engine,
            self.state,
            text,
            [fact("trajectory", "worsening", "一直没怎么好转")],
        )
        self.assertNotIn("trajectory", self.state.facts)
        self.assertTrue(
            _events_of(self.events, self.state.session_id, "fact.trajectory_guard")
        )

    def test_explicit_worsening_still_records(self):
        text = "最近越来越重了。"
        record(
            self.engine,
            self.state,
            text,
            [fact("trajectory", "worsening", "越来越重")],
        )
        self.assertEqual(self.state.get("trajectory"), "worsening")


class ControllerSummaryFallbackTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def _chest_unknown_state(self, with_goal=False):
        text = "我自己32岁心慌，没有危险信号，最近开始，不知道"
        facts = [
            fact("consult_for", "self", "我自己"),
            fact("age_years", 32, "32岁"),
            fact("chief_complaint", "心慌", "心慌"),
            fact("emergency_screen", None, "没有危险信号", "absent"),
            fact("onset", None, "不知道", "unknown"),
            fact("trajectory", None, "不知道", "unknown"),
            fact("severity", None, "不知道", "unknown"),
            fact("associated_symptoms", None, "不知道", "unknown"),
            fact("medications_taken", None, "不知道", "unknown"),
            fact("allergies", None, "不知道", "unknown"),
            fact("relevant_history", None, "不知道", "unknown"),
        ]
        if with_goal:
            facts.append(
                fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院")
            )
        record(self.engine, self.state, text, facts)
        return text

    def test_controller_auto_summary_survives_chest_gate_via_abstain(self):
        # C-06/J-02 shape: goal answered last, chest pathway unexplored
        # (no detail fields, no discriminating evidence), all other
        # blocking fields already attempted as unknown.
        self._chest_unknown_state(with_goal=False)
        self.state.active_question = "patient_goal"
        goal_text = "想知道要不要去医院检查一下，平时怎么缓解紧张"
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact(
                        "patient_goal",
                        "想知道要不要去医院检查一下",
                        "想知道要不要去医院检查一下",
                    )
                ],
            )
        ]
        reply = self.engine.submit(self.state.session_id, UserInput(goal_text))
        self.assertEqual(reply.phase, Phase.SUMMARY)
        self.assertTrue(
            _events_of(self.events, self.state.session_id, "review.abstained")
        )

    def test_controller_summary_probes_discrimination_before_abstaining(self):
        # Associated symptoms never probed: the fallback must ask that
        # discrimination question first instead of jumping to abstain.
        text = "我自己32岁心慌，没有危险信号，最近开始，不知道"
        record(
            self.engine,
            self.state,
            text,
            [
                fact("consult_for", "self", "我自己"),
                fact("age_years", 32, "32岁"),
                fact("chief_complaint", "心慌", "心慌"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("onset", None, "不知道", "unknown"),
                fact("trajectory", None, "不知道", "unknown"),
                fact("severity", None, "不知道", "unknown"),
                fact("medications_taken", None, "不知道", "unknown"),
                fact("allergies", None, "不知道", "unknown"),
                fact("relevant_history", None, "不知道", "unknown"),
                fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院"),
            ],
        )
        exploration = self.engine._pathway_exploration(self.state)
        self.assertTrue(any(not entry["explored"] for entry in exploration))
        reply = self.engine._controller_summary(self.state, text, "t-sum")
        self.assertEqual(reply.question_id, "associated_symptoms")


class RefusalStateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_refusal_lands_as_recorded_not_unknown_rejection(self):
        # J-01: the model proposes unknown with a refusal quote; the old
        # validator rejected it ("未知必须引用…不知道"), deadlocking the
        # summary gate on age_years. A refusal must land as a recorded
        # fact so missing_required is satisfied without inventing a value.
        text = "年龄我不太想说"
        record(
            self.engine,
            self.state,
            text,
            [fact("age_years", None, "年龄我不太想说", "unknown")],
        )
        self.assertIn("age_years", self.state.facts)
        self.assertIsNone(self.state.get("age_years"))
        self.assertTrue(
            _events_of(self.events, self.state.session_id, "fact.declined")
        )
        context = self.engine._context(self.state, text)
        self.assertNotIn("age_years", context["missing_required"])

    def test_missing_message_distinguishes_asked_but_unrecorded(self):
        record(
            self.engine,
            self.state,
            "膝盖疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "膝盖疼", "膝盖疼"),
            ],
        )
        self.state.asked_questions.append("age")
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(
                self.state, cmd("review_summary"), "膝盖疼", "t-sum"
            )
        self.assertIn("age_years（已提问但未落账）", str(caught.exception))

    def test_decline_option_is_mirrored_as_quick_reply(self):
        # J-01: the model-defined decline option must reach the patient
        # view as a quick reply, not only inside adaptive_question.
        reply = self.engine._execute(
            self.state,
            cmd(
                "ask_patient",
                topic="age_years",
                question="你多大？也可以直接选不想说。",
                selection_mode="single",
                options=[
                    {
                        "id": "decline",
                        "label": "不想说",
                        "response_text": "不想说",
                    }
                ],
                allow_other=False,
            ),
            "你多大？",
            "t-ask",
        )
        values = [item.value for item in reply.quick_replies]
        self.assertIn("adaptive_single:decline", values)


class DeterministicRefusalTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def test_age_refusal_records_declined_and_moves_on(self):
        self.engine.submit(self.state.session_id, UserInput("我自己", "self"))
        self.engine.submit(self.state.session_id, UserInput("男", "male"))
        reply = self.engine.submit(
            self.state.session_id, UserInput("年龄我不太想说")
        )
        self.assertNotEqual(reply.kind, "clarification")
        self.assertIn("age_years", self.state.facts)
        self.assertIsNone(self.state.get("age_years"))
        self.assertEqual(reply.question_id, "chief_complaint")


class SummaryConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def _reach_summary(self):
        text = "我自己32岁膝盖疼，没有危险信号，最近开始，不知道，想知道要不要去医院"
        record(
            self.engine,
            self.state,
            text,
            [
                fact("consult_for", "self", "我自己"),
                fact("age_years", 32, "32岁"),
                fact("chief_complaint", "膝盖疼", "膝盖疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("onset", None, "不知道", "unknown"),
                fact("trajectory", None, "不知道", "unknown"),
                fact("severity", None, "不知道", "unknown"),
                fact("associated_symptoms", None, "不知道", "unknown"),
                fact("medications_taken", None, "不知道", "unknown"),
                fact("allergies", None, "不知道", "unknown"),
                fact("relevant_history", None, "不知道", "unknown"),
                fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院"),
            ],
        )
        reply = self.engine._execute(
            self.state, cmd("review_summary"), text, "t-sum"
        )
        self.assertEqual(reply.phase, Phase.SUMMARY)
        return reply

    def test_free_text_confirmation_closes_like_button(self):
        # D-03: "我看了，都对，没什么要改的。" must equal confirm_summary.
        self._reach_summary()
        self.backend.commands = []
        reply = self.engine.submit(
            self.state.session_id, UserInput("我看了，都对，没什么要改的。")
        )
        self.assertEqual(reply.kind, "closed")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")

    def test_free_text_correction_is_not_a_confirmation(self):
        self._reach_summary()
        self.backend.commands = [cmd("ask_question", question_id="age")]
        reply = self.engine.submit(
            self.state.session_id, UserInput("不对，年龄写错了，应该是33岁")
        )
        self.assertNotEqual(self.state.phase, Phase.CLOSED)


class DeterministicSummaryConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.state, _ = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def test_free_text_confirmation_closes(self):
        # End to end through deterministic submit(): the early-confirm
        # check in _submit must close before any pipeline handler runs.
        self.state.phase = Phase.SUMMARY
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("我看了，都对，没什么要改的。"),
        )
        self.assertEqual(reply.kind, "closed")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")


class SelfCheckSkipAndCapTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, _ = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def test_free_text_skip_counts_as_skip_attempt(self):
        # D-03: "跳过。" (with punctuation) must end the task like the
        # skip button, not loop back into the same self-check.
        self.state.active_task = "body_location"
        failure = self.engine._try_task_result(
            self.state, "跳过。", None, "t-skip"
        )
        self.assertIsNone(failure)
        self.assertIn("symptom_location", self.state.facts)

    def test_suspend_cap_forces_return_to_main_flow(self):
        # D-04: after the cap, re-offering the suspended task must stop;
        # the conversation is cleared and planning resumes instead.
        adaptive, _, aevents = make_engine()
        astate, _ = adaptive.create_session()
        task = deepcopy(adaptive.content.tasks["body_location"])
        task["interaction_id"] = "inter-1"
        astate.self_check_conversation = {
            "id": "frame-1",
            "task": task,
            "previous_fact_id": None,
        }
        for index in range(3):
            aevents.append(
                Event(
                    astate.session_id,
                    "self_check.conversation_suspended",
                    {"task_id": "body_location", "interaction_id": "inter-1"},
                    turn_id=f"t-suspend-{index}",
                )
            )
        offered = adaptive._offer_task(astate, "身上起疹子")
        self.assertIsNone(offered)
        self.assertIsNone(astate.self_check_conversation)


class EmergencyRenderingTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def _append_user_turn(self, text, turn_id):
        self.events.append(
            Event(
                self.state.session_id,
                "turn.user_message",
                {"text": text},
                turn_id=turn_id,
            )
        )
        self.state.transcript.append({"role": "user", "content": text})

    def test_f12_denied_labels_never_render_as_triggers(self):
        # F-12 (post-scan-fix): the denied items must not appear as
        # trigger signals, and the affirmed chief complaint must be
        # carried in the handoff summary as a confirmed fact.
        record(
            self.engine,
            self.state,
            "出血是真的很多，突然就出好多血",
            [
                fact(
                    "chief_complaint",
                    "出血是真的很多，突然就出好多血",
                    "出血是真的很多，突然就出好多血",
                )
            ],
        )
        self._append_user_turn("出血是真的很多，突然就出好多血", "t-1")
        self._append_user_turn(
            "出血是真的很多，突然就出好多血。其他几项没有，没有叫不醒，"
            "也没有抽搐、嘴唇发紫、严重呼吸困难和胸痛。",
            "t-2",
        )
        reply = self.engine._emergency_reply(
            self.state,
            ["严重呼吸困难", "口唇发紫", "高风险胸痛描述"],
        )
        self.assertNotIn("嘴唇发紫", reply.message)
        self.assertNotIn("严重呼吸困难", reply.message)
        self.assertNotIn("胸痛", reply.message)
        self.assertNotIn("口唇发紫", reply.summary["triggers"])
        self.assertTrue(reply.summary["confirmed_facts"])

    def test_screen_confirmed_trigger_still_renders(self):
        self._append_user_turn("鼻血一直在流，压着也不停", "t-1")
        reply = self.engine._emergency_reply(self.state, ["患者确认急症筛查项"])
        self.assertIn("患者确认急症筛查项", reply.summary["triggers"])
        self.assertIn("120", reply.message)


class ClarificationGateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_combo_only_turn_asks_clarification_not_emergency(self):
        # C-01: bare chest location x pain co-occurrence must not close
        # the session irreversibly; it asks a discriminating question.
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"),
        )
        self.assertEqual(reply.kind, "clarification")
        self.assertNotEqual(self.state.phase, Phase.CLOSED)
        values = [item.value for item in reply.quick_replies]
        self.assertIn("confirm_emergency_clarification", values)
        self.assertIn("deny_emergency_clarification", values)

    def test_confirmed_clarification_then_takes_emergency_path(self):
        self.engine.submit(
            self.state.session_id,
            UserInput("我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"),
        )
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "是的，我确实有胸部不适", "confirm_emergency_clarification"
            ),
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertIn("120", reply.message)

    def test_denied_clarification_does_not_close(self):
        self.engine.submit(
            self.state.session_id,
            UserInput("我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"),
        )
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("没有，我没有这种情况", "deny_emergency_clarification"),
        )
        self.assertNotEqual(reply.kind, "emergency")
        self.assertNotEqual(self.state.phase, Phase.CLOSED)

    def test_definite_hit_still_closes_immediately(self):
        reply = self.engine.submit(
            self.state.session_id, UserInput("他现在叫不醒")
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.phase, Phase.CLOSED)


class UrgentRenderingTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_urgent_message_lists_collected_facts(self):
        record(
            self.engine,
            self.state,
            "我自己腰疼，没有危险信号，近一个月比半年前发得勤了，"
            "6分，腿麻，想知道要不要去检查",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("trajectory", "worsening", "发得勤了"),
                fact("severity", 6, "6分"),
                fact("associated_symptoms", "腿麻", "腿麻"),
                fact("patient_goal", "想知道要不要去检查", "想知道要不要去检查"),
            ],
        )
        self.state.risk = RiskAssessment(RiskLevel.URGENT, ["症状持续加重"])
        reply = self.engine._urgent(self.state)
        self.assertEqual(reply.kind, "urgent")
        self.assertNotIn("下面是已采集的信息，不必等", reply.message)
        self.assertIn("影响程度", reply.message)


class ConclusionCloseTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_off_pathway_close_uses_neutral_fallback(self):
        # Vocab layer (W2): compose_conclusion already applies
        # apply_neutral_fallback for direction-less off-pathway sheets;
        # the controller choke point (_attach_conclusion) must carry it.
        record(
            self.engine,
            self.state,
            "我自己，眼前突然闪光，还感觉一块黑影挡着视野，"
            "没有危险信号，想知道要不要马上去医院",
            [
                fact("consult_for", "self", "我自己"),
                fact(
                    "chief_complaint",
                    "眼前突然闪光，还感觉一块黑影挡着视野",
                    "眼前突然闪光，还感觉一块黑影挡着视野",
                ),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("patient_goal", "想知道要不要马上去医院", "想知道要不要马上去医院"),
            ],
        )
        self.state.phase = Phase.CLOSED
        self.state.closed_reason = "confirmed_by_user"
        summary = self.engine._summary_payload(
            self.state, review_status="closed_unconfirmed"
        )
        reply = AgentReply(
            "本次信息已整理。", Phase.CLOSED, kind="closed", summary=summary
        )
        self.engine._attach_conclusion(self.state, reply, "t-close")
        sheet = self.state.conclusion
        self.assertTrue(sheet)
        self.assertTrue(sheet.get("neutral_fallback"))

    def test_self_care_close_consumes_goal_line(self):
        # C-02 (controller side): a check-oriented goal plus a
        # self_care disposition must not end with only "暂时不需要跑一趟
        # 医院" — the close line must say when offline evaluation is
        # still needed and which entry takes it.
        self.state.closed_reason = "confirmed_by_user"
        sheet = {
            "disposition": {
                "level": "self_care",
                "window_hours": 48,
                "text": "可以在家先观察，暂时不需要专门跑一趟医院。",
            },
            "goal_line": "想知道要不要去检查",
            "department_hint": "全科医疗科",
            "department_visible": False,
            "return_now_if": ["意识异常或抽搐"],
        }
        reply = AgentReply(
            "你已确认本次摘要，会谈现已结束。",
            Phase.CLOSED,
            kind="closed",
            conclusion=sheet,
        )
        self.engine._speak_close_line(self.state, reply)
        self.assertIn("线下评估", reply.message)
        self.assertNotIn("全科医疗科", reply.message)


class SuspendCapSemanticsTests(unittest.TestCase):
    """RW2 rework: the cap counts *consecutive* suspensions of one task
    occurrence (task_id + interaction_id). A resume, completion, new
    offer or clear resets the run, so different occurrences each
    suspended once never accumulate toward the cap."""

    def _frame_state(self):
        adaptive, _, aevents = make_engine()
        astate, _ = adaptive.create_session()
        task = deepcopy(adaptive.content.tasks["body_location"])
        task["interaction_id"] = "inter-1"
        astate.self_check_conversation = {
            "id": "frame-1",
            "task": task,
            "previous_fact_id": None,
        }
        return adaptive, astate, aevents

    def _suspend(self, aevents, astate, turn):
        aevents.append(
            Event(
                astate.session_id,
                "self_check.conversation_suspended",
                {"task_id": "body_location", "interaction_id": "inter-1"},
                turn_id=turn,
            )
        )

    def _resume(self, aevents, astate, turn):
        aevents.append(
            Event(
                astate.session_id,
                "self_check.conversation_resumed",
                {"task_id": "body_location", "interaction_id": "inter-1"},
                turn_id=turn,
            )
        )

    def test_consecutive_suspends_count_and_resume_resets(self):
        adaptive, astate, aevents = self._frame_state()
        for index in range(3):
            self._suspend(aevents, astate, f"t-{index}")
        self.assertEqual(
            adaptive._self_check_suspend_count(astate, "body_location", "inter-1"),
            3,
        )
        adaptive, astate, aevents = self._frame_state()
        self._suspend(aevents, astate, "t-0")
        self._resume(aevents, astate, "t-0")
        self._suspend(aevents, astate, "t-1")
        self._resume(aevents, astate, "t-1")
        self._suspend(aevents, astate, "t-2")
        self.assertEqual(
            adaptive._self_check_suspend_count(astate, "body_location", "inter-1"),
            1,
        )
        aevents.append(
            Event(
                astate.session_id,
                "self_check.conversation_suspended",
                {"task_id": "body_location", "interaction_id": "inter-2"},
                turn_id="t-3",
            )
        )
        self.assertEqual(
            adaptive._self_check_suspend_count(astate, "body_location", "inter-1"),
            1,
        )

    def test_cap_clears_and_next_reply_carries_skip_notice(self):
        adaptive, astate, aevents = self._frame_state()
        for index in range(3):
            self._suspend(aevents, astate, f"t-{index}")
        offered = adaptive._offer_task(astate, "身上起疹子")
        self.assertIsNone(offered)
        self.assertIsNone(astate.self_check_conversation)
        reply = adaptive._plan_next(astate)
        self.assertTrue(
            reply.message.startswith("这一步先跳过，回到主流程继续。"),
            reply.message,
        )
        noticed = _events_of(
            aevents, astate.session_id, "self_check.suspend_cap_noticed"
        )
        self.assertEqual(len(noticed), 1)

    def test_five_location_drafts_do_not_cap(self):
        # The regression mode from test_location_answers: one session,
        # five consecutive location-draft submissions on the same
        # clarified task. Each draft suspends and resumes the task, so
        # the consecutive run never reaches the cap.
        from tests.test_location_answers import LocationAnswerTests

        case = LocationAnswerTests(
            "test_uncertain_or_unsupported_words_cannot_become_positive_drafts"
        )
        case.setUp()
        case.offer()
        for text, proposed in [
            ("可能右太阳穴附近", {"side": "right"}),
            ("可能右太阳穴附近", {"area": {"kind": "text", "value": "右太阳穴"}}),
            ("右太阳穴附近", {"extent": "area"}),
            ("额头附近", {"area": {"kind": "preset", "value": "temple"}}),
            ("右太阳穴附近", {"extent": "unknown"}),
        ]:
            case.backend.commands = [
                cmd(
                    "propose_self_check_result",
                    task_id="body_location",
                    option="head",
                    evidence=text,
                    input=proposed,
                ),
                cmd("show_self_check", task_id="body_location"),
            ]
            reply = case.helper.submit(text)
            self.assertIsNotNone(reply.task)
            self.assertIn("answer_spec", reply.task)
        caps = _events_of(
            case.engine.events,
            case.state.session_id,
            "self_check.suspend_cap_reached",
        )
        self.assertEqual(caps, [])


class BypassClosureTests(unittest.TestCase):
    def _observation_resume(self, state):
        state.observation_resume = {
            "task_id": "axillary_temperature",
            "phase": Phase.HISTORY.value,
            "question_id": None,
            "clarification": False,
            "reply": None,
            "attempt_id": "att-1",
            "pending_calculation": None,
            "calculation_resume": None,
        }
        state.active_task = "axillary_temperature"
        state.observation_attempts = [
            {
                "id": "att-1",
                "task_id": "axillary_temperature",
                "previous_fact_id": None,
            }
        ]

    def test_observation_resume_urgent_single_clue_defers(self):
        engine, _, events = make_engine()
        state, _ = engine.create_session()
        record(
            engine,
            state,
            "我自己腰疼，没有危险信号，近一个月比半年前发得勤了",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("trajectory", "worsening", "发得勤了"),
            ],
        )
        self.assertEqual(state.risk.level, RiskLevel.URGENT)
        self._observation_resume(state)
        reply = engine._handle_observation_action(
            state, UserInput("腋温36.5度"), "腋温36.5度", "t-obs"
        )
        self.assertIsNotNone(reply)
        self.assertNotEqual(reply.kind, "urgent")
        self.assertNotEqual(state.phase, Phase.CLOSED)
        self.assertTrue(
            _events_of(events, state.session_id, "triage.urgent_deferred")
        )

    def test_observation_resume_combo_only_clarifies_not_emergency(self):
        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        record(
            engine,
            state,
            "我自己32岁，头疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "头疼", "头疼"),
            ],
        )
        self._observation_resume(state)
        text = "腋温36.5度，胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"
        reply = engine._handle_observation_action(
            state, UserInput(text), text, "t-obs"
        )
        self.assertIsNotNone(reply)
        self.assertEqual(reply.kind, "clarification")
        self.assertNotEqual(state.phase, Phase.CLOSED)
        values = [item.value for item in reply.quick_replies]
        self.assertIn("confirm_emergency_clarification", values)
        self.assertIn("deny_emergency_clarification", values)

    def test_planner_stuck_with_downgraded_risk_finishes_not_urgent(self):
        from medagent.reasoning import DifferentialPlanner, ReasoningWorkspace

        class _StaticProposer:
            def __init__(self, raw):
                self._workspace = ReasoningWorkspace.from_dict(raw)

            def propose(self, state):
                return self._workspace

        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        try:
            state, _ = engine.create_session()
            for question in engine.content.questions:
                state.record(
                    Fact(
                        key=question.fact_key,
                        value="已填",
                        assertion=Assertion.PRESENT,
                        source=Source.PATIENT,
                        turn_id="t",
                    )
                )
            state.reasoning = {
                "version": 4,
                "stagnation_turns": 3,
                "hypotheses": [
                    {
                        "id": "lower_respiratory",
                        "label": "下呼吸道感染/肺炎可能",
                        "category": "serious_condition",
                        "prior_band": "medium",
                        "status": "active",
                        "evidence_for": [
                            {
                                "fact_key": "chief_complaint",
                                "fact_id": state.facts["chief_complaint"].fact_id,
                                "quote": "已填",
                            }
                        ],
                        "evidence_against": [],
                        "red_flags": ["severe_breathing"],
                        "discriminating_questions": [],
                        "updated_at_rev": 0,
                    }
                ],
            }
            # Downgraded risk: a prior deferral stood the urgent close
            # down, leaving empty triggers. The stuck rule must not
            # turn that into a SUMMARY-phase reply of kind "urgent".
            state.risk = RiskAssessment(RiskLevel.ROUTINE, [])
            engine._offer_task = lambda *a, **k: None
            planner = DifferentialPlanner(engine)
            planner._proposer = _StaticProposer(state.reasoning)
            reply = planner.plan(state)
            self.assertEqual(reply.kind, "summary")
            self.assertEqual(reply.phase, Phase.SUMMARY)
            self.assertFalse(
                reply.kind == "urgent" and reply.phase == Phase.SUMMARY
            )
        finally:
            host.stop()

    def test_controller_summary_resume_failure_falls_back_to_plan(self):
        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        record(
            engine,
            state,
            "我自己腰疼，没有危险信号",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "腰疼", "腰疼"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
            ],
        )
        for key in (
            "associated_symptoms",
            "relevant_history",
            "back_details",
        ):
            state.record(
                Fact(
                    key=key,
                    value="无",
                    assertion=Assertion.PRESENT,
                    source=Source.PATIENT,
                    evidence="无",
                    turn_id="t",
                )
            )
        state.record(
            Fact(
                key="symptom_location",
                value="部位：头",
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                evidence="头疼",
                turn_id="t",
            )
        )
        task = deepcopy(engine.content.tasks["body_location"])
        task["interaction_id"] = "inter-9"
        # The recorded symptom_location fact disagrees with the
        # frame's previous_fact_id, so the resume clears the frame
        # and raises ValueError; the controller fallback must plan
        # instead of letting it reach the driver.
        state.self_check_conversation = {
            "id": "frame-9",
            "task": task,
            "previous_fact_id": None,
        }
        reply = engine._controller_summary(state, "请整理摘要", "t-sum")
        self.assertIsNotNone(reply)
        self.assertIsNone(state.self_check_conversation)
        self.assertEqual(reply.kind, "question")

    def test_restore_fact_confirmed_clarification_replays_signal_intact(self):
        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        events = context.services.require("events")
        try:
            state, _ = engine.create_session()
            events.append(
                Event(
                    state.session_id,
                    "turn.user_message",
                    {"text": "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心"},
                    turn_id="t-1",
                )
            )
            state.transcript.append(
                {
                    "role": "user",
                    "content": "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心",
                }
            )
            events.append(
                Event(
                    state.session_id,
                    "emergency.clarification_confirmed",
                    {"labels": ["高风险胸痛描述"]},
                    turn_id="t-2",
                )
            )
            state.phase = Phase.CLOSED
            state.closed_reason = "emergency_route"
            state.risk = RiskAssessment(
                RiskLevel.EMERGENCY, ["高风险胸痛描述"]
            )
            state.record(
                Fact(
                    key="severity",
                    value=5,
                    assertion=Assertion.PRESENT,
                    source=Source.PATIENT,
                    evidence="5分",
                    turn_id="t-a",
                )
            )
            state.record(
                Fact(
                    key="severity",
                    value=7,
                    assertion=Assertion.PRESENT,
                    source=Source.PATIENT,
                    evidence="7分",
                    turn_id="t-b",
                )
            )
            reply = engine.restore_fact(state.session_id, "severity")
            self.assertEqual(reply.kind, "emergency")
            self.assertIn("高风险胸痛描述", reply.summary["triggers"])
            self.assertIn("高风险胸痛描述", reply.message)
        finally:
            host.stop()


if __name__ == "__main__":
    unittest.main()
