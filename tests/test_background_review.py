import threading
import time
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.background_review import BackgroundReviewCoordinator, validate_review
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class ReviewBackend:
    name = "dsh"

    def __init__(self, command=None, pause=False):
        self.command = command or {
            "tool": "inspect_case",
            "arguments": {
                "review": {
                    "contradictions": [],
                    "missing_information": ["症状开始时间"],
                    "suggested_questions": ["这种不适是何时开始的？"],
                }
            },
        }
        self.pause = pause
        self.started = threading.Event()
        self.cancelled = None

    def decide_cancellable(self, session_id, messages, cancelled):
        self.started.set()
        self.cancelled = cancelled
        if self.pause:
            cancelled.wait(2)
        return self.command


def review_engine(backend, events):
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("background review did not finish")


class BackgroundReviewTests(unittest.TestCase):
    def test_completed_advice_is_private_from_snapshot_and_available_to_next_turn(self):
        events = EventStore()
        engine = review_engine(ReviewBackend(), events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                coordinator.schedule(engine, state)
            wait_for(lambda: state.background_review and state.background_review["status"] == "completed")
            self.assertEqual(state.to_dict()["background_review"]["actionable_count"], 2)
            self.assertNotIn("advice", state.to_dict()["background_review"])
            self.assertEqual(
                engine._context(state, "下一条信息")["background_review"]["missing_information"],
                ["症状开始时间"],
            )
            completed = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.completed"
            ]
            self.assertEqual(completed[-1].payload["counts"]["suggested_questions"], 1)
        finally:
            coordinator.close()

    def test_new_submitted_turn_cancels_the_previous_review_without_mutating_case(self):
        events = EventStore()
        backend = ReviewBackend(pause=True)
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                coordinator.schedule(engine, state)
            self.assertTrue(backend.started.wait(1))
            with engine._session_locks[state.session_id]:
                coordinator.invalidate(state, "new_submitted_message")
            wait_for(lambda: backend.cancelled and backend.cancelled.is_set())
            self.assertIsNone(state.background_review)
            self.assertFalse(
                any(
                    event.event_type == "advisor.completed"
                    for event in events.for_session(state.session_id)
                )
            )
        finally:
            coordinator.close()

    def test_review_schema_rejects_extra_model_output(self):
        with self.assertRaises(ValueError):
            validate_review(
                {
                    "tool": "inspect_case",
                    "arguments": {"review": {}, "hidden_action": "write"},
                }
            )


if __name__ == "__main__":
    unittest.main()
