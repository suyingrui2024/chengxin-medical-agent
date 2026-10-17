"""Relationships do not become unique identity, but explicit case changes stay separate."""
from copy import deepcopy
import unittest

from medagent.consultation import StaleActionError, SubjectBoundaryError, UserInput
from medagent.consultation_subject import (
    compatible,
    explicit_switch,
    proposed_profile,
    reference,
    subject_profile,
)
from tests import test_patient_calculations as fixture
from tests import test_calculation_supplements as calculation_fixture
from tests import test_repeated_observations as observation_fixture


def record(
    person="我父亲", age=68, complaint="头疼", category="other_adult", correction=False
):
    return {
        "tool": "record_facts",
        "arguments": {
            "facts": [
                {
                    "key": "consult_for",
                    "value": category,
                    "assertion": "present",
                    "evidence": person,
                    "correction": correction,
                },
                {
                    "key": "age_years",
                    "value": age,
                    "assertion": "present",
                    "evidence": f"{age}岁",
                    "correction": correction,
                },
                {
                    "key": "chief_complaint",
                    "value": complaint,
                    "assertion": "present",
                    "evidence": complaint,
                    "correction": correction,
                },
            ]
        },
    }


class ConsultationSubjectTests(unittest.TestCase):
    def setUp(self):
        helper = fixture.PatientCalculationTests()
        helper.setUp()
        self.engine, self.state = helper.engine, helper.state
        self.backend = calculation_fixture.Backend()
        self.engine.backend = self.backend

    def establish(self, person="我父亲", age=68, complaint="头疼", category="other_adult"):
        self.backend.commands = [
            record(person, age, complaint, category),
            calculation_fixture.ask("emergency_screen"),
        ]
        return self.engine.submit(
            self.state.session_id, UserInput(f"{person}{age}岁，{complaint}。")
        )

    def resume(self, reply):
        return self.engine.submit(
            self.state.session_id,
            UserInput("继续当前问诊", "resume_current_subject", reply.action_id),
        )

    def test_aliases_refinements_and_age_category_are_separate(self):
        self.assertTrue(compatible(reference("爸爸"), reference("父亲")))
        self.assertTrue(compatible(reference("大宝"), reference("大儿子")))
        self.assertFalse(compatible(reference("大宝"), reference("二宝")))
        self.assertFalse(compatible(reference("母亲"), reference("父亲")))
        self.assertEqual(
            proposed_profile("other_adult", "我儿子")["category"], "other_adult"
        )
        self.assertEqual(reference("大宝是我儿子")["attributes"], ["child", "son", "eldest"])
        for quote in ["父亲和母亲", "我自己和父亲"]:
            with self.assertRaises(ValueError):
                reference(quote)
        with self.assertRaises(ValueError):
            proposed_profile("self", "我母亲")

    def test_explicit_and_unambiguous_implicit_changes_keep_original_facts_without_model(
        self,
    ):
        old = self.establish()
        before = deepcopy(self.state.to_dict()["facts"])
        calls = len(self.backend.calls)
        for text in ["改问我母亲，她65岁，肚子痛。", "我母亲65岁，肚子痛。"]:
            changed = self.engine.submit(self.state.session_id, UserInput(text))
            self.assertEqual(changed.kind, "subject_change")
            self.assertEqual(changed.subject_change["current"]["label"], "父亲")
            self.assertEqual(changed.subject_change["requested"]["label"], "母亲")
            self.assertEqual(self.state.to_dict()["facts"], before)
            self.assertEqual(len(self.backend.calls), calls)
        restored = self.resume(changed)
        self.assertEqual(restored.message, old.message)
        self.assertEqual(restored.question_id, old.question_id)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertIsNone(self.state.subject_resume)

    def test_same_enum_model_proposal_is_rejected_atomically(self):
        self.establish()
        before = deepcopy(self.state.to_dict()["facts"])
        command = record("我母亲", 65, "肚子痛", correction=True)
        with self.assertRaises(SubjectBoundaryError):
            self.engine._execute(self.state, command, "我母亲65岁，肚子痛。", "source")
        self.assertEqual(self.state.to_dict()["facts"], before)

    def test_model_has_a_subject_change_tool_and_does_not_write_new_person_facts(self):
        self.establish()
        before = deepcopy(self.state.to_dict()["facts"])
        self.backend.commands = [
            {"tool": "change_subject", "arguments": {"evidence": "我母亲"}}
        ]
        reply = self.engine.submit(
            self.state.session_id, UserInput("说到家族史，接下来咨询我母亲的问题。")
        )
        self.assertEqual(reply.kind, "subject_change")
        self.assertEqual(self.state.to_dict()["facts"], before)
        self.assertIn(
            "change_subject",
            str(self.engine.static_context),
        )

    def test_third_person_reflexive_is_not_a_second_reporter_identity(self):
        from medagent.consultation_subject import reference

        for quote in ["接下来为我母亲咨询她本人的不适", "母亲本人", "他本人", "她本人", "患者本人"]:
            with self.subTest(quote=quote):
                found = reference(quote)
                self.assertEqual(
                    found["label"] if found else None, "母亲" if "母亲" in quote else None
                )
        for quote in ["母亲和我本人", "母亲和本人"]:
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                reference(quote)
        self.assertEqual(reference("我本人")["label"], "本人")
        self.establish()
        self.backend.commands = [
            {"tool": "change_subject", "arguments": {"evidence": "为我母亲咨询她本人的不适"}}
        ]
        before = len(self.backend.calls)
        reply = self.engine.submit(
            self.state.session_id, UserInput("接下来为我母亲咨询她本人的不适，这不是父亲的相关病史。")
        )
        self.assertEqual(reply.kind, "subject_change")
        self.assertEqual(reply.subject_change["requested"]["label"], "母亲")
        self.assertEqual(len(self.backend.calls) - before, 1)

    def test_ordinary_family_history_stays_in_the_fathers_case(self):
        self.establish()
        text = "我母亲有糖尿病，是家族史。"
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "relevant_history",
                            "value": "报告者的母亲有糖尿病（背景补充）",
                            "assertion": "present",
                            "evidence": text,
                        }
                    ]
                },
            },
            calculation_fixture.ask("emergency_screen"),
        ]
        reply = self.engine.submit(self.state.session_id, UserInput(text))
        self.assertNotEqual(reply.kind, "subject_change")
        self.assertEqual(subject_profile(self.state)["label"], "父亲")
        self.assertIn("报告者的母亲", self.state.get("relevant_history"))

    def test_negative_or_hypothetical_switches_are_not_literal_actions(self):
        self.establish()
        current = subject_profile(self.state)
        for text in ["如果改问我母亲，需要新问诊吗？", "不是改问我母亲。", "不要换个人，我只说父亲。", "我母亲有糖尿病，是家族史。"]:
            self.assertIsNone(explicit_switch(text, current), text)

    def test_generic_role_update_does_not_erase_the_specific_reported_label(self):
        self.establish()
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "consult_for",
                            "value": "other_adult",
                            "assertion": "present",
                            "evidence": "其他家人",
                            "correction": True,
                        }
                    ]
                },
            },
            calculation_fixture.ask("emergency_screen"),
        ]
        self.engine.submit(self.state.session_id, UserInput("这次是其他家人。"))
        self.assertEqual(subject_profile(self.state)["label"], "父亲")
        self.assertEqual(
            self.engine._finish(self.state).summary["known"][0]["value"], "父亲"
        )

    def test_child_refinements_do_not_forget_previous_compatible_constraints(self):
        self.establish("大宝", 8, "咳嗽", "child")
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "consult_for",
                            "value": "child",
                            "assertion": "present",
                            "evidence": "儿子",
                            "correction": True,
                        }
                    ]
                },
            },
            calculation_fixture.ask("emergency_screen"),
        ]
        self.engine.submit(self.state.session_id, UserInput("大宝是我儿子。"))
        self.assertEqual(
            subject_profile(self.state)["reference"]["attributes"],
            ["child", "son", "eldest"],
        )
        calls = len(self.backend.calls)
        reply = self.engine.submit(self.state.session_id, UserInput("改问我女儿。"))
        self.assertEqual(reply.kind, "subject_change")
        self.assertEqual(len(self.backend.calls), calls)

    def test_repeated_model_boundaries_resume_the_original_question(self):
        old = self.establish()
        for name in ["我母亲", "我妻子"]:
            self.backend.commands = [
                {"tool": "change_subject", "arguments": {"evidence": name}}
            ]
            changed = self.engine.submit(
                self.state.session_id, UserInput(f"这里不是家族史，想咨询{name}的情况。")
            )
            self.assertEqual(changed.kind, "subject_change")
        self.assertEqual(self.resume(changed).message, old.message)

    def test_missing_stale_or_reused_resume_action_has_no_state_or_event_mutation(self):
        old = self.establish()
        changed = self.engine.submit(self.state.session_id, UserInput("改问我母亲。"))
        for action in [None, old.action_id]:
            before = self.state.to_dict()
            events = len(self.engine.events.for_session(self.state.session_id))
            with self.assertRaises(StaleActionError):
                self.engine.submit(
                    self.state.session_id,
                    UserInput("继续", "resume_current_subject", action),
                )
            self.assertEqual(self.state.to_dict(), before)
            self.assertEqual(
                len(self.engine.events.for_session(self.state.session_id)), events
            )
        self.resume(changed)
        with self.assertRaises(StaleActionError):
            self.resume(changed)

    def test_semantic_boundary_restores_a_cancelled_but_unexecuted_input_card(self):
        self.establish()
        text = "请把1.25kg换算成g"
        self.backend.commands = [calculation_fixture.calculation(text)]
        draft = self.engine.submit(self.state.session_id, UserInput(text))
        proposal = draft.calculation_input["proposal_id"]
        self.backend.commands = [
            {"tool": "change_subject", "arguments": {"evidence": "母亲"}}
        ]
        changed = self.engine.submit(
            self.state.session_id, UserInput("父亲的记录保留，这里想咨询母亲的情况。")
        )
        self.assertEqual(self.state.pending_calculation["proposal_id"], proposal)
        restored = self.resume(changed)
        self.assertEqual(restored.kind, "calculation_input")
        self.assertEqual(self.state.pending_calculation["proposal_id"], proposal)
        result = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对",
                "confirm_calculation",
                restored.action_id,
                {
                    "proposal_id": proposal,
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )
        self.assertEqual(result.calculation_result["data"]["value"], "1250")
        self.assertEqual(len(self.state.calculations), 1)

    def test_return_does_not_roll_back_legitimate_new_facts_or_reopen_an_obsolete_card(
        self,
    ):
        self.establish()
        text = "请把1.25kg换算成g"
        self.backend.commands = [calculation_fixture.calculation(text)]
        self.engine.submit(self.state.session_id, UserInput(text))
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "medications_taken",
                            "value": "父亲昨晚用过药物甲",
                            "assertion": "present",
                            "evidence": "父亲昨晚用过药物甲",
                        }
                    ]
                },
            },
            {"tool": "change_subject", "arguments": {"evidence": "母亲"}},
        ]
        changed = self.engine.submit(
            self.state.session_id, UserInput("父亲昨晚用过药物甲；顺便想咨询母亲的情况。")
        )
        current = self.state.facts["medications_taken"].fact_id
        restored = self.resume(changed)
        self.assertNotEqual(restored.kind, "calculation_input")
        self.assertIsNone(self.state.pending_calculation)
        self.assertEqual(self.state.facts["medications_taken"].fact_id, current)
        self.assertEqual(self.state.calculations, [])

    def test_calculation_failure_source_is_preserved_through_subject_dialog(self):
        self._verify_failed_calculation_return(False)

    def test_model_boundary_restores_the_original_failed_supplement_retry(self):
        self._verify_failed_calculation_return(True)

    def _verify_failed_calculation_return(self, semantic):
        self.establish()
        text = "请把1.25kg换算成g；父亲昨晚用过药物甲。"
        self.backend.commands = [calculation_fixture.calculation(text)]
        draft = self.engine.submit(self.state.session_id, UserInput(text))
        result = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )
        self.backend.commands = [RuntimeError("fixture failure")]
        failed = self.engine.submit(
            self.state.session_id,
            UserInput(
                "继续",
                "resume_after_calculation",
                result.action_id,
                {"execution_id": result.calculation_result["execution_id"]},
            ),
        )
        if semantic:
            self.backend.commands = [
                {"tool": "change_subject", "arguments": {"evidence": "母亲"}}
            ]
        changed = self.engine.submit(
            self.state.session_id,
            UserInput("父亲记录保留，这里想咨询母亲的情况。" if semantic else "改问我母亲。"),
        )
        self.assertEqual(self.state.pending_input, text)
        self.assertTrue(self.state.calculation_feedback["processing"])
        restored = self.resume(changed)
        self.assertEqual(restored.kind, failed.kind)
        self.assertEqual(restored.calculation_followup["status"], "pending")
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "medications_taken",
                            "value": "父亲昨晚用过药物甲",
                            "assertion": "present",
                            "evidence": "父亲昨晚用过药物甲",
                        }
                    ]
                },
            },
            calculation_fixture.ask("emergency_screen"),
        ]
        recovered = self.engine.submit(
            self.state.session_id, UserInput("重试", "retry_turn", restored.action_id)
        )
        self.assertEqual(recovered.calculation_followup["status"], "forwarded")
        self.assertEqual(len(self.state.calculations), 1)

    def test_observation_failure_and_emergency_priority_survive_subject_dialog(self):
        self.engine, _, self.state, summary = observation_fixture.measured_case(
            summary=True
        )
        self.engine.backend = self.backend
        task = observation_fixture.repeat(self.engine, self.state, summary)
        self.backend.commands = [RuntimeError("fixture failure")]
        failed = self.engine.submit(
            self.state.session_id,
            UserInput("腋温37.6℃；更正：昨晚用过药物甲。", expected_action_id=task.action_id),
        )
        changed = self.engine.submit(self.state.session_id, UserInput("改问我母亲。"))
        self.assertIsNotNone(self.state.observation_feedback)
        restored = self.resume(changed)
        self.assertEqual(restored.kind, failed.kind)
        self.assertEqual(restored.observation_result["supplement"]["status"], "pending")
        calls = len(self.backend.calls)
        urgent = self.engine.submit(
            self.state.session_id,
            UserInput("改问母亲，她现在叫不醒了。", expected_action_id=restored.action_id),
        )
        self.assertEqual(urgent.kind, "emergency")
        self.assertEqual(len(self.backend.calls), calls)
        self.assertIsNone(self.state.subject_resume)


if __name__ == "__main__":
    unittest.main()
