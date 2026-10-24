import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.bridging import build_bridging_sentence, lint_bridging_sentence
from medagent.consultation import UserInput
from medagent.domain import (
    AgentReply,
    Assertion,
    Fact,
    Phase,
    Source,
)
from medagent.runtime import build_runtime
from medagent.knowledge import ContentRepository
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.events import EventStore
from medagent.reasoner import OrderedQuestionRanker


def fact(key, value, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t0",
    )


class ScriptBackend:
    name = "script"

    def __init__(self):
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages.copy())
        raise RuntimeError("not used in bridging tests")


class BridgingBuilderTests(unittest.TestCase):
    def test_entry_template_anchors_on_chief_complaint(self):
        facts = {"chief_complaint": fact("chief_complaint", "肚子痛")}
        sentence, kind = build_bridging_sentence(
            facts, "gi_details", "腹痛在哪个位置？", [], 0
        )
        self.assertEqual(kind, "entry")
        self.assertEqual(sentence, "你说肚子痛，再细问一下这方面的情况：")

    def test_entry_requires_unrecorded_detail_key(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "肚子痛"),
            "gi_details": fact("gi_details", "左下腹"),
        }
        sentence, kind = build_bridging_sentence(
            facts, "gi_details", "腹痛在哪个位置？", [], 0
        )
        # Already recorded: falls through to the generic anchor, not entry.
        self.assertEqual(kind, "anchor")
        self.assertIn("肚子痛", sentence)

    def test_repeat_acknowledges_the_earlier_ask(self):
        original = "腹痛在哪个位置？"
        facts = {"gi_details": fact("gi_details", "左下腹")}
        sentence, kind = build_bridging_sentence(
            facts, "gi_details", original, ["你说肚子痛，再问一句：" + original], 3
        )
        self.assertEqual(kind, "repeat")
        self.assertIn("之前问过一次", sentence)

    def test_repeat_matches_unbridged_prior_text_too(self):
        original = "腹痛在哪个位置？"
        facts = {"gi_details": fact("gi_details", "左下腹")}
        sentence, kind = build_bridging_sentence(
            facts, "gi_details", original, [original], 3
        )
        self.assertEqual(kind, "repeat")

    def test_restored_unanswered_question_is_not_a_repeat(self):
        # The opening question restored after an interruption was never
        # answered: no fact recorded, so no "asked before" claim.
        original = "你好，这次是你自己还是替家人咨询？"
        sentence, kind = build_bridging_sentence({}, "consult_for", original, [original], 0)
        self.assertNotEqual(kind, "repeat")
        self.assertNotIn("之前问过", sentence or "")

    def test_denial_unknown_is_never_rendered_as_no_allergy(self):
        facts = {"allergies": fact("allergies", "没注意过", Assertion.UNKNOWN)}
        sentence, kind = build_bridging_sentence(
            facts, "allergies", "有没有已知的药物或食物过敏？", [], 0
        )
        self.assertEqual(kind, "denial")
        self.assertIn("还没注意过", sentence)
        self.assertNotIn("你没有", sentence)

    def test_denial_absent_names_the_denied_item(self):
        facts = {"allergies": fact("allergies", "青霉素", Assertion.ABSENT)}
        sentence, kind = build_bridging_sentence(
            facts, "allergies", "有没有已知的药物或食物过敏？", [], 0
        )
        self.assertEqual(kind, "denial")
        self.assertIn("没有青霉素", sentence)

    def test_anchor_rotation_uses_recorded_facts(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "咳嗽"),
            "onset": fact("onset", "两天前"),
        }
        first, _ = build_bridging_sentence(facts, "severity", "严重吗？", [], 0)
        second, _ = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertIn("咳嗽", first)
        self.assertIn("两天前", second)
        self.assertNotEqual(first, second)

    def test_severity_digits_gain_the_unit(self):
        facts = {"severity": fact("severity", 7)}
        sentence, kind = build_bridging_sentence(facts, "trajectory", "加重了吗？", [], 0)
        self.assertEqual(kind, "anchor")
        self.assertIn("7分", sentence)

    def test_sex_and_age_never_anchor(self):
        facts = {
            "sex": fact("sex", "男"),
            "age_years": fact("age_years", 32),
        }
        sentence, kind = build_bridging_sentence(facts, "severity", "严重吗？", [], 0)
        self.assertEqual((sentence, kind), (None, "no_anchor"))

    def test_long_fact_values_are_truncated(self):
        facts = {"chief_complaint": fact("chief_complaint", "肚子痛" * 20)}
        sentence, kind = build_bridging_sentence(facts, "gi_details", "哪里痛？", [], 0)
        self.assertEqual(kind, "entry")
        self.assertNotIn("肚子痛" * 20, sentence)

    def test_banned_phrase_in_fact_value_drops_the_whole_sentence(self):
        facts = {"chief_complaint": fact("chief_complaint", "医生说确诊肺炎")}
        sentence, kind = build_bridging_sentence(facts, "gi_details", "哪里痛？", [], 0)
        self.assertEqual((sentence, kind), (None, "lint_dropped"))

    def test_clarify_prohibited_word_in_fact_value_drops_the_sentence(self):
        facts = {"chief_complaint": fact("chief_complaint", "吃了 5 毫克的药")}
        sentence, kind = build_bridging_sentence(facts, "gi_details", "哪里痛？", [], 0)
        self.assertEqual((sentence, kind), (None, "lint_dropped"))

    def test_self_bridged_model_question_is_left_alone(self):
        sentence, kind = build_bridging_sentence(
            {"chief_complaint": fact("chief_complaint", "肚子痛")},
            "gi_details",
            "你提到最近总觉得累，想确认一下体重变化？",
            [],
            0,
        )
        self.assertEqual((sentence, kind), (None, "self_bridged"))

    def test_no_anchor_without_recorded_facts(self):
        sentence, kind = build_bridging_sentence({}, "onset", "什么时候开始？", [], 0)
        self.assertEqual((sentence, kind), (None, "no_anchor"))

    def test_lint_layers_cover_both_lists(self):
        self.assertFalse(lint_bridging_sentence("你说没事，再问一句："))
        self.assertFalse(lint_bridging_sentence("你说服用了药，再问一句："))
        self.assertTrue(lint_bridging_sentence("你说咳嗽，再问一句："))


class BridgingRecordReplyTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_deterministic_engine_prefixes_next_question(self):
        state, _ = self.engine.create_session()
        self.engine.submit(state.session_id, UserInput("我自己", "self"))
        self.engine.submit(state.session_id, UserInput("男", "male"))
        self.engine.submit(state.session_id, UserInput("32岁"))
        reply = self.engine.submit(state.session_id, UserInput("咳嗽发烧两天"))
        self.assertEqual(reply.kind, "question")
        trace = reply.developer_trace or {}
        self.assertTrue(trace.get("bridging", {}).get("applied"))
        prompt = self.engine.content.question(reply.question_id).prompt
        self.assertTrue(reply.message.endswith(prompt))
        self.assertIn("咳嗽发烧两天", reply.message)

    def test_non_question_kind_is_never_bridged(self):
        state, _ = self.engine.create_session()
        reply = AgentReply("一段摘要", state.phase, kind="summary")
        self.engine._record_reply(state, reply, "t1", "test")
        self.assertEqual(reply.message, "一段摘要")
        self.assertNotIn("bridging", reply.developer_trace or {})

    def test_same_reply_object_is_never_bridged_twice(self):
        state, _ = self.engine.create_session()
        state.facts["chief_complaint"] = fact("chief_complaint", "肚子痛")
        reply = AgentReply("哪里痛？", state.phase, kind="question", question_id="gi_details")
        self.engine._apply_bridging(state, reply)
        first = reply.message
        self.engine._apply_bridging(state, reply)
        self.assertEqual(reply.message, first)
        self.assertEqual(first.count("再细问一下"), 1)


class BridgingAdaptiveEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=ScriptBackend(),
        )

    def test_adaptive_engine_question_gets_entry_bridge(self):
        state, _ = self.engine.create_session()
        state.facts["chief_complaint"] = fact("chief_complaint", "肚子痛")
        reply = AgentReply(
            "腹痛在哪个位置？", state.phase, kind="question", question_id="gi_details"
        )
        self.engine._record_reply(state, reply, "t1", "test")
        self.assertEqual(
            reply.message, "你说肚子痛，再细问一下这方面的情况：腹痛在哪个位置？"
        )
        self.assertEqual(reply.developer_trace["bridging"]["kind"], "entry")


