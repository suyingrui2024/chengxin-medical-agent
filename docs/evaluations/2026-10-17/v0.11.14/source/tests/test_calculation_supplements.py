"""Calculation-first replies must not silently strand the rest of a patient turn."""
from copy import deepcopy
import json
import unittest

from medagent.calculation_followup import simple_conversion_request
from medagent.consultation import StaleActionError, UserInput
from medagent.domain import Assertion, Fact, Source
from tests import test_patient_calculations as fixture
from tests import test_repeated_observations as observation_fixture


class Backend:
    name = "fixture"

    def __init__(self):
        self.commands, self.calls = [], []

    def decide(self, identity, messages):
        self.calls.append(deepcopy(messages))
        command = self.commands.pop(0)
        if isinstance(command, BaseException):
            raise command
        return command


def medication(correction=False):
    return {
        "tool": "record_facts",
        "arguments": {
            "facts": [
                {
                    "key": "medications_taken",
                    "value": "昨晚用过药物甲",
                    "assertion": "present",
                    "evidence": "昨晚用过药物甲",
                    "correction": correction,
                }
            ]
        },
    }


def ask(identity="age"):
    return {"tool": "ask_question", "arguments": {"question_id": identity}}


def calculation(text):
    return {
        "tool": "run_capability",
        "arguments": {
            "id": "calculate.units",
            "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
            "evidence": text,
        },
    }


