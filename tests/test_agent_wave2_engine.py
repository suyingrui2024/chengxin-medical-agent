"""Wave-2 engine fixes (live-eval rounds 1-2 follow-up).

Covers: non-blocking candidates (sex / reported_temperature never gate
the summary), fact-dependent candidate gates (pregnancy_status),
ask_patient topics widened to the registered field set, partial
record_facts landing, the incidentally-recorded screening signal, the
age question's tone adaptation, and verification that the shared
_record_reply / _emergency_reply choke points give the adaptive engine
the spoken close line and the emergency explanation exactly once.
"""

import json
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError
from medagent.conclusion import spoken_close_line
from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


def make_engine():
    backend = ScriptBackend()
    events = EventStore()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    return engine, backend, events


def record(engine, state, text, facts, turn="t-record"):
    return engine._execute(state, cmd("record_facts", facts=facts), text, turn)


def ask_patient_cmd(topic, question="请具体说说这方面的情况？"):
    return cmd(
        "ask_patient",
        topic=topic,
        question=question,
        selection_mode="text",
        options=[],
        allow_other=False,
    )


class KnowledgeLoadingTests(unittest.TestCase):
    def setUp(self):
        self.repo = ContentRepository()
        self.by_id = {q.id: q for q in self.repo.questions}

    def test_blocking_defaults_true_and_loads_false(self):
        self.assertFalse(self.by_id["sex"].blocking)
        self.assertFalse(self.by_id["reported_temperature"].blocking)
        self.assertTrue(self.by_id["age"].blocking)
        self.assertTrue(self.by_id["chief_complaint"].blocking)

    def test_fact_gate_metadata_loaded(self):
        pregnancy = self.by_id["pregnancy_status"]
        self.assertEqual(pregnancy.excludes_fact_equals, {"sex": ["male"]})
        self.assertEqual(pregnancy.age_between_when_known, [12, 55])
        self.assertEqual(self.by_id["age"].excludes_fact_equals, {})
        self.assertIsNone(self.by_id["age"].age_between_when_known)

    def test_gate_metadata_not_forwarded_to_model_catalog(self):
        engine, _, _ = make_engine()
        for entry in engine.static_context["question_catalog"]:
            self.assertEqual(
                set(entry), {"id", "field", "prompt", "applies_when"}
            )


class BlockingCandidateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_non_blocking_are_candidates_but_never_missing(self):
        record(
            self.engine,
            self.state,
            "发烧两天",
            [fact("chief_complaint", "发烧", "发烧")],
        )
        context = self.engine._context(self.state, "发烧两天")
        fields = [q["field"] for q in context["questions"]]
        self.assertIn("reported_temperature", fields)
        self.assertIn("sex", fields)
        self.assertNotIn("reported_temperature", context["missing_required"])
        self.assertNotIn("sex", context["missing_required"])
        # A blocking control field is still reported missing.
        self.assertIn("resp_breathing", context["missing_required"])

    def test_review_summary_not_gated_by_non_blocking_questions(self):
        text = (
            "我自己32岁，发烧两天，没有危险信号，病情变化不大，不适程度3分，"
            "没有用药，过敏不知道，没有相关病史，想知道要不要去医院，其它不知道"
        )
        record(
            self.engine,
            self.state,
            text,
            [
                fact("consult_for", "self", "我自己"),
                fact("age_years", 32, "32岁"),
                fact("chief_complaint", "发烧", "发烧"),
                fact("emergency_screen", None, "没有危险信号", "absent"),
                fact("onset", "两天", "两天"),
                fact("trajectory", None, "其它不知道", "unknown"),
                fact("severity", None, "其它不知道", "unknown"),
                fact("associated_symptoms", None, "其它不知道", "unknown"),
                fact("resp_breathing", None, "其它不知道", "unknown"),
                fact("resp_cough_details", None, "其它不知道", "unknown"),
                fact("resp_intake", None, "其它不知道", "unknown"),
                fact("medications_taken", None, "没有用药", "absent"),
                fact("allergies", None, "过敏不知道", "unknown"),
                fact("relevant_history", None, "没有相关病史", "absent"),
                fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院"),
            ],
        )
        # sex and reported_temperature were never asked or recorded.
        self.assertNotIn("sex", self.state.facts)
        self.assertNotIn("reported_temperature", self.state.facts)
        reply = self.engine._execute(
            self.state, cmd("review_summary"), text, "t-summary"
        )
        self.assertEqual(reply.kind, "summary")


class FactGateTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def candidate_fields(self):
        context = self.engine._context(self.state, "核对")
        return [q["field"] for q in context["questions"]], context

    def test_male_excludes_pregnancy(self):
        record(
            self.engine,
            self.state,
            "我自己，男，30岁，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "male", "男"),
                fact("age_years", 30, "30岁"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        fields, _ = self.candidate_fields()
        self.assertNotIn("pregnancy_status", fields)

    def test_female_in_age_window_includes_pregnancy(self):
        record(
            self.engine,
            self.state,
            "我自己，女，30岁，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "female", "女"),
                fact("age_years", 30, "30岁"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        fields, context = self.candidate_fields()
        self.assertIn("pregnancy_status", fields)
        # It is a blocking question: candidacy also means it gates.
        self.assertIn("pregnancy_status", context["missing_required"])

    def test_female_outside_age_window_excludes_pregnancy(self):
        record(
            self.engine,
            self.state,
            "我自己，女，60岁，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "female", "女"),
                fact("age_years", 60, "60岁"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        fields, _ = self.candidate_fields()
        self.assertNotIn("pregnancy_status", fields)

    def test_unknown_age_keeps_pregnancy_eligible(self):
        record(
            self.engine,
            self.state,
            "我自己，女，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "female", "女"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        fields, _ = self.candidate_fields()
        self.assertIn("pregnancy_status", fields)

    def test_excluded_fixed_question_is_rejected(self):
        record(
            self.engine,
            self.state,
            "我自己，男，30岁，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "male", "男"),
                fact("age_years", 30, "30岁"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(
                self.state,
                cmd("ask_question", question_id="pregnancy_status"),
                "肚子疼",
                "t-ask",
            )
        self.assertIn("排除", str(caught.exception))


class AskPatientWideningTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    def test_topic_outside_candidates_can_be_asked(self):
        # Chief complaint is abdominal; urinary_details never candidated
        # (no urinary keyword), yet it is a registered field and askable.
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("chief_complaint", "肚子疼", "肚子疼"),
                    fact("onset", "一天", "一天"),
                ],
            ),
            ask_patient_cmd(
                "urinary_details",
                "你说肚子疼一天了，为了确认有没有尿路方面的问题，想先确认一下：小便方面有什么异常吗？",
            ),
        ]
        reply = self.submit("我自己，肚子疼一天了")
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "urinary_details")
        self.assertEqual(reply.adaptive_question["topic"], "urinary_details")

    def test_recorded_topic_allows_targeted_follow_up(self):
        text = "我自己，肚子疼，小便有点疼"
        record(
            self.engine,
            self.state,
            text,
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
                fact("urinary_details", "小便有点疼", "小便有点疼"),
            ],
        )
        reply = self.engine._execute(
            self.state,
            ask_patient_cmd(
                "urinary_details",
                "你说小便有点疼，为了确认疼痛规律，想先确认一下：尿痛是每次都有，还是时有时无？",
            ),
            text,
            "t-follow",
        )
        self.assertEqual(reply.kind, "question")
        context = self.engine._context(self.state, text)
        self.assertEqual(
            context["active_question_field_already_recorded"], "urinary_details"
        )

    def test_excluded_topic_is_rejected(self):
        record(
            self.engine,
            self.state,
            "我自己，男，30岁，肚子疼",
            [
                fact("consult_for", "self", "我自己"),
                fact("sex", "male", "男"),
                fact("chief_complaint", "肚子疼", "肚子疼"),
            ],
        )
        with self.assertRaises(ProposalError) as caught:
            self.engine._execute(
                self.state,
                ask_patient_cmd(
                    "pregnancy_status",
                    "你说肚子疼，为了确认筛查适用，想先确认一下：请具体说说这方面的情况？",
                ),
                "肚子疼",
                "t-excluded",
            )
        self.assertIn("排除", str(caught.exception))


class PartialRecordingTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    def test_valid_items_land_and_only_failures_are_returned(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("allergies", None, "没有过敏", "absent"),
                ],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = self.submit("我自己32岁，咳嗽两天")
        self.assertEqual(reply.question_id, "emergency_screen")
        self.assertEqual(self.state.get("consult_for"), "self")
        self.assertEqual(self.state.get("age_years"), 32)
        self.assertNotIn("allergies", self.state.facts)
        feedback = json.loads(self.backend.calls[1][-1]["content"])["tool_result"]
        self.assertFalse(feedback["ok"])
        self.assertTrue(feedback["partial"])
        self.assertEqual(feedback["recorded"], ["consult_for", "age_years"])
        self.assertEqual(
            [item["key"] for item in feedback["rejected"]], ["allergies"]
        )
        self.assertIn("guidance", feedback["rejected"][0])
        self.assertIn("不要重复提议", feedback["next"])

    def test_fully_invalid_batch_still_raises_without_recording(self):
        with self.assertRaises(ProposalError):
            record(
                self.engine,
                self.state,
                "咳嗽",
                [
                    fact("allergies", None, "没有过敏", "absent"),
                    fact("medications_taken", None, "没吃药", "absent"),
                ],
            )
        self.assertEqual(self.state.facts, {})


class SpilloverSignalTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_incidental_mention_is_flagged_but_counts_as_recorded(self):
        self.state.active_question = "severity"
        record(
            self.engine,
            self.state,
            "不适程度3分，能正常活动",
            [
                fact("severity", 3, "不适程度3分"),
                fact("associated_symptoms", "能正常活动", "能正常活动"),
            ],
        )
        context = self.engine._context(self.state, "不适程度3分，能正常活动")
        self.assertEqual(context["incidentally_recorded"], ["associated_symptoms"])
        # Option (a): the fact still counts as recorded; the signal is
        # advisory only and does not re-gate the summary.
        self.assertNotIn("associated_symptoms", context["missing_required"])

    def test_formal_answer_is_not_flagged(self):
        self.state.active_question = "associated_symptoms"
        record(
            self.engine,
            self.state,
            "还有喉咙痛，没有其他不适",
            [fact("associated_symptoms", "还有喉咙痛", "还有喉咙痛")],
        )
        context = self.engine._context(self.state, "还有喉咙痛")
        self.assertNotIn("incidentally_recorded", context)

    def test_correction_clears_the_flag(self):
        self.state.active_question = "severity"
        record(
            self.engine,
            self.state,
            "不适程度3分，能正常活动",
            [
                fact("severity", 3, "不适程度3分"),
                fact("associated_symptoms", "能正常活动", "能正常活动"),
            ],
        )
        self.state.active_question = None
        record(
            self.engine,
            self.state,
            "还有喉咙痛",
            [
                fact(
                    "associated_symptoms",
                    "还有喉咙痛",
                    "还有喉咙痛",
                    correction=True,
                )
            ],
            turn="t-correct",
        )
        context = self.engine._context(self.state, "还有喉咙痛")
        self.assertNotIn("incidentally_recorded", context)


class AgeToneTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    def test_adult_self_consult_gets_adapted_age_prompt(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                ],
            ),
            cmd("ask_question", question_id="age"),
        ]
        reply = self.submit("我自己，咳嗽")
        self.assertEqual(reply.question_id, "age")
        self.assertNotIn("婴幼儿", reply.message)
        self.assertIn("32 岁", reply.message)

    def test_child_consult_keeps_catalog_age_prompt(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "child", "替孩子"),
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                ],
            ),
            cmd("ask_question", question_id="age"),
        ]
        reply = self.submit("替孩子咨询，咳嗽")
        self.assertEqual(reply.question_id, "age")
        self.assertIn("月龄", reply.message)

    def test_model_catalog_prompt_is_unchanged(self):
        entry = next(
            item
            for item in self.engine.static_context["question_catalog"]
            if item["id"] == "age"
        )
        self.assertIn("婴幼儿", entry["prompt"])


