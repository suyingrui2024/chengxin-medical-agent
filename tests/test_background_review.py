import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from medagent.agent import AdaptiveConsultationEngine
from medagent.background_review import (
    BackgroundReviewCoordinator,
    ReviewJob,
    validate_review,
)
from medagent.decision_wait import DecisionInterrupted
from medagent.domain import RiskLevel
from medagent.events import Event, EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.server import Handler, MedicalAgentServer
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


class ReleasableReviewBackend:
    """A DSH-shaped backend the test releases by hand (D2/D4 probes)."""

    name = "dsh"

    def __init__(self, command=None):
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
        self.started = threading.Event()
        self.release = threading.Event()

    def decide_cancellable(self, session_id, messages, cancelled):
        self.started.set()
        self.release.wait(5)
        return self.command


class CancellingReviewBackend:
    """Mimics the real DSH backend: cancellation surfaces as an error."""

    name = "dsh"

    def decide_cancellable(self, session_id, messages, cancelled):
        cancelled.wait(5)
        raise DecisionInterrupted()


class IntegratedReviewBackend(ReviewBackend):
    def __init__(self):
        super().__init__()
        self.foreground_calls = 0

    def decide_cancellable(self, session_id, messages, cancelled):
        if messages[0]["content"].startswith("You are an asynchronous"):
            return super().decide_cancellable(session_id, messages, cancelled)
        self.foreground_calls += 1
        if self.foreground_calls == 1:
            return {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "chief_complaint",
                            "value": "咳嗽",
                            "assertion": "present",
                            "evidence": "咳嗽",
                        }
                    ]
                },
            }
        return {"tool": "ask_question", "arguments": {"question_id": "chief_complaint"}}


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


def schedule_material_review(coordinator, engine, events, state):
    """Mark the current foreground reply as a material case change for unit tests."""
    turn_id = next(
        (
            event.turn_id
            for event in reversed(events.for_session(state.session_id))
            if event.event_type == "turn.agent_reply"
        ),
        None,
    )
    if turn_id:
        events.append(
            Event(
                state.session_id,
                "fact.recorded",
                {"fact_key": "test_material_change"},
                turn_id=turn_id,
            )
        )
    coordinator.schedule(engine, state)