class CalculationSupplementTests(unittest.TestCase):
    def test_earlier_chief_change_cannot_indirectly_invalidate_newer_medication(self):
        self.state.record(
            Fact("chief_complaint", "肚子痛", Assertion.PRESENT, Source.PATIENT, "seed")
        )
        draft = self.offer("更正主诉为咳嗽；请把1.25kg换算成g。")
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "medications_taken",
                            "value": "今天用过药物乙",
                            "assertion": "present",
                            "evidence": "今天用过药物乙",
                        }
                    ]
                },
            },
            ask("allergies"),
        ]
        newer = self.engine.submit(
            self.state.session_id,
            UserInput("先不换算；今天用过药物乙。", expected_action_id=draft.action_id),
        )
        medication_id = self.state.facts["medications_taken"].fact_id
        before = len(self.engine.events.for_session(self.state.session_id))
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "chief_complaint",
                            "value": "咳嗽",
                            "assertion": "present",
                            "evidence": "咳嗽",
                            "correction": True,
                        }
                    ]
                },
            },
            ask("allergies"),
        ]
        self.process(newer)
        self.assertEqual(self.state.facts["medications_taken"].fact_id, medication_id)
        self.assertEqual(self.state.get("chief_complaint"), "肚子痛")
        self.assertFalse(
            any(
                e.event_type == "fact.invalidated"
                for e in self.engine.events.for_session(self.state.session_id)[before:]
            )
        )
        self.assertIn("不能覆盖随后", json.dumps(self.backend.calls[-1], ensure_ascii=False))

    def test_explicit_edit_after_followup_failure_keeps_original_supplement_pending(
        self,
    ):
        self.state.record(
            Fact("age_years", 32, Assertion.PRESENT, Source.PATIENT, "seed")
        )
        result = self.confirm(self.offer())
        self.backend.commands = [RuntimeError("fixture failure")]
        self.continue_result(result)
        edited = self.engine.correct_fact(
            self.state.session_id,
            "age_years",
            "34岁",
            self.state.facts["age_years"].fact_id,
        )
        self.assertEqual(self.state.get("age_years"), 34)
        self.assertIsNotNone(self.state.calculation_feedback)
        self.assertEqual(edited.calculation_followup["status"], "pending")
        self.assertTrue(edited.calculation_followup["can_process"])

    def setUp(self):
        helper = fixture.PatientCalculationTests()
        helper.setUp()
        self.engine, self.state = helper.engine, helper.state
        self.backend = Backend()
        self.engine.backend = self.backend
        self.text = "请把1.25kg换算成g；昨晚用过药物甲。"

    def offer(self, text=None, quote=None):
        self.text = text or self.text
        self.backend.commands = [calculation(quote or self.text)]
        return self.engine.submit(self.state.session_id, UserInput(self.text))

    def confirm(self, draft):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对参数",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )

    def continue_result(self, result):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "继续处理补充",
                "resume_after_calculation",
                result.action_id,
                {"execution_id": result.calculation_result["execution_id"]},
            ),
        )

    def process(self, reply):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "处理补充",
                "process_calculation_supplement:" + reply.calculation_followup["id"],
                reply.action_id,
            ),
        )

    def test_simple_requests_have_a_bounded_fast_path_and_unknown_remainders_are_kept(
        self,
    ):
        for text in [
            "请把1.25kg换算成g",
            "我想换算单位，但还没有提供数字或单位",
            "请把血糖180mg/dL换成mmol/L，只做单位换算。",
            "想做血糖单位换算，但还没有给数字和单位，请先让我填写。",
        ]:
            self.assertTrue(simple_conversion_request(text), text)
        for text in [
            self.text,
            "我自己32岁；请把1.25kg换算成g",
            "请把1.25kg换算成g，还需要注意什么？",
            "1" * 1999 + "病",
        ]:
            self.assertFalse(simple_conversion_request(text), text[:100])

    def test_confirmation_keeps_history_pending_then_forwards_full_original_with_original_turn(
        self,
    ):
        draft = self.offer(quote="请把1.25kg换算成g")
        source = draft.calculation_followup["source_turn_id"]
        self.assertEqual(draft.calculation_followup["status"], "pending")
        self.assertEqual(draft.calculation_followup["text"], self.text)
        result = self.confirm(draft)
        self.assertEqual(result.calculation_result["data"]["value"], "1250")
        self.assertEqual(result.calculation_followup["outcome"], "completed")
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(self.state.facts, {})
        self.backend.commands = [medication(), ask()]
        reply = self.continue_result(result)
        self.assertEqual(reply.question_id, "age")
        self.assertEqual(self.state.facts["medications_taken"].turn_id, source)
        self.assertIn(self.text, self.backend.calls[-1][1]["content"])
        self.assertEqual(reply.calculation_followup["status"], "forwarded")
        self.assertIsNone(self.state.calculation_feedback)
        self.assertEqual(len(self.state.calculations), 1)

    def test_skipping_arithmetic_still_processes_the_rest_of_the_original_message(self):
        draft = self.offer()
        self.backend.commands = [medication(), ask()]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "先跳过换算",
                "skip_calculation",
                draft.action_id,
                {"proposal_id": draft.calculation_input["proposal_id"], "input": {}},
            ),
        )
        self.assertEqual(reply.calculation_followup["outcome"], "skipped")
        self.assertEqual(reply.calculation_followup["status"], "forwarded")
        self.assertIn("药物甲", self.state.get("medications_taken"))
        self.assertEqual(self.state.calculations, [])

    def test_failed_followup_retries_original_source_and_rejects_reopening_consumed_calculation(
        self,
    ):
        result = self.confirm(self.offer())
        source = result.calculation_followup["source_turn_id"]
        self.backend.commands = [RuntimeError("fixture failure")]
        failed = self.continue_result(result)
        self.assertEqual(failed.kind, "service_error")
        self.assertEqual(failed.calculation_followup["status"], "pending")
        self.assertEqual(self.state.pending_input, self.text)
        self.backend.commands = [calculation(self.text), medication(), ask()]
        reply = self.engine.submit(
            self.state.session_id, UserInput("重试这一轮", "retry_turn", failed.action_id)
        )
        self.assertEqual(reply.question_id, "age")
        self.assertEqual(self.state.facts["medications_taken"].turn_id, source)
        self.assertEqual(len(self.state.calculations), 1)
        self.assertIsNone(self.state.pending_calculation)
        self.assertIn("不要重复", json.dumps(self.backend.calls[-1], ensure_ascii=False))

    def test_new_information_leaves_old_supplement_pending_and_cannot_overwrite_later_correction(
        self,
    ):
        draft = self.offer("我32岁；请把1.25kg换算成g；昨晚用过药物甲。")
        self.backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "age_years",
                            "value": 34,
                            "assertion": "present",
                            "evidence": "34岁",
                        }
                    ]
                },
            },
            ask("chief_complaint"),
        ]
        newer = self.engine.submit(
            self.state.session_id,
            UserInput("先不换算了，更正年龄34岁。", expected_action_id=draft.action_id),
        )
        age_id = self.state.facts["age_years"].fact_id
        self.assertTrue(newer.calculation_followup["can_process"])
        self.assertIsNone(self.state.pending_calculation)
        stale = {
            "tool": "record_facts",
            "arguments": {
                "facts": [
                    {
                        "key": "age_years",
                        "value": 32,
                        "assertion": "present",
                        "evidence": "32岁",
                        "correction": True,
                    },
                    *medication()["arguments"]["facts"],
                ]
            },
        }
        self.backend.commands = [stale, medication(), ask("chief_complaint")]
        reply = self.process(newer)
        self.assertEqual(self.state.get("age_years"), 34)
        self.assertEqual(self.state.facts["age_years"].fact_id, age_id)
        self.assertIn("不能覆盖随后", json.dumps(self.backend.calls[-1], ensure_ascii=False))
        self.assertEqual(reply.calculation_followup["status"], "forwarded")

    def test_later_failed_message_retries_itself_instead_of_the_earlier_calculation_source(
        self,
    ):
        draft = self.offer()
        new_text = "我想补充，年龄34岁。"
        self.backend.commands = [RuntimeError("new-message failure")]
        failed = self.engine.submit(
            self.state.session_id,
            UserInput(new_text, expected_action_id=draft.action_id),
        )
        self.assertFalse(self.state.calculation_feedback["processing"])
        self.backend.commands = [ask("chief_complaint")]
        reply = self.engine.submit(
            self.state.session_id, UserInput("重试", "retry_turn", failed.action_id)
        )
        context = json.loads(self.backend.calls[-1][1]["content"])
        self.assertEqual(context["latest_user_text"], new_text)
        self.assertEqual(reply.calculation_followup["status"], "pending")

    def test_new_calculation_cannot_replace_an_unprocessed_earlier_supplement(self):
        draft = self.offer()
        pending = draft.calculation_followup["id"]
        text = "请把1.25kg换算成g"
        self.backend.commands = [calculation(text), ask("chief_complaint")]
        reply = self.engine.submit(
            self.state.session_id, UserInput(text, expected_action_id=draft.action_id)
        )
        self.assertEqual(reply.calculation_followup["id"], pending)
        self.assertIsNone(self.state.pending_calculation)
        self.assertTrue(reply.calculation_followup["can_process"])

    def test_pending_supplement_blocks_summary_confirmation_and_stale_processing_actions(
        self,
    ):
        draft = self.offer()
        self.backend.commands = [ask("chief_complaint")]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("先不换算了", expected_action_id=draft.action_id),
        )
        summary = self.engine._finish(self.state)
        self.engine._record_reply(self.state, summary, "test-summary", "fixture")
        self.assertEqual(summary.quick_replies, [])
        revision = self.state.revision
        for value, action in [
            ("confirm_summary", summary.action_id),
            ("process_calculation_supplement:foreign", summary.action_id),
            (
                "process_calculation_supplement:" + reply.calculation_followup["id"],
                reply.action_id,
            ),
        ]:
            with self.assertRaises(StaleActionError):
                self.engine.submit(
                    self.state.session_id, UserInput("旧操作", value, action)
                )
        self.assertEqual(self.state.revision, revision)

    def test_raw_emergency_remains_prior_to_calculation_and_followup_model_work(self):
        result = self.confirm(self.offer())
        before = len(self.backend.calls)
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("现在叫不醒了", expected_action_id=result.action_id),
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.calculation_followup["status"], "unprocessed")
        self.assertEqual(len(self.backend.calls), before)
        self.assertNotIn(
            "interaction_followup",
            {b["type"] for b in reply.to_dict()["presentation"]["blocks"]},
        )

    def test_repeat_measurement_can_suspend_a_mixed_request_without_losing_its_supplement(
        self,
    ):
        self.engine, _, self.state, summary = observation_fixture.measured_case(
            summary=True
        )
        self.engine.backend = self.backend
        draft = self.offer("请把1.25kg换算成g；更正：昨晚用过药物甲。")
        task = observation_fixture.repeat(self.engine, self.state, draft)
        again = self.engine.submit(
            self.state.session_id,
            UserInput("腋温37.6℃", expected_action_id=task.action_id),
        )
        self.assertEqual(again.kind, "calculation_input")
        result = self.confirm(again)
        self.backend.commands = [
            medication(True),
            {"tool": "review_summary", "arguments": {}},
        ]
        reply = self.continue_result(result)
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(self.state.get("temperature_c"), 37.6)
        self.assertEqual(reply.calculation_followup["status"], "forwarded")


if __name__ == "__main__":
    unittest.main()
