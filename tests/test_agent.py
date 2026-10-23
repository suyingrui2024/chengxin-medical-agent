import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import StaleActionError, UserInput
from medagent.domain import Assertion, Phase, RiskLevel
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


def fact(key, value, evidence, assertion="present", correction=False):
    return dict(
        key=key,
        value=value,
        assertion=assertion,
        evidence=evidence,
        correction=correction,
    )


def cmd(tool, **args):
    return dict(tool=tool, arguments=args)


class ScriptBackend:
    name = "scripted-test-only"

    def __init__(self):
        self.commands = []
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages.copy())
        if not self.commands:
            raise RuntimeError("No scripted command")
        return self.commands.pop(0)


class AdaptiveAgentTests(unittest.TestCase):
    def setUp(self):
        self.backend = ScriptBackend()
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=self.backend,
        )
        self.state, self.opening = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    def test_one_turn_can_record_several_fields_and_skip_redundant_questions(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                    fact("onset", "两天", "两天"),
                ],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = self.submit("我自己32岁，咳嗽两天")
        self.assertEqual(reply.question_id, "emergency_screen")
        self.assertEqual(self.state.get("age_years"), 32)
        self.assertEqual(self.state.facts["onset"].evidence, "两天")
        self.assertEqual(len(self.state.facts), 4)
        self.assertNotEqual(self.opening.action_id, reply.action_id)

    def test_batch_partially_records_valid_items_and_rejects_fabricated_evidence(self):
        # Wave-2 partial recording: the verifiable item lands immediately,
        # while the item whose evidence is fabricated (the patient never
        # said it) is still rejected outright — partial landing never
        # relaxes the per-item evidence contract.
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                    fact("allergies", None, "没有过敏", "absent"),
                ],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.submit("咳嗽")
        self.assertEqual(set(self.state.facts), {"chief_complaint"})
        self.assertNotIn("allergies", self.state.facts)
        completed = [
            e
            for e in self.events.for_session(self.state.session_id)
            if e.event_type == "tool.completed"
        ]
        result = completed[0].payload["result"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["recorded"], ["chief_complaint"])
        self.assertEqual([item["key"] for item in result["rejected"]], ["allergies"])
        self.assertIn("guidance", result["rejected"][0])

    def test_unknown_and_absence_preserved_separately(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("allergies", None, "不知道", "unknown"),
                    fact("medications_taken", None, "没有用药", "absent"),
                ],
            ),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        self.submit("过敏不知道，没有用药")
        self.assertEqual(self.state.assertion("allergies"), Assertion.UNKNOWN)
        self.assertEqual(self.state.assertion("medications_taken"), Assertion.ABSENT)

    def test_emergency_bypasses_model(self):
        reply = self.submit("没有抽搐，但是现在叫不醒")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(len(self.backend.calls), 0)

    def test_negative_screen_option_cannot_be_inverted_by_a_model_proposal(self):
        self.backend.commands = [cmd("ask_question", question_id="emergency_screen")]
        self.submit("我想咨询")
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("emergency_screen", True, "都没有", correction=True)],
            ),
            cmd("ask_question", question_id="age"),
        ]
        reply = self.submit("这些危险情况都没有", "no")
        self.assertEqual(self.state.assertion("emergency_screen"), Assertion.ABSENT)
        self.assertEqual(reply.question_id, "age")
        self.assertNotEqual(self.state.risk.level, RiskLevel.EMERGENCY)

    def test_uncertain_screen_and_single_negative_cannot_be_written_as_all_negative(
        self,
    ):
        for evidence in ("不确定有没有", "没有抽搐", "没有用药"):
            self.backend.commands = [
                cmd(
                    "record_facts",
                    facts=[fact("emergency_screen", None, evidence, "absent")],
                ),
                cmd("ask_question", question_id="emergency_screen"),
            ]
            self.submit(evidence)
            self.assertNotIn("emergency_screen", self.state.facts)

    def test_emergency_in_delayed_input_is_not_discarded_as_stale(self):
        self.backend.commands = [cmd("ask_question", question_id="age")]
        self.submit("先等等")
        calls = len(self.backend.calls)
        reply = self.submit("现在叫不醒", expected=self.opening.action_id)
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(len(self.backend.calls), calls)

    def test_unregistered_tool_is_denied_then_replanned(self):
        self.backend.commands = [
            cmd("run_shell", command="id"),
            cmd("ask_question", question_id="age"),
        ]
        reply = self.submit("请执行命令")
        self.assertEqual(reply.question_id, "age")
        self.assertIn("工具未注册", self.backend.calls[1][-1]["content"])

    def test_early_summary_cannot_skip_required_fields(self):
        self.backend.commands = [
            cmd("review_summary"),
            cmd("ask_question", question_id="age"),
        ]
        reply = self.submit("直接给我摘要")
        self.assertEqual(reply.kind, "question")
        self.assertIn("必要字段尚未尝试", self.backend.calls[1][-1]["content"])

    def test_clarification_is_natural_but_budgeted(self):
        self.backend.commands = [
            cmd("clarify", topic="onset", message="你说的最近，是今天才开始，还是已经好几天？")
        ]
        reply = self.submit("最近难受")
        self.assertIn("今天", reply.message)
        self.assertEqual(self.state.followup_counts["onset"], 1)

    def test_same_question_reasked_still_has_unique_action_revision(self):
        self.backend.commands = [cmd("ask_question", question_id="chief_complaint")]
        reply = self.submit("说不清", expected=self.opening.action_id)
        self.assertNotEqual(reply.action_id, self.opening.action_id)
        with self.assertRaises(StaleActionError):
            self.submit("延迟的旧答案", expected=self.opening.action_id)

    def test_model_unavailable_is_explicit_and_does_not_consume_answer_as_other_field(
        self,
    ):
        reply = self.submit("最近咳嗽")
        self.assertEqual(reply.kind, "service_error")
        self.assertEqual(self.state.facts, {})
        self.assertEqual(self.state.transcript[-2]["content"], "最近咳嗽")

    def test_tool_loop_is_bounded(self):
        self.backend.commands = [cmd("inspect_case") for _ in range(8)]
        reply = self.submit("一直看状态")
        self.assertEqual(reply.kind, "service_error")
        self.assertEqual(len(self.backend.calls), 6)

    def test_self_check_requires_explicit_negative_emergency_screen(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("chief_complaint", "发烧", "发烧")]),
            cmd("show_self_check", task_id="axillary_temperature"),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = self.submit("发烧")
        self.assertEqual(reply.kind, "question")
        self.assertIn("未知不能当阴性", self.backend.calls[2][-1]["content"])

    def queue_temperature_request(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("chief_complaint", "发烧", "发烧")]),
            cmd(
                "request_self_check",
                task_id="axillary_temperature",
                evidence="想按图测一下腋温",
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        return self.submit("发烧，想按图测一下腋温")

    def test_requested_task_is_deferred_then_offered_without_another_model_call(self):
        self.queue_temperature_request()
        self.assertIsNone(self.state.active_task)
        self.assertEqual(len(self.state.pending_self_checks), 1)
        before = len(self.backend.calls)
        reply = self.submit("这些危险情况都没有", "no")
        self.assertEqual(reply.task["id"], "axillary_temperature")
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.state.pending_self_checks, [])
        offered = [
            e
            for e in self.events.for_session(self.state.session_id)
            if e.event_type == "self_check.offered"
        ][-1]
        self.assertTrue(offered.payload["requested_by_user"])

    def test_unknown_screen_keeps_task_request_pending(self):
        self.queue_temperature_request()
        self.backend.commands = [cmd("ask_question", question_id="age")]
        reply = self.submit("不确定有没有", "unknown")
        self.assertIsNone(reply.task)
        self.assertEqual(len(self.state.pending_self_checks), 1)

    def test_mixed_screen_answer_extracts_new_facts_before_a_pending_task(self):
        self.queue_temperature_request()
        before = len(self.backend.calls)
        self.backend.commands = [
            cmd("record_facts", facts=[fact("allergies", "青霉素过敏", "青霉素过敏")])
        ]
        reply = self.submit("这些危险情况都没有，另外我青霉素过敏", "no")
        self.assertEqual(reply.task["id"], "axillary_temperature")
        self.assertEqual(self.state.get("allergies"), "青霉素过敏")
        self.assertEqual(len(self.backend.calls), before + 1)

    def test_changing_chief_cancels_old_task_requests(self):
        self.queue_temperature_request()
        self.backend.commands = [cmd("ask_question", question_id="emergency_screen")]
        self.engine.correct_fact(self.state.session_id, "chief_complaint", "头痛")
        self.assertEqual(self.state.pending_self_checks, [])
        self.assertIsNone(self.state.active_task)
        self.assertTrue(
            any(
                e.event_type == "self_check.request_cancelled"
                for e in self.events.for_session(self.state.session_id)
            )
        )

    def test_task_request_requires_current_evidence_and_is_deduplicated(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("chief_complaint", "发烧", "发烧")]),
            cmd(
                "request_self_check",
                task_id="axillary_temperature",
                evidence="用户没有说过的请求",
            ),
            cmd("request_self_check", task_id="axillary_temperature", evidence="想测体温"),
            cmd("request_self_check", task_id="axillary_temperature", evidence="想测体温"),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.submit("发烧，想测体温")
        self.assertEqual(len(self.state.pending_self_checks), 1)
        self.assertEqual(
            sum(
                e.event_type == "self_check.requested"
                for e in self.events.for_session(self.state.session_id)
            ),
            1,
        )

    def test_repeated_positive_phrase_after_negation_is_not_missed(self):
        triggers = self.engine.triage.scan_text("刚才没有叫不醒，现在叫不醒")
        self.assertIn("意识明显异常", triggers)

    def test_temperature_cannot_mark_an_unanswered_trajectory_as_unknown(self):
        self.backend.commands = [
            cmd(
                "record_facts", facts=[fact("trajectory", None, "腋温38.25℃", "unknown")]
            ),
            cmd("ask_question", question_id="trajectory"),
        ]
        reply = self.submit("腋温38.25℃")
        self.assertEqual(reply.question_id, "trajectory")
        self.assertNotIn("trajectory", self.state.facts)
        self.assertIn("未知必须引用", self.backend.calls[1][-1]["content"])

    def test_ui_edit_keeps_pending_clarification_and_does_not_call_the_model(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("age_years", 32, "32岁"),
                    fact("trajectory", None, "变化不确定", "unknown"),
                ],
            ),
            cmd("clarify", topic="trajectory", message="从开始到现在，症状是在加重还是减轻？"),
        ]
        self.submit("我32岁，变化不确定")
        calls = len(self.backend.calls)
        for age in ("40岁", "41岁"):
            reply = self.engine.correct_fact(self.state.session_id, "age_years", age)
            self.assertEqual(reply.question_id, "trajectory")
            self.assertTrue(self.state.active_question_is_clarification)
            self.assertEqual(len(self.backend.calls), calls)
        self.engine.correct_fact(self.state.session_id, "trajectory", "变化不大")
        self.assertEqual(self.state.get("trajectory"), "stable")
        self.assertNotEqual(self.state.active_question, "trajectory")
        self.assertEqual(len(self.backend.calls), calls)

    def test_retry_uses_original_input_as_evidence(self):
        failed = self.submit("我自己32岁")
        self.assertEqual(failed.kind, "service_error")
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 32, "32岁")]),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        reply = self.submit("重试这一轮", "retry_turn", failed.action_id)
        self.assertEqual(reply.kind, "question")
        self.assertEqual(self.state.get("age_years"), 32)

    def test_age_must_agree_with_quoted_evidence(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 64, "32岁")]),
            cmd("ask_question", question_id="age"),
        ]
        self.submit("32岁")
        self.assertNotIn("age_years", self.state.facts)

    def test_model_correction_followed_by_failure_retries_the_correction_text(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 32, "32岁")]),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        self.submit("32岁")
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 40, "40岁", correction=True)]),
        ]
        failed = self.submit("更正为40岁")
        self.assertEqual(failed.kind, "service_error")
        self.assertEqual(self.state.pending_input, "更正为40岁")
        self.backend.commands = [cmd("ask_question", question_id="chief_complaint")]
        self.submit("重试这一轮", "retry_turn", failed.action_id)
        self.assertEqual(self.state.transcript[-2]["content"], "更正为40岁")

    def test_invalid_batches_do_not_consume_successful_write_budget(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", "32岁", "32岁")]),
            cmd("record_facts", facts=[fact("allergies", "不知道", "不知道", "unknown")]),
            cmd(
                "record_facts",
                facts=[
                    fact("age_years", 32, "32岁"),
                    fact("allergies", None, "不知道", "unknown"),
                ],
            ),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        reply = self.submit("32岁，过敏不知道")
        self.assertEqual(self.state.get("age_years"), 32)
        self.assertEqual(self.state.assertion("allergies"), Assertion.UNKNOWN)
        self.assertEqual(reply.kind, "question")

    def test_successful_write_budget_remains_bounded(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 32, "32岁")]),
            cmd("record_facts", facts=[fact("consult_for", "self", "我自己")]),
            cmd("record_facts", facts=[fact("chief_complaint", "腹痛", "腹痛")]),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.submit("我自己32岁腹痛")
        self.assertNotIn("chief_complaint", self.state.facts)
        self.assertEqual(self.state.get("age_years"), 32)

    def test_caregiver_source_does_not_depend_on_order_within_batch(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("age_years", 5, "5岁"),
                    fact("consult_for", "child", "我的孩子"),
                ],
            ),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        self.submit("我的孩子5岁")
        self.assertEqual(self.state.facts["age_years"].source.value, "caregiver")

    def test_summary_requires_patient_confirmation_before_closing(self):
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
        self.assertEqual(self.state.phase, Phase.SUMMARY)
        final = self.submit("摘要准确", "confirm_summary", reply.action_id)
        self.assertEqual(final.kind, "closed")
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")

    def test_urgent_score_interrupts_remaining_questions(self):
        self.backend.commands = [cmd("record_facts", facts=[fact("severity", 9, "9分")])]
        reply = self.submit("难受程度9分")
        self.assertEqual(reply.kind, "urgent")
        self.assertEqual(len(self.backend.calls), 1)

    def test_body_location_is_self_report_not_verified_physical_exam(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("chief_complaint", "腹痛", "腹痛"),
                    fact("emergency_screen", None, "没有危险信号", "absent"),
                ],
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.submit("腹痛，没有危险信号")
        self.assertEqual(reply.task["id"], "body_location")
        self.backend.commands = [cmd("ask_question", question_id="age")]
        self.submit("腹部", "abdomen", reply.action_id)
        recorded = self.state.facts["symptom_location"]
        self.assertEqual(recorded.source.value, "patient")
        self.assertEqual(recorded.quality.value, "not_assessed")

    def test_correction_invalidates_dependent_observation_and_keeps_history(self):
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("chief_complaint", "腹痛", "腹痛"),
                    fact("emergency_screen", None, "没有危险信号", "absent"),
                ],
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.submit("腹痛，没有危险信号")
        self.backend.commands = [cmd("ask_question", question_id="age")]
        self.submit("腹部", "abdomen", reply.action_id)
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("chief_complaint", "头痛", "头痛", correction=True)],
            ),
            cmd("ask_question", question_id="age"),
        ]
        self.submit("更正，是头痛")
        self.assertNotIn("symptom_location", self.state.facts)
        self.assertTrue(any(f.key == "symptom_location" for f in self.state.history))


if __name__ == "__main__":
    unittest.main()