class SpokenCloseVerificationTests(unittest.TestCase):
    """Task 3 (revised): the shared _record_reply choke point must give
    the adaptive engine the spoken disposition line on a confirmed close,
    exactly once."""

    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    def test_confirmed_close_speaks_the_disposition_once(self):
        facts = [
            fact("consult_for", "self", "我自己"),
            fact("age_years", 32, "32岁"),
            fact("chief_complaint", "腹痛", "腹痛"),
            fact("emergency_screen", None, "没有危险信号", "absent"),
        ]
        known = {f["key"] for f in facts}
        for q in self.engine.content.questions:
            if q.fact_key not in known and self.engine.content.question_applies(
                q, "腹痛"
            ):
                facts.append(fact(q.fact_key, None, "不知道", "unknown"))
        self.backend.commands = [
            cmd("record_facts", facts=facts),
            cmd("review_summary"),
        ]
        reply = self.submit("我自己32岁腹痛，没有危险信号，其它不知道")
        self.assertEqual(reply.kind, "summary")
        final = self.submit("摘要准确", "confirm_summary", reply.action_id)
        self.assertEqual(final.kind, "closed")
        # The administrative tail is preserved, after the spoken line.
        self.assertIn("可以把它提供给接诊医生", final.message)
        line = spoken_close_line(self.state.conclusion)
        self.assertNotEqual(line, "")
        self.assertTrue(final.message.startswith(line))
        self.assertEqual(final.message.count(line), 1)
        # Batch C rework (P-10): self_care home observation is now
        # spoken once, in the framing opener ("在家先观察"), not in
        # the repeated main sentence ("在家观察").
        self.assertTrue(
            "小时内" in line or "在家观察" in line or "在家先观察" in line
        )
        # The already-closed guard reply must not repeat the line.
        guard = self.submit("还有个问题")
        self.assertNotIn(line, guard.message)


