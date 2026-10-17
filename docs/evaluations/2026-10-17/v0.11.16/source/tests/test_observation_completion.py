"""Host completion signals and location-specific clarification, independent of LLM skill."""
import json
import unittest

from medagent.consultation import UserInput
from tests import test_self_check_conversation as conversation
from tests.test_agent import cmd, fact


class ObservationCompletionTests(unittest.TestCase):
    def setUp(self):
        helper = conversation.SelfCheckConversationTests()
        helper.setUp()
        self.helper = helper
        self.engine, self.state, self.backend = (
            helper.engine,
            helper.state,
            helper.backend,
        )

    def context(self):
        return json.loads(self.backend.calls[-1][1]["content"])

    def test_fixed_location_is_explicitly_completed_before_next_model_decision(self):
        offered = self.helper.offer()
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.helper.submit("头/面部", "head", offered.action_id)
        completion = self.context()["observation_completion"]
        recorded = self.state.facts["symptom_location"]
        self.assertEqual(completion["task_id"], "body_location")
        self.assertEqual(completion["result_key"], "symptom_location")
        self.assertEqual(completion["fact_id"], recorded.fact_id)
        self.assertEqual(completion["source_turn_id"], recorded.turn_id)
        self.assertTrue(completion["result_is_current"])
        self.assertEqual(completion["quality"], "not_assessed")
        self.assertIsNone(self.state.observation_feedback)

    def test_temperature_completion_cannot_be_confused_with_a_model_writable_fact(self):
        offered = self.helper.offer("axillary_temperature")
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        reply = self.helper.submit("37.2℃", action=offered.action_id)
        context = self.context()
        self.assertEqual(
            context["observation_completion"]["fact_id"],
            self.state.facts["temperature_c"].fact_id,
        )
        self.assertIn("temperature_c", context["observation_fields_read_only"])
        self.assertIsNone(context["completed_observation_with_supplement"])
        self.assertIsNone(reply.observation_result)

    def test_completed_context_survives_service_retry_and_does_not_replay_on_new_input(
        self,
    ):
        self.helper.offer()
        text = "右太阳穴附近疼"
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence=text,
            )
        ]
        proposed = self.helper.submit(text)
        self.backend.commands = [RuntimeError("Synthetic unavailable backend")]
        failed = self.helper.submit(
            "核对部位", proposed.task["result_proposal"]["choice"], proposed.action_id
        )
        self.assertEqual(failed.kind, "service_error")
        first = self.context()["observation_completion"]
        fact_id = self.state.facts["symptom_location"].fact_id
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        reply = self.helper.submit("重试这一轮", "retry_turn", failed.action_id)
        self.assertEqual(self.context()["observation_completion"], first)
        self.assertEqual(self.state.facts["symptom_location"].fact_id, fact_id)
        self.backend.commands = [
            cmd("record_facts", facts=[fact("onset", "两天前开始", "两天前开始")]),
            cmd("ask_question", question_id="trajectory"),
        ]
        self.helper.submit("两天前开始", action=reply.action_id)
        self.assertIsNone(self.context()["observation_completion"])

    def test_clarifying_location_keeps_it_in_the_graphical_task_without_associated_fact(
        self,
    ):
        offered = self.helper.offer()
        question = "能指出最明显的位置吗？说不清可跳过。"
        self.backend.commands = [
            cmd("clarify", topic="symptom_location", message=question)
        ]
        reply = self.helper.submit("可能是右太阳穴附近疼，具体位置不确定")
        self.assertEqual(reply.kind, "self_check")
        self.assertEqual(reply.task["clarification"], question)
        self.assertEqual(reply.task["interaction_id"], offered.task["interaction_id"])
        self.assertNotIn("symptom_location", self.state.facts)
        self.assertNotIn("associated_symptoms", self.state.facts)
        self.assertIsNone(self.state.active_question)

    def test_location_clarification_requires_a_current_task_and_remains_bounded(self):
        self.helper.offer()
        self.backend.commands = [
            cmd("clarify", topic="symptom_location", message="位置在哪里？")
        ]
        reply = self.helper.submit("位置说不清")
        self.assertEqual(reply.kind, "self_check")
        self.backend.commands = [
            cmd("clarify", topic="symptom_location", message="最明显的不适在哪里？")
        ]
        reply = self.helper.submit("还是说不清")
        self.backend.commands = [
            cmd("clarify", topic="symptom_location", message="再说一次位置？"),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.helper.submit("不知道")
        self.assertEqual(self.state.followup_counts["symptom_location"], 2)
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        reply = self.helper.submit("跳过", "skip", reply.action_id)
        self.backend.commands = [
            cmd("clarify", topic="symptom_location", message="位置在哪里？"),
            cmd("ask_question", question_id="onset"),
        ]
        self.helper.submit("其他情况")
        self.assertIsNone(self.state.self_check_conversation)
        self.assertEqual(self.state.followup_counts["symptom_location"], 2)

    def test_a_later_correction_does_not_present_invalidated_completion_as_current(
        self,
    ):
        self.helper.offer()
        text = "右太阳穴附近疼；更正主诉为咳嗽"
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="右太阳穴附近疼",
            )
        ]
        proposed = self.helper.submit(text)
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("chief_complaint", "咳嗽", "咳嗽", correction=True)],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.helper.submit(
            "核对部位", proposed.task["result_proposal"]["choice"], proposed.action_id
        )
        context = json.loads(self.backend.calls[-1][-1]["content"])["tool_result"][
            "case"
        ]
        self.assertFalse(context["observation_completion"]["result_is_current"])
        self.assertNotIn("symptom_location", context["facts"])

    def test_preserved_previous_reading_is_not_reported_as_newly_recorded(self):
        from tests.test_repeated_observations import measured_case, repeat

        engine, backend, state, prior = measured_case(summary=True)
        old = state.facts["temperature_c"].fact_id
        offered = repeat(engine, state, prior)
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("medications_taken", "昨晚用过药物甲", "昨晚用过药物甲", correction=True)
                ],
            ),
            cmd("review_summary"),
        ]
        engine.submit(
            state.session_id,
            UserInput("体温不知道，昨晚用过药物甲", expected_action_id=offered.action_id),
        )
        context = json.loads(backend.calls[-2][1]["content"])["observation_completion"]
        self.assertTrue(context["previous_reading_preserved"])
        self.assertIsNone(context["fact_id"])
        self.assertEqual(context["assertion"], "unknown")
        self.assertFalse(context["result_is_current"])
        self.assertEqual(state.facts["temperature_c"].fact_id, old)

    def test_location_clarification_does_not_grant_fact_write_permission(self):
        self.helper.offer()
        self.backend.commands = [
            cmd("record_facts", facts=[fact("symptom_location", "头部", "头部")]),
            cmd("clarify", topic="symptom_location", message="最明显的位置在哪里？"),
        ]
        reply = self.helper.submit("可能在头部")
        self.assertEqual(reply.kind, "self_check")
        self.assertNotIn("symptom_location", self.state.facts)

    def test_overlong_or_blank_location_questions_do_not_consume_a_followup(self):
        for message in ["位置" * 41, "   "]:
            with self.subTest(message=message):
                self.setUp()
                self.helper.offer()
                self.backend.commands = [
                    cmd("clarify", topic="symptom_location", message=message),
                    cmd("show_self_check", task_id="body_location"),
                ]
                reply = self.helper.submit("位置不知道")
                self.assertEqual(reply.kind, "self_check")
                self.assertEqual(
                    self.state.followup_counts.get("symptom_location", 0), 0
                )


if __name__ == "__main__":
    unittest.main()
