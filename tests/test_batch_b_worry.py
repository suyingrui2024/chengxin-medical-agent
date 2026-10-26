"""Disease batch B closing work (WORRY / GOAL).

Covers only this worker's four files: worry capture in both engines'
user-turn entries (consultation.py _begin_turn, agent.py _submit),
the spoken close line appends (_speak_close_line), and the summary
label fallback (_summary_payload). The conclusion sheet's worry /
goal_response slots themselves are the conclusion worker's delivery
(conclusion.py, not modified here); these tests consume them through
the published contract: sheet["worry"] = {"object", "response"} or
None, sheet["goal_response"] = str or None (visit-keyword goals
excluded by the composer, answered by the batch-A close branch).

GOAL gate note (item 5 investigation, pinned by GoalGateTests):
patient_goal is already in the required set — its question defaults
to blocking=True, so both the deterministic planner and the adaptive
review_summary gate treat it as required for a routine close. No new
inclusion change was needed, and none is made here. The pinned
residual is a口径 mismatch, not a missing membership: a goal recorded
with a non-PRESENT assertion (e.g. UNKNOWN) satisfies the gate, which
only checks presence in state.facts, while compose_conclusion only
reads PRESENT goals for goal_line — so goal_line can still be null
after a routine close. Urgent-route closes skip background by design
(batch A gated only single-clue urgent triggers).
"""

import unittest

from medagent import closing
from medagent.conclusion import compose_conclusion, spoken_close_line
from medagent.consultation import UserInput
from medagent.domain import (
    AgentReply,
    Assertion,
    Fact,
    Phase,
    Source,
)
from medagent.agent_tools import ProposalError
from medagent.knowledge import ContentRepository
from medagent.runtime import build_runtime
from tests.test_agent import cmd, fact
from tests.test_agent_wave2_engine import make_engine

WORRY_1 = "我这是不是食物中毒？"
WORRY_2 = "我这是不是阑尾炎？"

# Answer map proven by tests/test_closed_outcome.py for a routine
# deterministic drive; individual tests override one entry each.
BASE_ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("女", "female"),
    "pregnancy_status": ("没有怀孕，月经没有推迟", "not_pregnant"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "gi_details": ("肚脐周围隐痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想知道平时怎么护眼", None),
}


def _worry_events(engine, session_id):
    return [
        event
        for event in engine.events.for_session(session_id)
        if event.event_type == "worry.captured"
    ]


class DeterministicCaptureTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_first_worry_wins_and_never_writes_patient_goal(self):
        state, _ = self.engine.create_session()
        self.engine.submit(state.session_id, UserInput(WORRY_1))
        self.engine.submit(state.session_id, UserInput(WORRY_2))
        captured = state.facts.get("patient_worry")
        self.assertIsNotNone(captured)
        self.assertEqual(captured.value, "食物中毒")
        self.assertEqual(captured.assertion, Assertion.PRESENT)
        self.assertEqual(captured.source, Source.PATIENT)
        self.assertEqual(captured.evidence, WORRY_1)
        self.assertNotIn("patient_goal", state.facts)
        events = _worry_events(self.engine, state.session_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["object"], "食物中毒")

    def test_non_worry_severity_and_plan_sentences_do_not_capture(self):
        for text in (
            "肚子疼了两天",
            "我这是不是很严重？",
            "我这是不是要去医院？",
        ):
            state, _ = self.engine.create_session()
            self.engine.submit(state.session_id, UserInput(text))
            self.assertNotIn("patient_worry", state.facts)
            self.assertEqual(
                _worry_events(self.engine, state.session_id), []
            )