class EmergencyVerificationTests(unittest.TestCase):
    """Task 4 (revised): adaptive emergency replies ride the shared
    _emergency_reply, which now names the signals via
    emergency_explanation and adds waiting guidance."""

    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_adaptive_emergency_reply_names_signals_and_guides_waiting(self):
        reply = self.engine.submit(
            self.state.session_id, UserInput("没有抽搐，但是现在叫不醒")
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(len(self.backend.calls), 0)
        self.assertIn("危险信号", reply.message)
        self.assertIn("120", reply.message)
        self.assertIn("保持安静休息", reply.message)
        self.assertIn("不要自行开车", reply.message)
        self.assertIn("不要继续等待本次对话", reply.message)
        triggers = [t for t in self.state.risk.triggers if t]
        self.assertTrue(triggers)
        self.assertTrue(any(t in reply.message for t in triggers))


class PromptGuidanceTests(unittest.TestCase):
    def test_wave2_guidance_present_in_prompt(self):
        engine, _, _ = make_engine()
        prompt = engine.prompt
        # (a) controlled cross-turn quotes for corrections
        self.assertIn("可引用该事实原始记录所在轮次的患者原话", prompt)
        # (b/c) question strategy and missing_required as a floor, not a form
        self.assertIn("missing_required 是底线清单，不是填表目标", prompt)
        self.assertIn("incidentally_recorded", prompt)
        self.assertIn("active_question_field_already_recorded", prompt)
        # (d) sex early, pregnancy gate awareness
        self.assertIn("开场尽早记录 sex", prompt)
        self.assertIn("pregnancy_status", prompt)
        # (e) closing: host speaks the conclusion sheet
        self.assertIn("口头播报处置要点与警示信号", prompt)
        # partial recording semantics replaced the atomic-batch wording
        self.assertIn("partial 结果", prompt)
        self.assertNotIn("整批事实必须全部符合契约才会写入", prompt)


if __name__ == "__main__":
    unittest.main()
