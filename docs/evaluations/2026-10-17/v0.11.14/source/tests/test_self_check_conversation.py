"""Ordinary conversation must not be committed as a physical observation."""
from copy import deepcopy
import unittest

from medagent.consultation import StaleActionError, UserInput
from medagent.extensions.builtin import observation
from medagent.interpreter import ObservationInputError
from tests.test_agent import cmd, fact
from tests.test_calculation_supplements import Backend
from tests import test_patient_calculations as calculation_fixture


class SelfCheckConversationTests(unittest.TestCase):
    def setUp(self):
        fixture = calculation_fixture.PatientCalculationTests()
        fixture.setUp()
        self.engine, self.state = fixture.engine, fixture.state
        self.backend = self.engine.backend = Backend()

    def submit(self, text, choice=None, action=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, action)
        )

    def offer(self, task="body_location"):
        chief = "头疼" if task == "body_location" else "发热"
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("chief_complaint", chief, chief),
                    fact("emergency_screen", None, "上述危险信号都没有", "absent"),
                ],
            ),
            cmd("show_self_check", task_id=task),
        ]
        return self.submit(f"我自己32岁，{chief}；上述危险信号都没有。")

    def medicine(self):
        return cmd(
            "record_facts", facts=[fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲")]
        )

    def test_medication_supplement_is_not_a_location_and_original_task_can_resume(self):
        offered = self.offer()
        self.backend.commands = [
            self.medicine(),
            cmd("ask_question", question_id="onset"),
        ]
        reply = self.submit("昨晚用过药物甲。", action=offered.action_id)
        self.assertNotIn("symptom_location", self.state.facts)
        self.assertEqual(self.state.get("medications_taken"), "昨晚用过药物甲")
        self.assertEqual(reply.question_id, "onset")
        before = len(self.backend.calls)
        choice = next(
            q.value
            for q in reply.quick_replies
            if q.value.startswith("resume_self_check:")
        )
        resumed = self.submit("继续定位", choice, reply.action_id)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(resumed.task["interaction_id"], offered.task["interaction_id"])
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.submit("头/面部", "head", resumed.action_id)
        self.assertEqual(self.state.get("symptom_location"), "头/面部")
        self.assertIsNone(self.state.self_check_conversation)

    def test_model_location_proposal_requires_confirmation_and_keeps_original_source(
        self,
    ):
        offered = self.offer()
        text = "右太阳穴附近疼，昨晚用过药物甲。"
        self.backend.commands = [
            self.medicine(),
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="右太阳穴附近疼",
            ),
        ]
        proposed = self.submit(text, action=offered.action_id)
        candidate = proposed.task["result_proposal"]
        self.assertNotIn("symptom_location", self.state.facts)
        self.assertEqual(
            proposed.task["interaction_id"], offered.task["interaction_id"]
        )
        original_turn = self.state.facts["medications_taken"].turn_id
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.submit("核对并记录", candidate["choice"], proposed.action_id)
        result = self.state.facts["symptom_location"]
        self.assertEqual(
            (result.value, result.evidence, result.turn_id),
            ("头/面部", "右太阳穴附近疼", original_turn),
        )
        self.assertEqual(result.quality.value, "not_assessed")
        self.assertEqual(result.source.value, "patient")
        before = deepcopy(self.state.to_dict())
        events = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.submit("核对并记录", candidate["choice"], proposed.action_id)
        self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )

    def test_manual_choice_replaces_suggestion_without_reusing_its_quote(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="太阳穴附近",
            )
        ]
        proposed = self.submit("太阳穴附近")
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.submit("胸部", "chest", proposed.action_id)
        self.assertEqual(self.state.get("symptom_location"), "胸部")
        self.assertEqual(self.state.facts["symptom_location"].evidence, "胸部")
        self.assertIsNone(self.state.self_check_conversation)

    def test_invalid_quote_or_option_cannot_create_a_candidate_or_fact(self):
        self.offer()
        for option, evidence in [("head", "不存在的原话"), ("invented", "昨晚用过药物甲")]:
            self.backend.commands = [
                cmd(
                    "propose_self_check_result",
                    task_id="body_location",
                    option=option,
                    evidence=evidence,
                ),
                cmd("show_self_check", task_id="body_location"),
            ]
            reply = self.submit("昨晚用过药物甲")
            self.assertNotIn("result_proposal", reply.task)
            self.assertNotIn("symptom_location", self.state.facts)

    def test_temperature_history_supplement_routes_to_model_but_invalid_reading_stays_local(
        self,
    ):
        offered = self.offer("axillary_temperature")
        before = len(self.backend.calls)
        error = self.submit("380℃", action=offered.action_id)
        self.assertTrue(error.input_error)
        self.assertEqual(len(self.backend.calls), before)
        self.backend.commands = [
            self.medicine(),
            cmd("show_self_check", task_id="axillary_temperature"),
        ]
        resumed = self.submit("昨晚用过药物甲", action=error.action_id)
        self.assertEqual(resumed.kind, "self_check")
        self.assertFalse(resumed.input_error)
        self.assertNotIn("temperature_c", self.state.facts)
        self.assertEqual(self.state.get("medications_taken"), "昨晚用过药物甲")
        self.assertEqual(resumed.task["interaction_id"], offered.task["interaction_id"])

    def test_correction_and_emergency_invalidate_suspended_task(self):
        for emergency in [False, True]:
            with self.subTest(emergency=emergency):
                self.setUp()
                self.offer()
                self.backend.commands = [
                    self.medicine(),
                    cmd("ask_question", question_id="onset"),
                ]
                self.submit("昨晚用过药物甲")
                if emergency:
                    reply = self.submit("现在叫不醒了")
                    self.assertEqual(reply.kind, "emergency")
                else:
                    self.backend.commands = [
                        cmd(
                            "record_facts",
                            facts=[
                                fact("chief_complaint", "咳嗽", "咳嗽", correction=True)
                            ],
                        ),
                        cmd("ask_question", question_id="emergency_screen"),
                    ]
                    self.submit("更正，是咳嗽")
                self.assertIsNone(self.state.self_check_conversation)
                self.assertNotIn("symptom_location", self.state.facts)

    def test_free_text_location_parser_no_longer_accepts_unclassified_words(self):
        with self.assertRaises(ObservationInputError):
            observation("昨晚用过药物甲", None, "location")

    def test_age_report_during_temperature_task_updates_age_not_temperature(self):
        self.offer("axillary_temperature")
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 38, "38岁", correction=True)]),
            cmd("show_self_check", task_id="axillary_temperature"),
        ]
        reply = self.submit("更正，我38岁")
        self.assertNotIn("temperature_c", self.state.facts)
        self.assertEqual(self.state.get("age_years"), 38)
        self.assertEqual(reply.task["id"], "axillary_temperature")
        self.assertIsNone(self.state.self_check_conversation)

    def test_other_numeric_narratives_reach_planner_without_becoming_temperature(self):
        for text in ["血糖38.2mmol/L", "体重38.2kg", "发烧38天"]:
            with self.subTest(text=text):
                self.setUp()
                offered = self.offer("axillary_temperature")
                before = len(self.backend.calls)
                self.backend.commands = [
                    cmd("show_self_check", task_id="axillary_temperature")
                ]
                reply = self.submit(text)
                self.assertEqual(len(self.backend.calls), before + 1)
                self.assertIn(text, self.backend.calls[-1][-1]["content"])
                self.assertFalse(reply.input_error)
                self.assertNotIn("temperature_c", self.state.facts)
                self.assertEqual(
                    reply.task["interaction_id"], offered.task["interaction_id"]
                )

    def test_even_a_semantically_wrong_model_suggestion_is_not_a_record(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="昨晚用过药物甲",
            )
        ]
        reply = self.submit("昨晚用过药物甲")
        self.assertIn("result_proposal", reply.task)
        self.assertNotIn("symptom_location", self.state.facts)
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.submit("说不清，跳过", "skip", reply.action_id)
        self.assertEqual(
            self.state.facts["symptom_location"].assertion.value, "unknown"
        )
        self.assertEqual(self.state.facts["symptom_location"].quality.value, "not_done")

    def test_uncertain_location_is_not_proposed_as_a_positive_other_location(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="other",
                evidence="说不清具体部位",
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.submit("说不清具体部位")
        self.assertNotIn("result_proposal", reply.task)
        self.assertNotIn("symptom_location", self.state.facts)

    def test_location_quote_cannot_drop_uncertainty_from_the_same_clause(self):
        for text in ["可能是右太阳穴附近疼", "右太阳穴附近疼，具体位置不清楚", "不确定，右太阳穴附近疼"]:
            with self.subTest(text=text):
                self.setUp()
                self.offer()
                self.backend.commands = [
                    cmd(
                        "propose_self_check_result",
                        task_id="body_location",
                        option="head",
                        evidence="右太阳穴附近疼",
                    ),
                    cmd("show_self_check", task_id="body_location"),
                ]
                reply = self.submit(text)
                self.assertNotIn("result_proposal", reply.task)
                self.assertNotIn("symptom_location", self.state.facts)

    def test_source_scope_keeps_location_details_but_separates_medication_uncertainty(
        self,
    ):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="太阳穴附近疼",
            )
        ]
        reply = self.submit("在右太阳穴附近疼了两天，药名不清楚")
        self.assertEqual(reply.task["result_proposal"]["evidence"], "在右太阳穴附近疼了两天")
        self.assertNotIn("symptom_location", self.state.facts)

    def test_resume_button_without_current_action_is_rejected_before_journaling(self):
        self.offer()
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        reply = self.submit("先说说其他情况")
        choice = next(
            q.value
            for q in reply.quick_replies
            if q.value.startswith("resume_self_check:")
        )
        before = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.submit("继续", choice)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), before
        )

    def test_returning_from_another_subject_keeps_one_continuation_button(self):
        self.offer()
        self.backend.commands = [
            self.medicine(),
            cmd("ask_question", question_id="onset"),
        ]
        reply = self.submit("昨晚用过药物甲")
        frame = deepcopy(self.state.self_check_conversation)
        changed = self.submit("改问我母亲，她65岁，肚子痛。", action=reply.action_id)
        self.assertEqual(changed.kind, "subject_change")
        before = len(self.backend.calls)
        restored = self.submit("继续当前问诊", "resume_current_subject", changed.action_id)
        self.assertEqual(self.state.self_check_conversation, frame)
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(
            len(
                [
                    q
                    for q in restored.quick_replies
                    if q.value.startswith("resume_self_check:")
                ]
            ),
            1,
        )

    def test_separator_only_quote_is_a_controlled_tool_error(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="；",
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.submit("补充；昨晚用药")
        self.assertEqual(reply.kind, "self_check")
        self.assertNotIn("result_proposal", reply.task)
        self.assertNotIn("symptom_location", self.state.facts)


if __name__ == "__main__":
    unittest.main()
