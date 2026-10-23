"""Server-side follow-up draft wiring (design §5.7.4) + review deadline.

Before this, SubagentCoordinator.run_followup_batch had exactly one
caller — an offline script that wrote drafts into its own process's
in-memory dict and exited — so the server's follow-up list never carried
a draft and the UI's read-only draft block was unreachable in the real
product. These tests pin the in-process loop:

- GET /api/follow-ups (via Workspace.maybe_schedule_followup_drafts)
  queues generation on a background thread when an advisory coordinator
  is attached;
- drafts persist to follow_up_drafts.json in the session dir, are
  reloaded by a fresh Workspace/server over the same data, and are
  never regenerated for a session that already has one;
- off/shadow/missing/raising coordinators leave the list untouched.

The background-review section pins the recalibrated deadline and the
tail-bounded latest-turn scan in BackgroundReviewCoordinator.schedule.
"""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from medagent.background_review import (
    REVIEW_DEADLINE_SECONDS,
    BackgroundReviewCoordinator,
)
from medagent.consultation import UserInput
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import Event, EventStore
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer
from medagent.subagents import SubagentCoordinator
from medagent.workspace import Workspace

PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"
DRAFT_TEXT = "您好，想了解一下您这几天咳嗽恢复得怎么样了？"


def report(findings=None, conclusion="小结", level="low", **extra):
    arguments = {
        "conclusion": conclusion,
        "findings": findings or [],
        "uncertainty": {"level": level, "reasons": []},
    }
    arguments.update(extra)
    return {"tool": "subagent_report", "arguments": arguments}


class StubBackend:
    """Scripts sub-model replies through a handler function."""

    name = "stub"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls.append(session_id)
        return self.handler(session_id, messages, cancelled)


def drafting_handler(session_id, messages, cancelled):
    return report(
        findings=[],
        priority="high",
        reason="上次结论提示需要确认恢复情况",
        draft_message=DRAFT_TEXT,
    )


def make_follow_up_state(engine, due_at=PAST, status="scheduled"):
    state, _reply = engine.create_session()
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="我咳嗽三天了，还有点嗓子疼",
        )
    )
    state.follow_up = {
        "kind": "routine_checkin",
        "status": status,
        "due_at": due_at,
        "reason": "例行回访确认恢复情况",
        "outcome": None,
    }
    return state


class WorkspaceDraftWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session_dir = root / "sessions"
        self.runtime = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
            event_path=root / "events.jsonl",
        )
        self.context = self.runtime.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.workspace = Workspace(self.engine, self.events, "demo")

    def tearDown(self):
        self.runtime.stop()
        self.tmp.cleanup()

    def _attach(self, coordinator):
        self.workspace.subagent_coordinator = coordinator

    def _join_batch(self):
        thread = self.workspace._draft_batch_thread
        self.assertIsNotNone(thread)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_due_follow_up_gets_draft_persisted_and_not_regenerated(self):
        state = make_follow_up_state(self.engine)
        backend = StubBackend(drafting_handler)
        self._attach(SubagentCoordinator(self.events, backend, mode="advisory"))

        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertNotIn("draft", items[0])

        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 1)
        self._join_batch()

        items = self.workspace.follow_ups()
        self.assertEqual(items[0]["draft"]["draft_message"], DRAFT_TEXT)
        self.assertEqual(items[0]["draft"]["priority"], "high")

        drafts_file = self.session_dir / "follow_up_drafts.json"
        self.assertTrue(drafts_file.exists())
        payload = json.loads(drafts_file.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["drafts"][state.session_id]["draft_message"], DRAFT_TEXT
        )

        calls_after_first = len(backend.calls)
        self.assertGreater(calls_after_first, 0)
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 0)
        self.assertEqual(self.workspace.follow_up_drafts_due(), [])
        self.assertEqual(len(backend.calls), calls_after_first)

    def test_drafts_survive_a_fresh_workspace_without_a_coordinator(self):
        state = make_follow_up_state(self.engine)
        backend = StubBackend(drafting_handler)
        self._attach(SubagentCoordinator(self.events, backend, mode="advisory"))
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 1)
        self._join_batch()

        fresh = Workspace(self.engine, self.events, "demo")
        self.assertIsNone(fresh.subagent_coordinator)
        self.assertEqual(fresh.maybe_schedule_followup_drafts(), 0)
        items = fresh.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["draft"]["draft_message"], DRAFT_TEXT)

    def test_off_shadow_and_missing_coordinator_never_draft(self):
        make_follow_up_state(self.engine)
        backend = StubBackend(drafting_handler)

        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 0)

        self._attach(SubagentCoordinator(self.events, backend, mode="off"))
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 0)

        self._attach(SubagentCoordinator(self.events, backend, mode="shadow"))
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 0)

        self.assertEqual(backend.calls, [])
        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertNotIn("draft", items[0])

    def test_not_due_and_completed_plans_are_not_drafted(self):
        make_follow_up_state(self.engine, due_at=FUTURE)
        make_follow_up_state(self.engine, due_at=PAST, status="completed")
        backend = StubBackend(drafting_handler)
        self._attach(SubagentCoordinator(self.events, backend, mode="advisory"))

        self.assertEqual(self.workspace.follow_up_drafts_due(), [])
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 0)
        self.assertEqual(backend.calls, [])
        self.assertEqual(len(self.workspace.follow_ups()), 2)

    def test_raising_coordinator_leaves_the_list_untouched(self):
        make_follow_up_state(self.engine)

        class ExplodingCoordinator:
            mode = "advisory"
            backend = StubBackend(drafting_handler)

            def run_followup_batch(self, items, states):
                raise RuntimeError("boom")

        self._attach(ExplodingCoordinator())
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 1)
        self._join_batch()
        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertNotIn("draft", items[0])

    def test_backend_failure_produces_no_draft_and_no_error(self):
        make_follow_up_state(self.engine)

        def exploding(session_id, messages, cancelled):
            raise RuntimeError("model down")

        backend = StubBackend(exploding)
        self._attach(SubagentCoordinator(self.events, backend, mode="advisory"))
        self.assertEqual(self.workspace.maybe_schedule_followup_drafts(), 1)
        self._join_batch()
        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertNotIn("draft", items[0])

    def test_store_is_idempotent_and_first_draft_wins(self):
        result = {
            "session_id": "s-1",
            "priority": "routine",
            "draft_message": "第一份草稿",
        }
        self.assertEqual(self.workspace.store_follow_up_drafts([result]), 1)
        self.assertEqual(
            self.workspace.store_follow_up_drafts(
                [{**result, "draft_message": "第二份草稿"}]
            ),
            0,
        )
        self.assertEqual(
            self.workspace.follow_up_drafts["s-1"]["draft_message"], "第一份草稿"
        )
        payload = json.loads(
            (self.session_dir / "follow_up_drafts.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            payload["drafts"]["s-1"]["draft_message"], "第一份草稿"
        )


ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("女", "female"),
    "pregnancy_status": ("没有怀孕，月经没有推迟", "not_pregnant"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "gi_details": ("肚脐周围隐痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}


class FollowUpDraftHttpTests(unittest.TestCase):
    """End to end: a real closed case with a due plan, served over HTTP,
    then a full server restart over the same data directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session_dir = root / "sessions"
        self.event_path = root / "events.jsonl"
        self.runtime = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
            event_path=self.event_path,
        )
        self.context = self.runtime.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, reply = self.engine.create_session()
        self._drive_to_confirmed_close(reply)
        # The plan is scheduled 72h out by the engine; make it due and
        # persist so the restart leg restores it as due.
        self.state.follow_up["due_at"] = PAST
        self.engine.persist_state(self.state)
        self.server = self._serve(self.engine, self.events)
        backend = StubBackend(drafting_handler)
        self.backend = backend
        self.server.workspace.subagent_coordinator = SubagentCoordinator(
            self.events, backend, mode="advisory"
        )

    def tearDown(self):
        self._stop_server()
        self.runtime.stop()
        self.tmp.cleanup()

    def _serve(self, engine, events):
        server = MedicalAgentServer(
            ("127.0.0.1", 0), Handler, engine, events, "demo"
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        server._test_worker = worker
        return server

    def _stop_server(self):
        self.server.shutdown()
        self.server._test_worker.join(timeout=5)
        self.server.server_close()

    def _drive_to_confirmed_close(self, reply):
        for _ in range(25):
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    self.state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            text, choice = ANSWERS[reply.question_id]
            reply = self.engine.submit(
                self.state.session_id, UserInput(text, choice)
            )
        else:
            raise AssertionError(f"did not reach summary; last kind={reply.kind}")
        reply = self.engine.submit(
            self.state.session_id, UserInput("确认", "confirm_summary")
        )
        self.assertEqual(reply.kind, "closed")
        self.assertEqual(self.state.follow_up.get("status"), "scheduled")

    def _get_follow_ups(self, server=None):
        server = server or self.server
        connection = http.client.HTTPConnection(
            *server.server_address, timeout=5
        )
        try:
            connection.request("GET", "/api/follow-ups")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            return json.loads(response.read() or b"{}")["follow_ups"]
        finally:
            connection.close()

    def test_list_triggers_draft_and_restart_keeps_it(self):
        items = self._get_follow_ups()
        mine = [
            item
            for item in items
            if item["session_id"] == self.state.session_id
        ]
        self.assertEqual(len(mine), 1)

        thread = self.server.workspace._draft_batch_thread
        self.assertIsNotNone(thread)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertGreater(len(self.backend.calls), 0)

        items = self._get_follow_ups()
        mine = [
            item
            for item in items
            if item["session_id"] == self.state.session_id
        ]
        self.assertEqual(mine[0]["draft"]["draft_message"], DRAFT_TEXT)

        # Full restart over the same data dir, with no coordinator
        # attached: the draft must be served from the durable store.
        self._stop_server()
        self.runtime.stop()
        runtime2 = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
            event_path=self.event_path,
        )
        context2 = runtime2.start()
        server2 = self._serve(
            context2.services.require("consultation"),
            context2.services.require("events"),
        )
        try:
            # The restarted server only has its own env-gated coordinator
            # (mode off, and the demo engine has no backend at all), so
            # nothing could have generated the draft in this process.
            self.assertEqual(
                server2.workspace.subagent_coordinator.mode, "off"
            )
            items = self._get_follow_ups(server2)
            mine = [
                item
                for item in items
                if item["session_id"] == self.state.session_id
            ]
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0]["draft"]["draft_message"], DRAFT_TEXT)
        finally:
            server2.shutdown()
            server2._test_worker.join(timeout=5)
            server2.server_close()
            runtime2.stop()


class BackgroundReviewDeadlineTests(unittest.TestCase):
    def test_default_deadline_fits_the_dsh_bridge_budget(self):
        # The DSH bridge hard timeout is 55s (backends.py); the review
        # deadline must leave a cold start + round trip room yet expire
        # before that cap. The pre-fix value was 8s.
        self.assertGreaterEqual(REVIEW_DEADLINE_SECONDS, 30.0)
        self.assertLess(REVIEW_DEADLINE_SECONDS, 55.0)
        coordinator = BackgroundReviewCoordinator(EventStore())
        self.assertEqual(coordinator._deadline_seconds, REVIEW_DEADLINE_SECONDS)

    def _events(self, spec):
        events = EventStore()
        for session_id, event_type, turn_id in spec:
            events.append(Event(session_id, event_type, {}, turn_id=turn_id))
        return events.for_session("s-1")

    def test_latest_turn_material_before_reply(self):
        events = self._events(
            [
                ("s-1", "fact.recorded", "t1"),
                ("s-1", "turn.agent_reply", "t1"),
            ]
        )
        self.assertEqual(
            BackgroundReviewCoordinator._latest_turn(events), ("t1", True)
        )

    def test_latest_turn_material_after_reply(self):
        # Follow-up scheduling and the review tests themselves append
        # same-turn events after the reply; they still count.
        events = self._events(
            [
                ("s-1", "turn.agent_reply", "t1"),
                ("s-1", "fact.recorded", "t1"),
            ]
        )
        self.assertEqual(
            BackgroundReviewCoordinator._latest_turn(events), ("t1", True)
        )

    def test_latest_turn_ignores_older_turns_material_change(self):
        events = self._events(
            [
                ("s-1", "fact.recorded", "t1"),
                ("s-1", "turn.agent_reply", "t1"),
                ("s-1", "turn.agent_reply", "t2"),
            ]
        )
        self.assertEqual(
            BackgroundReviewCoordinator._latest_turn(events), ("t2", False)
        )

    def test_latest_turn_without_reply_or_with_none_turn(self):
        events = self._events([("s-1", "fact.recorded", "t1")])
        self.assertEqual(
            BackgroundReviewCoordinator._latest_turn(events), (None, False)
        )
        events = self._events([("s-1", "turn.agent_reply", None)])
        self.assertEqual(
            BackgroundReviewCoordinator._latest_turn(events), (None, False)
        )

    def test_schedule_skips_when_latest_turn_changed_nothing(self):
        events = EventStore()
        state = CaseState(session_id="s-9")
        events.append(
            Event("s-9", "fact.recorded", {}, turn_id="t1")
        )
        events.append(Event("s-9", "turn.agent_reply", {}, turn_id="t1"))
        events.append(Event("s-9", "turn.agent_reply", {}, turn_id="t2"))

        class DshBackend:
            name = "dsh"

            def decide_cancellable(self, session_id, messages, cancelled):
                raise AssertionError("must not run for an immaterial turn")

        class Engine:
            backend = DshBackend()
            _session_locks = {"s-9": threading.Lock()}

            def state(self, session_id):
                return state

        coordinator = BackgroundReviewCoordinator(events)
        coordinator.schedule(Engine(), state)
        skipped = [
            event
            for event in events.for_session("s-9")
            if event.event_type == "advisor.skipped"
        ]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(
            skipped[0].payload["reason"], "no_material_case_change"
        )
        self.assertIsNone(state.background_review)


if __name__ == "__main__":
    unittest.main()