class AdaptiveCaptureTests(unittest.TestCase):
    def test_first_worry_wins_on_adaptive_turns(self):
        engine, backend, _ = make_engine()
        state, _ = engine.create_session()
        backend.commands = [
            cmd("ask_question", question_id="emergency_screen"),
            cmd("ask_question", question_id="onset"),
        ]
        engine.submit(state.session_id, UserInput(WORRY_1))
        engine.submit(state.session_id, UserInput(WORRY_2))
        captured = state.facts.get("patient_worry")
        self.assertIsNotNone(captured)
        self.assertEqual(captured.value, "食物中毒")
        self.assertEqual(captured.assertion, Assertion.PRESENT)
        self.assertEqual(captured.source, Source.PATIENT)
        self.assertNotIn("patient_goal", state.facts)
        events = _worry_events(engine, state.session_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload["object"], "食物中毒")


class DeterministicCloseTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _close_confirmed(self, answers):
        state, reply = self.engine.create_session()

        def answer(text, choice=None):
            return self.engine.submit(
                state.session_id, UserInput(text, choice)
            )

        for _ in range(30):
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = answer("暂时跳过", "skip")
                continue
            text, choice = answers[reply.question_id]
            reply = answer(text, choice)
        self.assertEqual(reply.kind, "summary")
        return state, answer("确认", "confirm_summary")

    def test_close_speaks_worry_response_and_goal_response(self):
        answers = dict(BASE_ANSWERS)
        answers["associated_symptoms"] = (WORRY_1, None)
        state, reply = self._close_confirmed(answers)
        self.assertEqual(state.facts["patient_worry"].value, "食物中毒")
        # Routine close walked background: the goal was asked and landed.
        self.assertEqual(
            state.facts["patient_goal"].value, "想知道平时怎么护眼"
        )
        sheet = reply.conclusion
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet["worry"]["object"], "食物中毒")
        self.assertIn("你问过是不是‘食物中毒’", reply.message)
        self.assertIn(sheet["worry"]["response"], reply.message)
        self.assertIsNotNone(sheet["goal_response"])
        self.assertIn(sheet["goal_response"], reply.message)
        self.assertEqual(reply.message.count(sheet["goal_response"]), 1)
        self.assertLess(
            reply.message.index(sheet["worry"]["response"]),
            reply.message.index(sheet["goal_response"]),
        )

    def test_visit_keyword_goal_uses_batch_a_branch_only(self):
        answers = dict(BASE_ANSWERS)
        answers["associated_symptoms"] = (WORRY_1, None)
        answers["patient_goal"] = ("想知道要不要去医院", None)
        _, reply = self._close_confirmed(answers)
        sheet = reply.conclusion
        self.assertIsNotNone(sheet)
        # The composer excludes visit-keyword goals from goal_response;
        # the batch-A branch in _speak_close_line owns that answer.
        self.assertIsNone(sheet["goal_response"])
        self.assertEqual(reply.message.count("你问到要不要去检查"), 1)
        self.assertIn("你问过是不是‘食物中毒’", reply.message)
        self.assertLess(
            reply.message.index("你问到要不要去检查"),
            reply.message.index("你问过是不是‘食物中毒’"),
        )


class CloseLineUnitTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _speak(self, sheet):
        state, _ = self.engine.create_session()
        state.closed_reason = "confirmed_by_user"
        reply = AgentReply(
            "原收尾。",
            Phase.CLOSED,
            kind="closed",
            conclusion=sheet,
        )
        closing.render_confirmed_close(state, reply)
        return reply.message

    def _sheet(self, **overrides):
        sheet = {
            "disposition": {"level": "self_care"},
            "goal_line": "想整理就医记录",
            "worry": None,
            "goal_response": None,
        }
        sheet.update(overrides)
        return sheet

    def test_batch_a_then_worry_then_goal_order(self):
        message = self._speak(
            self._sheet(
                goal_line="想知道要不要去检查",
                worry={
                    "object": "食物中毒",
                    "response": "关于食物中毒，目前还不能确定。",
                },
                goal_response="关于平时怎么护眼，建议线下复查时一并确认。",
            )
        )
        self.assertEqual(message.count("你问到要不要去检查"), 1)
        self.assertEqual(message.count("关于食物中毒，目前还不能确定。"), 1)
        self.assertEqual(
            message.count("关于平时怎么护眼，建议线下复查时一并确认。"), 1
        )
        self.assertLess(
            message.index("你问到要不要去检查"),
            message.index("关于食物中毒"),
        )
        self.assertLess(
            message.index("关于食物中毒"),
            message.index("关于平时怎么护眼"),
        )

    def test_goal_response_without_batch_a(self):
        message = self._speak(
            self._sheet(goal_response="关于平时怎么护眼，建议线下复查时一并确认。")
        )
        self.assertNotIn("你问到要不要去检查", message)
        self.assertEqual(
            message.count("关于平时怎么护眼，建议线下复查时一并确认。"), 1
        )

    def test_absent_slots_leave_close_untouched(self):
        sheet = self._sheet()
        message = self._speak(sheet)
        self.assertEqual(message, spoken_close_line(sheet) + "原收尾。")

    def test_malformed_worry_does_not_break_close(self):
        sheet = self._sheet(worry="oops")
        message = self._speak(sheet)
        self.assertEqual(message, spoken_close_line(sheet) + "原收尾。")

    def test_unterminated_response_gets_period(self):
        message = self._speak(
            self._sheet(
                worry={"object": "食物中毒", "response": "关于食物中毒目前还不能确定"}
            )
        )
        self.assertIn("关于食物中毒目前还不能确定。", message)
        self.assertNotIn("确定原收尾", message)


class SummaryLabelTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_label_fallback_prefers_handoff_table_then_key(self):
        state, _ = self.engine.create_session()
        state.record(
            Fact(
                key="sex",
                value="female",
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence="女",
            )
        )
        state.record(
            Fact(
                key="age_years",
                value=32,
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence="32岁",
            )
        )
        state.record(
            Fact(
                key="patient_worry",
                value="食物中毒",
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence=WORRY_1,
            )
        )
        payload = self.engine._summary_payload(
            state, review_status="confirmed_by_user"
        )
        labels = {row["key"]: row["label"] for row in payload["known"]}
        self.assertEqual(labels["sex"], "性别")
        self.assertEqual(labels["age_years"], "年龄")
        # Neither table carries patient_worry: the chain ends at the key.
        self.assertEqual(labels["patient_worry"], "patient_worry")


class GoalGateTests(unittest.TestCase):
    def test_patient_goal_is_blocking_and_gates_routine_summary(self):
        repo = ContentRepository()
        by_id = {question.id: question for question in repo.questions}
        self.assertTrue(by_id["patient_goal"].blocking)
        engine, backend, _ = make_engine()
        state, _ = engine.create_session()
        backend.commands = []
        context = engine._context(state, "")
        self.assertIn("patient_goal", context["missing_required"])
        with self.assertRaises(ProposalError) as caught:
            engine._execute(
                state, cmd("review_summary"), "", "t-gate"
            )
        self.assertIn("patient_goal", str(caught.exception))

    def test_unknown_goal_passes_gate_but_goal_line_stays_null(self):
        # Residual R-GOAL-1, pinned as current behavior: the gate only
        # checks presence in state.facts, while compose_conclusion only
        # reads PRESENT goals. If this口径 is ever unified, update this
        # test to the new contract rather than deleting the coverage.
        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        from tests.test_agent_wave2_engine import record

        record(
            engine,
            state,
            "不知道",
            [fact("patient_goal", None, "不知道", "unknown")],
        )
        self.assertIn("patient_goal", state.facts)
        self.assertNotIn(
            "patient_goal", engine._context(state, "")["missing_required"]
        )
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        sheet = compose_conclusion(state)
        self.assertIsNotNone(sheet)
        self.assertIsNone(sheet["goal_line"])


if __name__ == "__main__":
    unittest.main()
