"""Task occurrence identity scopes UI drafts without changing clinical records."""
from copy import deepcopy
import unittest

from medagent.consultation import UserInput
from medagent.domain import AgentReply, Phase
from medagent.events import Event
from medagent.task_interactions import task_with_interaction
from tests import test_consultation as fixture


class TaskInteractionTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.ConsultationTestCase()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.engine, self.state = self.helper.engine, self.helper.state

    def task(self, complaint="头疼"):
        answers = {
            "consult_for": ("我自己", "self"),
            "age": ("32岁", None),
            "chief_complaint": (complaint, None),
            "emergency_screen": ("都没有", "no"),
            "onset": ("昨天逐渐出现", None),
            "trajectory": ("变化不大", "stable"),
            "severity": ("3分", None),
            "associated_symptoms": ("没有", None),
            "resp_breathing": ("能正常呼吸", None),
            "resp_cough_details": ("干咳", None),
            "resp_intake": ("能正常喝水吃饭", None),
            "medications_taken": ("没有", None),
            "allergies": ("没有", None),
            "relevant_history": ("没有", None),
            "patient_goal": ("整理信息", None),
        }
        for _ in range(30):
            reply = self.helper.reply
            if reply.kind == "self_check":
                return reply
            self.assertIn(reply.question_id, answers)
            self.helper.answer(*answers[reply.question_id])
        self.fail("Expected a registered task")

    def record(self, reply):
        self.engine._record_reply(self.state, reply, "test-turn", "metadata check")
        return reply

    def test_first_offer_identity_is_in_reply_and_projection_without_mutating_catalog(
        self,
    ):
        reply = self.task()
        offered = [
            e
            for e in self.engine.events.for_session(self.state.session_id)
            if e.event_type == "self_check.offered"
        ][-1]
        self.assertEqual(reply.task["interaction_id"], "offer:" + offered.event_id)
        self.assertNotIn("interaction_id", self.engine.content.tasks[reply.task["id"]])
        blocks = reply.to_dict()["presentation"]["blocks"]
        self.assertEqual(
            next(b["data"] for b in blocks if b["type"] == "self_check")[
                "interaction_id"
            ],
            reply.task["interaction_id"],
        )

    def test_actual_invalid_temperature_feedback_keeps_the_same_offer_identity(self):
        first = self.task("发热")
        self.assertEqual(first.task["id"], "axillary_temperature")
        before = deepcopy(self.state.to_dict()["facts"])
        reply = self.helper.answer("abc")
        self.assertTrue(reply.input_error)
        self.assertNotEqual(reply.action_id, first.action_id)
        self.assertEqual(reply.task["interaction_id"], first.task["interaction_id"])
        self.assertEqual(self.state.to_dict()["facts"], before)

    def test_new_offer_of_same_task_gets_a_new_identity_without_result_writes(self):
        first = self.task()
        before = deepcopy(self.state.to_dict()["facts"])
        second = self.record(self.engine._offer_task(self.state, "头疼", "body_location"))
        self.assertNotEqual(second.task["interaction_id"], first.task["interaction_id"])
        self.assertEqual(self.state.to_dict()["facts"], before)

    def test_host_continuation_can_restore_an_earlier_offer(self):
        first = self.task()
        self.record(self.engine._offer_task(self.state, "头疼", "body_location"))
        restored = self.record(deepcopy(first))
        self.assertEqual(restored.task["interaction_id"], first.task["interaction_id"])

    def test_invalid_feedback_after_restoring_an_earlier_offer_keeps_the_restored_identity(
        self,
    ):
        first = self.task("发热")
        self.record(self.engine._offer_task(self.state, "发热", "axillary_temperature"))
        self.record(deepcopy(first))
        invalid = self.helper.answer("abc")
        self.assertTrue(invalid.input_error)
        self.assertEqual(invalid.task["interaction_id"], first.task["interaction_id"])

    def test_repeat_uses_its_registered_attempt_and_feedback_keeps_it(self):
        first = self.task("发热")
        self.helper.answer("37.2℃")
        repeat = self.engine._request_repeat(
            self.state, "axillary_temperature", "想再测一次腋温", "repeat-request"
        )
        self.record(repeat)
        attempt = self.state.observation_attempts[-1]["id"]
        self.assertEqual(repeat.task["interaction_id"], "repeat:" + attempt)
        self.assertNotEqual(repeat.task["interaction_id"], first.task["interaction_id"])
        invalid = self.engine.submit(self.state.session_id, UserInput("abc"))
        self.assertTrue(invalid.input_error)
        self.assertEqual(invalid.task["interaction_id"], repeat.task["interaction_id"])
        self.assertEqual(self.state.get("temperature_c"), 37.2)

    def test_other_session_events_and_unknown_incoming_ids_are_not_accepted(self):
        task = {"id": "body_location", "interaction_id": "offer:foreign"}
        foreign = Event(
            "other-session",
            "self_check.offered",
            {"task_id": "body_location"},
            event_id="foreign",
        )
        result = task_with_interaction(
            task, self.state.session_id, "current-action", [foreign]
        )
        self.assertEqual(result["interaction_id"], "action:current-action")
        self.assertEqual(task["interaction_id"], "offer:foreign")

    def test_unknown_repeat_cannot_reuse_an_initial_offer(self):
        event = Event(
            self.state.session_id,
            "self_check.offered",
            {"task_id": "axillary_temperature"},
        )
        result = task_with_interaction(
            {"id": "axillary_temperature", "repeat": {"attempt_id": "unknown"}},
            self.state.session_id,
            "action",
            [event],
        )
        self.assertEqual(result["interaction_id"], "action:action")

    def test_legacy_task_without_offer_falls_back_to_the_specific_action(self):
        first = self.record(
            AgentReply(
                "task",
                Phase.OBSERVATION,
                kind="self_check",
                task=self.engine.content.tasks["body_location"],
            )
        )
        second = self.record(
            AgentReply(
                "task",
                Phase.OBSERVATION,
                kind="self_check",
                task=self.engine.content.tasks["body_location"],
            )
        )
        self.assertNotEqual(first.task["interaction_id"], second.task["interaction_id"])
        self.assertNotIn("interaction_id", self.engine.content.tasks["body_location"])
