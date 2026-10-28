"""Batch C BURDEN + POLISH worker tests (13 items).

Each test names the issue it covers. Scope notes:
- knowledge.py is intentionally untouched: free-text-to-option mapping
  does not live there (deterministic mapping returns raw text in
  interpreter.py; model mapping is guarded in agent.py record_facts).
- P-10's conclusion-sentence duplication lives in conclusion.py, which
  is outside this worker's file attribution; only its bridging parts
  (punctuation / verb doubling) are tested here.
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError
from medagent.bridging import build_bridging_sentence
from medagent.consultation import UserInput  # noqa: F401  (parity import)
from medagent.domain import (
    Assertion,
    Fact,
    Phase,
    Source,
)
from medagent.events import EventStore
from medagent.interpreter import Interpretation
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


def fact(key, value, assertion=Assertion.PRESENT, evidence=None):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t0",
        evidence=evidence,
    )


class ScriptBackend:
    name = "script"

    def decide(self, session_id, messages):
        raise RuntimeError("not used in batch C burden tests")


def make_adaptive():
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=ScriptBackend(),
    )
    state, _ = engine.create_session()
    return engine, state


def ask_patient_cmd(topic, question, mode="text", options=None):
    return {
        "tool": "ask_patient",
        "arguments": {
            "topic": topic,
            "question": question,
            "selection_mode": mode,
            "options": options or [],
            "allow_other": False,
        },
    }


def record_cmd(facts):
    return {"tool": "record_facts", "arguments": {"facts": facts}}


def proposal(key, value, evidence, assertion="present"):
    return {
        "key": key,
        "value": value,
        "assertion": assertion,
        "evidence": evidence,
    }


def bridged(question_body):
    """A B-route question: bridging opener + why-ask half-sentence."""
    return f"你说此前的情况，为了确认进展，想先确认一下：{question_body}"


class BurdenRepeatGateTests(unittest.TestCase):
    """D-01 / P-02: asked + recorded topics are not re-asked verbatim."""

    def test_d01_asked_and_recorded_topic_rejected_via_ask_patient(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "风团发痒")
        state.facts["trajectory"] = fact(
            "trajectory", None, Assertion.UNKNOWN, "风团此起彼伏"
        )
        state.asked_questions.append("trajectory")
        with self.assertRaises(ProposalError) as caught:
            engine._execute(
                state,
                ask_patient_cmd("trajectory", bridged("总体是加重、减轻，还是变化不大？")),
                "风团此起彼伏，旧的消了新的又起。",
                "t1",
            )
        self.assertIn("不得原样重问", str(caught.exception))

    def test_p02_asked_and_recorded_cough_topic_rejected(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "咳嗽")
        state.facts["resp_cough_details"] = fact(
            "resp_cough_details", None, Assertion.UNKNOWN, "干咳还是有痰没分清楚"
        )
        state.asked_questions.append("resp_cough_details")
        with self.assertRaises(ProposalError):
            engine._execute(
                state,
                ask_patient_cmd(
                    "resp_cough_details",
                    bridged("咳嗽时有没有痰音、咳出痰或喉咙有呼噜声？"),
                ),
                "刚才说过了，就是咳嗽。",
                "t1",
            )

    def test_d01_clarify_cap_bounds_repeat_asks_at_two(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "风团发痒")
        state.followup_counts["trajectory"] = 2
        with self.assertRaises(ProposalError):
            engine._execute(
                state,
                {
                    "tool": "clarify",
                    "arguments": {
                        "topic": "trajectory",
                        "message": bridged("总体是在加重还是减轻？"),
                    },
                },
                "风团此起彼伏。",
                "t1",
            )

    def test_p02_followup_denial_lands_on_the_same_fact_key(self):
        """A clarify answer's denial lands on its own key, never null."""
        host = build_runtime()
        context = host.start()
        try:
            engine = context.services.require("consultation")
            state, _ = engine.create_session()
            engine._record_fact(
                state,
                "resp_cough_details",
                Interpretation("没有咳血，也没有胸口疼", Assertion.PRESENT),
                "t1",
                "没有咳血，也没有胸口疼",
            )
            recorded = state.facts["resp_cough_details"]
            self.assertIsNotNone(recorded.value)
            self.assertIn("没有咳血", recorded.value)
        finally:
            host.stop()