class BridgingDefectFixTests(unittest.TestCase):
    """run7 defects 1-4, pinned as regressions."""

    def test_defect1_nishuo_opener_is_self_bridged(self):
        # run7: 16 stacked sentences, all from the missing "你说" opener.
        for opener in (
            "你说发烧咳嗽两天了，为了确认筛查适用，想先确认一下：请问是男是女？",
            "你刚才说头痛两天了，为了确认进展，想先确认一下：什么时候开始的？",
            "你提到过一次尿路感染，为了确认复发，想先确认一下：这次有尿痛吗？",
        ):
            sentence, kind = build_bridging_sentence(
                {"chief_complaint": fact("chief_complaint", "头痛")},
                "severity",
                opener,
                [],
                0,
            )
            self.assertEqual((sentence, kind), (None, "self_bridged"))

    def test_defect2_medication_slot_verb_is_deduped(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "头痛"),
            "medications_taken": fact("medications_taken", "自己吃过几天益生菌"),
        }
        sentence, kind = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertEqual(kind, "anchor")
        self.assertIn("用过几天益生菌", sentence)
        self.assertNotIn("用过自己吃过", sentence)

    def test_defect2_onset_slot_start_is_not_doubled(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "头痛"),
            "onset": fact("onset", "一天半前开始"),
        }
        sentence, kind = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertEqual(kind, "anchor")
        self.assertEqual(sentence.count("开始"), 1)
        self.assertIn("从一天半前开始到现在", sentence)

    def test_defect3_truncation_stops_at_phrase_boundary(self):
        # The 30-char cut used to land inside "头有点晕沉" ("...、头").
        value = "低烧、咳嗽、流鼻涕、喉咙痛、全身酸痛、乏力、头有点晕沉、恶心想吐"
        self.assertGreater(len(value), 30)
        sentence, kind = build_bridging_sentence(
            {
                "chief_complaint": fact("chief_complaint", "头痛"),
                "associated_symptoms": fact("associated_symptoms", value),
            },
            "severity",
            "严重吗？",
            [],
            3,
        )
        self.assertEqual(kind, "anchor")
        self.assertIn("头有点晕沉", sentence)
        self.assertNotIn("、恶心，", sentence)
        self.assertNotIn("头，", sentence)

    def test_defect4_detail_facts_join_the_anchor_pool(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "头痛"),
            "head_details": fact("head_details", "怕光、脖子僵"),
        }
        sentence, kind = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertEqual(kind, "anchor")
        self.assertIn("怕光", sentence)

    def test_defect4_recent_exposures_join_the_anchor_pool(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "皮疹"),
            "recent_exposures": fact("recent_exposures", "第一次吃虾"),
        }
        sentence, kind = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertEqual(kind, "anchor")
        self.assertIn("第一次吃虾", sentence)

    def test_defect4_detail_never_anchors_its_own_question(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "头痛"),
            "head_details": fact("head_details", "怕光、脖子僵"),
        }
        sentence, kind = build_bridging_sentence(facts, "head_details", "怕光吗？", [], 1)
        self.assertEqual(kind, "anchor")
        self.assertNotIn("怕光", sentence)


