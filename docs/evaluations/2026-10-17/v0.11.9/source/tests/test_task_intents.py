import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Assertion, ObservationQuality
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.task_intents import task_intent
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


class TaskIntentTests(unittest.TestCase):
    def setUp(self):
        self.backend, self.events, content = (
            ScriptBackend(),
            EventStore(),
            ContentRepository(),
        )
        self.engine = AdaptiveConsultationEngine(
            content=content,
            events=self.events,
            triage=TriageService(),
            self_checks=SelfCheckService(content.registry),
            ranker=OrderedQuestionRanker(),
            backend=self.backend,
        )
        self.state, self.reply = self.engine.create_session()

    def send(self, text, choice=None):
        self.reply = self.engine.submit(
            self.state.session_id, UserInput(text, choice, self.reply.action_id)
        )
        return self.reply

    def request(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("chief_complaint", "发烧", "发烧")]),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.send("发烧，想按图测一下腋温")

    def test_declarative_request_is_retained_when_the_model_omits_the_request_tool(
        self,
    ):
        self.request()
        self.assertEqual(len(self.state.pending_self_checks), 1)
        calls = len(self.backend.calls)
        reply = self.send("这些危险情况都没有", "no")
        self.assertEqual(reply.task["id"], "axillary_temperature")
        self.assertEqual(len(self.backend.calls), calls)

    def test_cancel_pending_request_marks_not_done_and_does_not_complete_screening(
        self,
    ):
        self.request()
        self.backend.commands = [cmd("ask_question", question_id="emergency_screen")]
        self.send("先不测体温")
        self.assertEqual(self.state.pending_self_checks, [])
        self.assertIsNone(self.state.active_task)
        self.assertNotIn("emergency_screen", self.state.facts)
        self.assertEqual(self.state.facts["temperature_c"].assertion, Assertion.UNKNOWN)
        self.assertEqual(
            self.state.facts["temperature_c"].quality, ObservationQuality.NOT_DONE
        )

    def test_cancel_active_task_and_renew_explicitly(self):
        self.request()
        self.send("这些危险情况都没有", "no")
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.send("取消测温")
        skipped = self.state.facts["temperature_c"]
        self.assertIsNone(self.state.active_task)
        self.backend.commands = [cmd("inspect_case")]
        reply = self.send("现在想测体温")
        self.assertEqual(reply.task["id"], "axillary_temperature")
        self.assertNotIn("temperature_c", self.state.facts)
        self.assertIn(skipped, self.state.history)

    def test_cancellation_cannot_retract_an_existing_reading(self):
        self.request()
        self.send("这些危险情况都没有", "no")
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.send("38.2℃")
        reading = self.state.facts["temperature_c"]
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.send("取消测温")
        self.assertIs(self.state.facts["temperature_c"], reading)
        self.assertEqual(reading.value, 38.2)

    def test_negative_request_is_not_queued_from_a_substring(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("chief_complaint", "发烧", "发烧")]),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        self.send("发烧，我不想按图测一下腋温")
        self.assertEqual(self.state.pending_self_checks, [])
        self.assertEqual(
            self.state.facts["temperature_c"].quality, ObservationQuality.NOT_DONE
        )

    def test_latest_explicit_preference_wins_and_negated_cancellation_is_not_a_decline(
        self,
    ):
        requests, cancellations = ["想测体温"], ["先不测体温", "不想测体温"]
        examples = {
            "先不测体温，后来想测体温": ("request", "想测体温"),
            "想测体温，后来不想测体温": ("cancel", "不想测体温"),
            "不是不想测体温": (None, None),
            "我不想测体温": ("cancel", "不想测体温"),
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(task_intent(text, requests, cancellations), expected)


if __name__ == "__main__":
    unittest.main()