class BurdenTrajectoryMappingTests(unittest.TestCase):
    """D-02: fluctuation text must not map to stable."""

    def test_d02_fluctuation_evidence_rejected_as_stable(self):
        engine, state = make_adaptive()
        text = "刚才说过了，风团此起彼伏，旧的消了新的又起。"
        result = engine._execute(
            state,
            record_cmd(
                [
                    proposal(
                        "trajectory",
                        "stable",
                        "风团此起彼伏，旧的消了新的又起",
                    )
                ]
            ),
            text,
            "t1",
        )
        self.assertFalse(result["ok"])
        self.assertNotIn("trajectory", state.facts)
        rejected = {item["key"]: item for item in result["rejected"]}
        self.assertIn("波动交替", rejected["trajectory"]["reason"])

    def test_d02_genuine_stable_evidence_still_records(self):
        engine, state = make_adaptive()
        text = "变化不大，一直这样。"
        result = engine._execute(
            state,
            record_cmd([proposal("trajectory", "stable", "变化不大")]),
            text,
            "t1",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(state.get("trajectory"), "stable")


class BurdenScreeningTests(unittest.TestCase):
    """O-06: screening questions carry the list; answers file as screening."""

    def test_o06_screening_question_gains_the_registered_list(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "脖子肩膀僵得厉害")
        reply = engine._execute(
            state,
            ask_patient_cmd(
                "emergency_screen",
                bridged("此刻有没有以下任一情况？"),
            ),
            "脖子肩膀僵得厉害。",
            "t1",
        )
        for item in ("叫不醒", "抽搐", "嘴唇发紫", "严重呼吸困难", "大出血"):
            self.assertIn(item, reply.message)

    def test_o06_screening_answer_files_as_screening_fact(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "脖子肩膀僵得厉害")
        state.active_question = "emergency_screen"
        text = "没有胳膊无力，没有头晕呕吐，没有受过外伤，也没有发烧。"
        result = engine._execute(
            state,
            record_cmd(
                [
                    proposal(
                        "associated_symptoms",
                        "筛查回答已记录",
                        "没有胳膊无力",
                    )
                ]
            ),
            text,
            "t1",
        )
        self.assertTrue(result["ok"])
        screen = state.facts["emergency_screen"]
        self.assertEqual(screen.assertion, Assertion.ABSENT)
        self.assertIsNone(screen.value)

    def test_o06_completed_screen_is_not_reasked_verbatim(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "脖子肩膀僵得厉害")
        state.facts["emergency_screen"] = fact(
            "emergency_screen", None, Assertion.ABSENT, "都没有"
        )
        state.asked_questions.append("emergency_screen")
        with self.assertRaises(ProposalError):
            engine._execute(
                state,
                ask_patient_cmd(
                    "emergency_screen",
                    bridged("此刻有没有以下任一情况？"),
                ),
                "刚才说过了，都没有。",
                "t1",
            )
        with self.assertRaises(ProposalError):
            engine._execute(
                state,
                {
                    "tool": "clarify",
                    "arguments": {
                        "topic": "emergency_screen",
                        "message": bridged("此刻有没有以下任一情况？"),
                    },
                },
                "刚才说过了，都没有。",
                "t1",
            )

    def test_o06_screening_supplement_with_new_items_stays_possible(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "脖子肩膀僵得厉害")
        state.facts["emergency_screen"] = fact(
            "emergency_screen", None, Assertion.ABSENT, "都没有"
        )
        reply = engine._execute(
            state,
            {
                "tool": "clarify",
                "arguments": {
                    "topic": "emergency_screen",
                    "message": bridged("补问一下新增项目：有没有大出血？"),
                },
            },
            "刚才的否认里没提大出血。",
            "t1",
        )
        self.assertEqual(reply.question_id, "emergency_screen")
        self.assertIn("大出血", reply.message)


class BurdenMergeTests(unittest.TestCase):
    """O-12: later writebacks merge, never drop denials/unknowns."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_o12_denials_and_unknown_survive_a_short_writeback(self):
        state, _ = self.engine.create_session()
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation(
                "没有受过外伤、腿没有完全没劲、有没有漏尿没注意过",
                Assertion.PRESENT,
            ),
            "t1",
            "没有受过外伤",
        )
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation(
                "小便解不干净，大便费劲，屁股那块发麻，没有发烧",
                Assertion.PRESENT,
            ),
            "t2",
            "小便解不干净",
        )
        value = state.facts["back_details"].value
        self.assertIn("小便解不干净", value)
        self.assertIn("没有受过外伤", value)
        self.assertIn("腿没有完全没劲", value)
        self.assertIn("有没有漏尿没注意过", value)

    def test_o12_affirmed_term_supersedes_its_denial(self):
        state, _ = self.engine.create_session()
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation("没有发烧", Assertion.PRESENT),
            "t1",
            "没有发烧",
        )
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation("发烧38度", Assertion.PRESENT),
            "t2",
            "发烧38度",
        )
        self.assertNotIn("没有发烧", state.facts["back_details"].value)

    def test_o12_explicit_correction_replaces_without_merge(self):
        state, _ = self.engine.create_session()
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation("没有受过外伤", Assertion.PRESENT),
            "t1",
            "没有受过外伤",
        )
        self.engine._record_fact(
            state,
            "back_details",
            Interpretation("腰痛", Assertion.PRESENT),
            "t2",
            "腰痛",
            merge=False,
        )
        self.assertEqual(state.facts["back_details"].value, "腰痛")


class BurdenCompositeFilterTests(unittest.TestCase):
    """Y-10: composite questions drop already-denied items."""

    def test_y10_fixed_composite_drops_denied_fever_item(self):
        engine, state = make_adaptive()
        state.facts["associated_symptoms"] = fact(
            "associated_symptoms",
            "没有视力模糊，没有发烧，没有整个眼睛红肿，也没有眼外伤",
        )
        question = engine.content.question("skin_details")
        reply = engine._ask(state, question, "测试")
        self.assertNotIn("发热", reply.message)
        self.assertIn("水疱", reply.message)

    def test_y10_model_composite_keeps_only_new_items(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "咳嗽")
        state.facts["resp_cough_details"] = fact(
            "resp_cough_details", "没有咳血，也没有胸口疼"
        )
        reply = engine._execute(
            state,
            ask_patient_cmd(
                "resp_cough_details",
                bridged("有没有咳血、胸口疼，或者晚上咳得更厉害？"),
            ),
            "咳嗽前面已经说了。",
            "t1",
        )
        self.assertNotIn("咳血", reply.message)
        self.assertNotIn("胸口疼", reply.message)
        self.assertIn("晚上咳得更厉害", reply.message)


class BurdenOptionVisibilityTests(unittest.TestCase):
    """K-04: multi-option questions must show their options somewhere."""

    def _options(self):
        labels = [
            "牙龈肿了",
            "脸肿了",
            "发热",
            "咬东西时更疼",
            "冷热刺激后疼痛持续不退",
            "以上都没有",
        ]
        return [
            {"id": f"opt{i}", "label": label, "response_text": label}
            for i, label in enumerate(labels)
        ]

    def test_k04_dangling_reference_lists_options_in_text(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "牙疼")
        reply = engine._execute(
            state,
            ask_patient_cmd(
                "associated_symptoms",
                bridged("请问除了牙疼，还有没有以下这些情况？"),
                mode="multiple",
                options=self._options(),
            ),
            "牙疼。",
            "t1",
        )
        for label in ("牙龈肿了", "脸肿了", "发热", "以上都没有"):
            self.assertIn(label, reply.message)

    def test_k04_already_listed_option_is_not_duplicated(self):
        engine, state = make_adaptive()
        state.facts["chief_complaint"] = fact("chief_complaint", "牙疼")
        reply = engine._execute(
            state,
            ask_patient_cmd(
                "associated_symptoms",
                bridged("请问除了牙疼，还有没有以下这些情况：牙龈肿了？"),
                mode="multiple",
                options=self._options(),
            ),
            "牙疼。",
            "t1",
        )
        self.assertNotIn("（选项：", reply.message)


class BurdenCompletenessTests(unittest.TestCase):
    """G-07: summary draft waits for chief-complaint key items."""

    def _full_state(self, engine, state, gi_value):
        for key, value, assertion in [
            ("consult_for", "self", Assertion.PRESENT),
            ("sex", "male", Assertion.PRESENT),
            ("age_years", 32, Assertion.PRESENT),
            ("chief_complaint", "便秘好几天", Assertion.PRESENT),
            ("emergency_screen", None, Assertion.ABSENT),
            # P3 off-pathway warning screen (constipation triggers no
            # pathway; the emergency screen subsumes the other three).
            ("warning_剧痛", None, Assertion.ABSENT),
            ("warning_持续加重", None, Assertion.ABSENT),
            ("warning_功能骤降", None, Assertion.ABSENT),
            ("onset", "三天前", Assertion.PRESENT),
            ("trajectory", "stable", Assertion.PRESENT),
            ("severity", 3, Assertion.PRESENT),
            ("associated_symptoms", "肚子胀", Assertion.PRESENT),
            ("gi_details", gi_value, Assertion.PRESENT),
            ("medications_taken", "没用药", Assertion.PRESENT),
            ("allergies", "没有过敏", Assertion.PRESENT),
            ("relevant_history", "没有病史", Assertion.PRESENT),
            ("patient_goal", "想通便", Assertion.PRESENT),
        ]:
            state.facts[key] = fact(key, value, assertion)

    def test_g07_missing_effort_and_weight_block_first_draft(self):
        engine, state = make_adaptive()
        self._full_state(engine, state, "大便干硬，三四天一次")
        with self.assertRaises(ProposalError) as caught:
            engine._execute(
                state,
                {"tool": "review_summary", "arguments": {}},
                "请核对摘要",
                "t1",
            )
        message = str(caught.exception)
        self.assertIn("排便费力程度", message)
        self.assertIn("体重变化", message)

    def test_g07_covered_items_pass_to_summary(self):
        engine, state = make_adaptive()
        self._full_state(engine, state, "排便费力，体重没变")
        reply = engine._execute(
            state,
            {"tool": "review_summary", "arguments": {}},
            "请核对摘要",
            "t1",
        )
        self.assertEqual(reply.phase, Phase.SUMMARY)

    def test_g07_non_constipation_chief_has_no_gap(self):
        engine, state = make_adaptive()
        self._full_state(engine, state, "大便干硬，三四天一次")
        state.facts["chief_complaint"] = fact("chief_complaint", "头痛")
        self.assertEqual(engine._completeness_gaps(state), [])


class PolishBridgingTests(unittest.TestCase):
    """N-09 / S-07 / O-08 / P-05 / P-10 bridging polish."""

    def test_n09_overlapping_fragments_never_echo_twice(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "一边胳膊没劲，说话也不清楚，头晕"),
            "associated_symptoms": fact("associated_symptoms", "说话不清楚，头晕"),
        }
        for index in range(6):
            sentence, _ = build_bridging_sentence(
                facts, "severity", "严重吗？", [], index
            )
            if sentence is None:
                continue
            self.assertLessEqual(sentence.count("头晕"), 1)
            self.assertNotIn("说话不清楚，头晕", sentence)

    def test_n09_partial_overlap_keeps_only_new_fragments(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "头痛"),
            "associated_symptoms": fact("associated_symptoms", "头痛、恶心"),
        }
        sentence, kind = build_bridging_sentence(
            facts, "severity", "严重吗？", [], 1
        )
        self.assertEqual(kind, "anchor")
        self.assertIn("恶心", sentence)
        self.assertEqual(sentence.count("头痛"), 1)

    def test_s07_sentence_final_period_gains_no_comma(self):
        facts = {
            "chief_complaint": fact(
                "chief_complaint", "我大便时肛门疼，还带点血，坐着也难受。"
            )
        }
        sentence, _ = build_bridging_sentence(facts, "severity", "严重吗？", [], 0)
        self.assertNotIn("。，", sentence)
        self.assertIn("坐着也难受，再问一句", sentence)

    def test_o08_medication_denial_never_gains_affirming_verb(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "肛门疼"),
            "medications_taken": fact(
                "medications_taken", "没有用过药，热敷后稍好一点"
            ),
        }
        seen_denial = False
        for index in range(6):
            sentence, _ = build_bridging_sentence(
                facts, "severity", "严重吗？", [], index
            )
            if sentence is None:
                continue
            self.assertNotIn("用过没有", sentence)
            self.assertNotIn("用过没", sentence)
            if "没有用过药" in sentence:
                seen_denial = True
                self.assertIn("你说没有用过药", sentence)
        self.assertTrue(seen_denial)

    def test_p05_completed_onset_loses_its_extra_verb(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "发烧咳嗽"),
            "onset": fact("onset", "两天了"),
        }
        sentence, _ = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertIn("从两天开始到现在", sentence)
        self.assertNotIn("从两天了开始到现在", sentence)

    def test_p10_onset_start_is_not_doubled(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "红疹子"),
            "onset": fact("onset", "昨天开始的"),
        }
        sentence, _ = build_bridging_sentence(facts, "severity", "严重吗？", [], 1)
        self.assertIn("从昨天开始到现在", sentence)
        self.assertNotIn("开始的开始", sentence)

    def test_p10_punctuation_and_verb_normalization_compose(self):
        facts = {
            "chief_complaint": fact("chief_complaint", "身上起了红疹子。"),
            "onset": fact("onset", "昨天开始的"),
        }
        for index in range(4):
            sentence, _ = build_bridging_sentence(
                facts, "severity", "严重吗？", [], index
            )
            if sentence is None:
                continue
            self.assertNotIn("。，", sentence)
            self.assertNotIn("开始的开始", sentence)


if __name__ == "__main__":
    unittest.main()