class BridgingEngineTriggerTests(unittest.TestCase):
    """repeat / denial trigger paths through the _record_reply choke point
    (run7 never exercised them live, so they are pinned here, not deleted)."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_repeat_triggered_through_apply_bridging(self):
        state, _ = self.engine.create_session()
        state.facts["gi_details"] = fact("gi_details", "左下腹")
        original = "腹痛在哪个位置？"
        state.transcript.append(
            {"role": "assistant", "content": "你说肚子痛，再问一句：" + original}
        )
        reply = AgentReply(original, state.phase, kind="question", question_id="gi_details")
        self.engine._apply_bridging(state, reply)
        self.assertIn("之前问过一次", reply.message)
        self.assertEqual(reply.developer_trace["bridging"]["kind"], "repeat")

    def test_denial_absent_triggered_through_apply_bridging(self):
        state, _ = self.engine.create_session()
        state.facts["allergies"] = fact("allergies", "青霉素", Assertion.ABSENT)
        reply = AgentReply(
            "有没有已知的药物或食物过敏？",
            state.phase,
            kind="question",
            question_id="allergies",
        )
        self.engine._apply_bridging(state, reply)
        self.assertIn("没有青霉素", reply.message)
        self.assertEqual(reply.developer_trace["bridging"]["kind"], "denial")

    def test_denial_unknown_triggered_through_apply_bridging(self):
        state, _ = self.engine.create_session()
        state.facts["allergies"] = fact("allergies", "没注意过", Assertion.UNKNOWN)
        reply = AgentReply(
            "有没有已知的药物或食物过敏？",
            state.phase,
            kind="question",
            question_id="allergies",
        )
        self.engine._apply_bridging(state, reply)
        self.assertIn("还没注意过", reply.message)
        self.assertNotIn("你没有", reply.message)

    def test_self_bridged_model_question_is_never_stacked(self):
        state, _ = self.engine.create_session()
        state.facts["chief_complaint"] = fact("chief_complaint", "发烧咳嗽")
        original = "你说发烧咳嗽两天了，为了确认后续筛查适用，想先确认一下：请问是男是女？"
        reply = AgentReply(original, state.phase, kind="question", question_id="sex")
        self.engine._apply_bridging(state, reply)
        self.assertEqual(reply.message, original)
        self.assertEqual(reply.developer_trace["bridging"]["reason"], "self_bridged")


class ModelBridgingValidationTests(unittest.TestCase):
    """B route: ask_patient / clarify must self-bridge, or be repaired."""

    def setUp(self):
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=ScriptBackend(),
        )
        self.state, _ = self.engine.create_session()

    def _ask_patient(self, topic, question):
        return self.engine._execute(
            self.state,
            {
                "tool": "ask_patient",
                "arguments": {
                    "topic": topic,
                    "question": question,
                    "selection_mode": "text",
                    "options": [],
                    "allow_other": False,
                },
            },
            "患者原话",
            "t1",
        )

    def _clarify(self, topic, message):
        return self.engine._execute(
            self.state,
            {"tool": "clarify", "arguments": {"topic": topic, "message": message}},
            "患者原话",
            "t1",
        )

    def test_plain_ask_patient_is_rejected_when_anchorable_fact_exists(self):
        from medagent.agent_tools import ProposalError

        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        with self.assertRaises(ProposalError):
            self._ask_patient("onset", "什么时候开始的？")

    def test_bridged_ask_patient_passes_unchanged(self):
        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        question = "你说头痛两天了，为了确认进展，想先确认一下：什么时候开始的？"
        reply = self._ask_patient("onset", question)
        self.assertEqual(reply.message, question)

    def test_ask_patient_without_bridge_but_with_why_is_rejected(self):
        from medagent.agent_tools import ProposalError

        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        with self.assertRaises(ProposalError):
            self._ask_patient("onset", "想确认一下进展，什么时候开始的？")

    def test_plain_ask_patient_passes_with_no_anchorable_fact(self):
        # Opening rounds have nothing to anchor on: no forced bridging.
        reply = self._ask_patient("chief_complaint", "哪里不舒服？")
        self.assertEqual(reply.message, "哪里不舒服？")

    def test_plain_clarify_is_rejected_when_anchorable_fact_exists(self):
        from medagent.agent_tools import ProposalError

        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        with self.assertRaises(ProposalError):
            self._clarify("onset", "疼痛是一直持续还是间断？")

    def test_bridged_clarify_passes_unchanged(self):
        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        message = "你说头痛两天了，为了确认进展，想确认一下：疼痛是一直持续还是间断？"
        reply = self._clarify("onset", message)
        self.assertEqual(reply.message, message)

    def test_prohibited_phrase_still_rejected_before_bridge_check(self):
        from medagent.adaptive_questions import AdaptiveQuestionError

        self.state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        with self.assertRaises(AdaptiveQuestionError):
            self._ask_patient(
                "onset", "你说头痛两天了，为了确认进展，想确认一下：是不是已经确诊了？"
            )


if __name__ == "__main__":
    unittest.main()