class BackgroundReviewTests(unittest.TestCase):
    def test_shadow_review_is_private_from_snapshot_and_does_not_change_next_turn(self):
        events = EventStore()
        engine = review_engine(ReviewBackend(), events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            wait_for(lambda: state.background_review and state.background_review["status"] == "completed")
            self.assertEqual(state.to_dict()["background_review"]["actionable_count"], 2)
            self.assertEqual(state.to_dict()["background_review"]["mode"], "shadow")
            self.assertNotIn("advice", state.to_dict()["background_review"])
            self.assertNotIn("background_review", engine._context(state, "下一条信息"))
            completed = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.completed"
            ]
            self.assertEqual(completed[-1].payload["counts"]["suggested_questions"], 1)
            self.assertEqual(completed[-1].payload["mode"], "shadow")
            self.assertIsInstance(completed[-1].payload["duration_ms"], int)
        finally:
            coordinator.close()

    def test_advisory_mode_can_expose_only_current_review_to_the_next_turn(self):
        events = EventStore()
        engine = review_engine(ReviewBackend(), events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events, mode="advisory")
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            wait_for(lambda: state.background_review and state.background_review["status"] == "completed")
            self.assertEqual(
                engine._context(state, "下一条信息")["background_review"]["missing_information"],
                ["症状开始时间"],
            )
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
                schedule_material_review(coordinator, engine, events, state)
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

    def test_review_deadline_marks_the_advisor_unavailable(self):
        events = EventStore()
        backend = ReviewBackend(pause=True)
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events, deadline_seconds=0.02)
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            wait_for(
                lambda: state.background_review
                and state.background_review["status"] == "unavailable"
            )
            unavailable = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.unavailable"
            ]
            self.assertEqual(unavailable[-1].payload["error_type"], "deadline")
        finally:
            coordinator.close()

    def test_snapshot_only_includes_patient_confirmed_report_items(self):
        # D1 (healthcheck C): a draft must neither be mislabelled as
        # confirmed nor displace a confirmed item from the last-20 cut.
        events = EventStore()
        engine = review_engine(ReviewBackend(), events)
        state, _ = engine.create_session()
        state.report_items = [
            {
                "name": "血红蛋白",
                "reported_value": "130",
                "status": "confirmed_by_patient",
            }
        ] + [
            {"name": f"草稿{i}", "reported_value": "1", "status": "draft"}
            for i in range(20)
        ]
        state.report_items.append({"name": "无状态项", "reported_value": "2"})
        coordinator = BackgroundReviewCoordinator(events)
        try:
            snapshot = coordinator._snapshot(
                state,
                ReviewJob("job-1", None, state.session_id, state.revision, None),
            )
        finally:
            coordinator.close()
        self.assertEqual(
            [item["name"] for item in snapshot["patient_confirmed_report_items"]],
            ["血红蛋白"],
        )

    def test_stale_success_discards_and_clears_running_state(self):
        # D2 (healthcheck C, R05): a revision bump with no invalidate
        # used to leave a stale "running" behind after the discard.
        events = EventStore()
        backend = ReleasableReviewBackend()
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            self.assertTrue(backend.started.wait(1))
            with engine._session_locks[state.session_id]:
                state.revision += 1
            backend.release.set()
            wait_for(lambda: state.background_review is None)
            discarded = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.discarded"
            ]
            self.assertEqual(discarded[-1].payload["reason"], "stale_case")
            self.assertFalse(
                any(
                    event.event_type == "advisor.completed"
                    for event in events.for_session(state.session_id)
                )
            )
        finally:
            backend.release.set()
            coordinator.close()

    def test_timeout_raised_as_interruption_is_still_labelled_deadline(self):
        # D3 (healthcheck C, R08a): the DSH backend raises
        # DecisionInterrupted when cancelled; a timed-out job must
        # still be reported as a deadline failure, not by exception name.
        events = EventStore()
        backend = CancellingReviewBackend()
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events, deadline_seconds=0.02)
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            wait_for(
                lambda: state.background_review
                and state.background_review["status"] == "unavailable"
            )
            unavailable = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.unavailable"
            ]
            self.assertEqual(unavailable[-1].payload["error_type"], "deadline")
            self.assertNotIn("advice", state.background_review)
        finally:
            coordinator.close()

    def test_completion_rechecks_phase_and_risk_before_landing(self):
        # D4 (healthcheck C): escalation to urgent mid-review, at the
        # same revision, must discard at completion — not land advice
        # that the next turn could then serve.
        events = EventStore()
        backend = ReleasableReviewBackend()
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events, mode="advisory")
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            self.assertTrue(backend.started.wait(1))
            with engine._session_locks[state.session_id]:
                state.risk.level = RiskLevel.URGENT
            backend.release.set()
            wait_for(lambda: state.background_review is None)
            discarded = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.discarded"
            ]
            self.assertEqual(
                discarded[-1].payload["reason"], "phase_or_risk_changed"
            )
            self.assertFalse(
                any(
                    event.event_type == "advisor.completed"
                    for event in events.for_session(state.session_id)
                )
            )
            self.assertNotIn(
                "background_review", engine._context(state, "下一条信息")
            )
        finally:
            backend.release.set()
            coordinator.close()

    def test_completed_event_leaves_a_drop_trace(self):
        # D5 (healthcheck C, R09): counts alone cannot tell "model
        # gave 4" from "model gave 6, 2 dropped" — dropped_counts must.
        events = EventStore()
        backend = ReviewBackend(
            command={
                "tool": "inspect_case",
                "arguments": {
                    "review": {
                        "contradictions": ["甲", "乙", "丙", "丁", "戊", "甲"],
                        "missing_information": [123, ""],
                        "suggested_questions": ["何时开始的？"],
                    }
                },
            }
        )
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                schedule_material_review(coordinator, engine, events, state)
            wait_for(
                lambda: state.background_review
                and state.background_review["status"] == "completed"
            )
            completed = [
                event
                for event in events.for_session(state.session_id)
                if event.event_type == "advisor.completed"
            ]
            self.assertEqual(
                completed[-1].payload["counts"],
                {
                    "contradictions": 4,
                    "missing_information": 0,
                    "suggested_questions": 1,
                },
            )
            self.assertEqual(
                completed[-1].payload["dropped_counts"],
                {
                    "contradictions": 2,
                    "missing_information": 2,
                    "suggested_questions": 0,
                },
            )
            self.assertEqual(
                state.background_review["advice"]["contradictions"],
                ["甲", "乙", "丙", "丁"],
            )
        finally:
            coordinator.close()

    def test_capacity_limit_skips_a_second_session_without_blocking_it(self):
        events = EventStore()
        first_backend = ReviewBackend(pause=True)
        second_backend = ReviewBackend()
        first_engine = review_engine(first_backend, events)
        second_engine = review_engine(second_backend, events)
        first, _ = first_engine.create_session()
        second, _ = second_engine.create_session()
        coordinator = BackgroundReviewCoordinator(events, maximum=1)
        try:
            with first_engine._session_locks[first.session_id]:
                schedule_material_review(coordinator, first_engine, events, first)
            self.assertTrue(first_backend.started.wait(1))
            with second_engine._session_locks[second.session_id]:
                schedule_material_review(coordinator, second_engine, events, second)
            wait_for(
                lambda: second.background_review
                and second.background_review["status"] == "unavailable"
            )
            self.assertFalse(second_backend.started.is_set())
            unavailable = [
                event
                for event in events.for_session(second.session_id)
                if event.event_type == "advisor.unavailable"
            ]
            self.assertEqual(unavailable[-1].payload["error_type"], "capacity")
        finally:
            coordinator.close()

    def test_direct_model_mode_never_starts_a_dsh_review(self):
        events = EventStore()
        backend = ReviewBackend()
        backend.name = "compatible"
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            with engine._session_locks[state.session_id]:
                coordinator.schedule(engine, state)
            self.assertIsNone(state.background_review)
            self.assertFalse(backend.started.is_set())
        finally:
            coordinator.close()

    def test_non_material_foreground_turn_is_skipped(self):
        events = EventStore()
        backend = ReviewBackend()
        engine = review_engine(backend, events)
        state, _ = engine.create_session()
        coordinator = BackgroundReviewCoordinator(events)
        try:
            events.append(
                Event(
                    state.session_id,
                    "turn.agent_reply",
                    {"reply": {}},
                    turn_id="non-material-turn",
                )
            )
            with engine._session_locks[state.session_id]:
                coordinator.schedule(engine, state)
            self.assertIsNone(state.background_review)
            self.assertFalse(backend.started.is_set())
            self.assertEqual(
                events.for_session(state.session_id)[-1].event_type, "advisor.skipped"
            )
        finally:
            coordinator.close()

    def test_server_submits_then_completes_a_private_background_review(self):
        events = EventStore()
        engine = review_engine(IntegratedReviewBackend(), events)
        server = MedicalAgentServer(("127.0.0.1", 0), Handler, engine, events, "dsh")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(path, payload):
            request = Request(
                base + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=2) as response:
                return json.loads(response.read())

        try:
            created = post("/api/sessions", {})
            session_id = created["session_id"]
            reply = post(
                f"/api/sessions/{session_id}/messages",
                {"text": "我自己，最近有些咳嗽"},
            )
            self.assertEqual(reply["state"]["background_review"]["status"], "queued")
            state = engine.state(session_id)
            wait_for(
                lambda: state.background_review
                and state.background_review["status"] == "completed"
            )
            with urlopen(base + f"/api/sessions/{session_id}/snapshot", timeout=2) as response:
                snapshot = json.loads(response.read())
            self.assertEqual(snapshot["state"]["background_review"]["status"], "completed")
            self.assertEqual(snapshot["state"]["background_review"]["mode"], "shadow")
            self.assertNotIn("advice", snapshot["state"]["background_review"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_server_can_disable_reviews_and_forget_a_session(self):
        events = EventStore()
        engine = review_engine(ReviewBackend(), events)
        server = MedicalAgentServer(("127.0.0.1", 0), Handler, engine, events, "dsh")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def request(path, method="GET", payload=None):
            data = json.dumps(payload).encode() if payload is not None else None
            headers = {"Content-Type": "application/json"} if data else {}
            with urlopen(
                Request(base + path, data=data, headers=headers, method=method), timeout=2
            ) as response:
                return json.loads(response.read())

        try:
            created = request("/api/sessions", "POST", {})
            session_id = created["session_id"]
            self.assertTrue(request("/api/settings")["dsh_background_review_enabled"])
            self.assertEqual(
                request("/api/reviews/enabled", "POST", {"enabled": False}),
                {"enabled": False},
            )
            self.assertFalse(server.reviews.enabled_for_process)
            self.assertEqual(
                request(f"/api/sessions/{session_id}", "DELETE"), {"deleted": True}
            )
            self.assertEqual(events.for_session(session_id), [])
            with self.assertRaises(HTTPError) as error:
                request(f"/api/sessions/{session_id}")
            self.assertEqual(error.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
