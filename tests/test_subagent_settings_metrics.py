"""Per-kind subagent modes on the settings surface + metrics (lane B2).

Pins the server side of the shadow→advisory per-kind rollout:

- the server assembly feeds MEDAGENT_SUBAGENT_MODE_<KIND> env overrides
  into the coordinator (kind_modes);
- public_settings()["subagents"] reports {"mode", "kinds"} from the
  attached coordinator's live state (env derivation only before one is
  attached), so the settings page cannot drift from the coordinator;
- apply({"subagent_modes": ...}) validates first, then switches the
  shared coordinator in place — no engine recomposition, nothing leaks
  into the LLM settings; modes applied before a coordinator exists are
  parked and replayed when one is wired in; invalid payloads are a 400
  with no state change (the frontend's one-click rollback is just
  "set all four kinds back");
- the follow-up draft trigger gates on mode_for("followup_batch"),
  not the global mode;
- GET /api/subagents/metrics aggregates the coordinator's events per
  kind and is all-zero (200) when nothing has run.
"""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import Event, EventStore
from medagent.reasoning import Hypothesis, ReasoningWorkspace
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer
from medagent.subagents import SubagentCoordinator
from medagent.workspace import Workspace

CHIEF_EVIDENCE = "我咳嗽三天了，还有点嗓子疼"
COUGH_EVIDENCE = "主要是干咳，没有痰"
PAST = "2000-01-01T00:00:00+00:00"
DRAFT_TEXT = "您好，想了解一下您这几天咳嗽恢复得怎么样了？"
ALL_KINDS = ("case_digest", "evidence_lookup", "second_opinion", "followup_batch")
ZERO = {
    "proposed": 0,
    "completed": 0,
    "partial": 0,
    "failed": 0,
    "dropped": 0,
    "tokens": 0,
    "disagreements": 0,
}


def scrub_env():
    """patch.dict payload keeping everything except MEDAGENT_*.

    Clearing the whole environment would also drop no_proxy, and once
    any earlier test has built urllib's global opener with this
    machine's egress proxy configured, localhost requests would be
    proxied and killed instead of bypassing it.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MEDAGENT_")
    }


def report(findings=None, conclusion="小结", level="low", **extra):
    arguments = {
        "conclusion": conclusion,
        "findings": findings or [],
        "uncertainty": {"level": level, "reasons": []},
    }
    arguments.update(extra)
    return {"tool": "subagent_report", "arguments": arguments}


def case_finding(claim, quote, ref="chief_complaint"):
    return {
        "claim": claim,
        "evidence": [{"source_type": "case", "quote": quote, "ref": ref}],
        "confidence": "medium",
    }


class StubBackend:
    """Scripts sub-model replies; optionally records usage events the
    way a real backend does (model.responded under the sub-session id),
    which is what the coordinator's token accounting reads."""

    name = "stub"

    def __init__(self, handler, events=None, usage=None):
        self.handler = handler
        self.events = events
        self.usage = usage
        self.calls = []

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls.append(session_id)
        if self.events is not None and self.usage is not None:
            self.events.append(
                Event(session_id, "model.responded", {"usage": self.usage})
            )
        return self.handler(session_id, messages, cancelled)


def drafting_handler(session_id, messages, cancelled):
    return report(
        findings=[],
        priority="high",
        reason="上次结论提示需要确认恢复情况",
        draft_message=DRAFT_TEXT,
    )


def make_state(session_id="case-1"):
    state = CaseState(session_id=session_id)
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=CHIEF_EVIDENCE,
        )
    )
    state.record(
        Fact(
            key="resp_cough_details",
            value="干咳",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=COUGH_EVIDENCE,
        )
    )
    reasoning = ReasoningWorkspace(version=1)
    reasoning.hypotheses["uri"] = Hypothesis(
        id="uri",
        label="上呼吸道感染",
        category="common_condition",
        prior_band="high",
    )
    state.reasoning = reasoning.to_dict()
    state.transcript = [
        {"role": "user", "content": CHIEF_EVIDENCE},
        {"role": "assistant", "content": "咳嗽是干咳还是有痰？"},
        {"role": "user", "content": COUGH_EVIDENCE},
    ]
    return state


def make_follow_up_state(engine, due_at=PAST):
    state, _reply = engine.create_session()
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=CHIEF_EVIDENCE,
        )
    )
    state.follow_up = {
        "kind": "routine_checkin",
        "status": "scheduled",
        "due_at": due_at,
        "reason": "例行回访确认恢复情况",
        "outcome": None,
    }
    return state


class WorkspaceFixture(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, scrub_env(), clear=True)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runtime = build_runtime(
            backend="demo",
            session_dir=root / "sessions",
            event_path=root / "events.jsonl",
        )
        self.context = self.runtime.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.workspace = Workspace(self.engine, self.events, "demo")

    def tearDown(self):
        self.workspace.close()
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()


class SubagentSettingsTests(WorkspaceFixture):
    def test_public_settings_env_derivation_without_coordinator(self):
        with patch.dict(
            os.environ,
            {
                "MEDAGENT_SUBAGENT_MODE": "shadow",
                "MEDAGENT_SUBAGENT_MODE_SECOND_OPINION": "advisory",
                "MEDAGENT_SUBAGENT_MODE_CASE_DIGEST": "bogus",
            },
        ):
            subagents = self.workspace.public_settings()["subagents"]
        self.assertEqual(subagents["mode"], "shadow")
        self.assertEqual(
            subagents["kinds"],
            {
                "case_digest": "shadow",  # invalid override falls back
                "evidence_lookup": "shadow",
                "second_opinion": "advisory",
                "followup_batch": "shadow",
            },
        )

    def test_apply_subagent_modes_switches_attached_coordinator(self):
        backend = StubBackend(drafting_handler)
        coordinator = SubagentCoordinator(self.events, backend, mode="shadow")
        self.workspace.subagent_coordinator = coordinator
        engine_before = self.workspace.engine

        result = self.workspace.apply(
            {"subagent_modes": {"second_opinion": "advisory"}}
        )
        # The response reflects the new mode immediately...
        self.assertEqual(result["subagents"]["mode"], "shadow")
        self.assertEqual(result["subagents"]["kinds"]["second_opinion"], "advisory")
        self.assertEqual(result["subagents"]["kinds"]["case_digest"], "shadow")
        # ...and the shared coordinator agrees (mode_for is the truth).
        self.assertEqual(coordinator.mode_for("second_opinion"), "advisory")
        self.assertEqual(coordinator.mode_for("case_digest"), "shadow")
        # In-place switch: no engine recomposition happened.
        self.assertIs(self.workspace.engine, engine_before)
        changed = [
            event
            for event in self.events.for_session("")
            if event.event_type == "subagent.modes_changed"
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].payload["kinds"]["second_opinion"], "advisory")

        # Combined with an LLM payload: modes still land, and the key
        # never leaks into the persisted LLM settings.
        result = self.workspace.apply(
            {"backend": "demo", "subagent_modes": {"case_digest": "advisory"}}
        )
        self.assertEqual(result["subagents"]["kinds"]["case_digest"], "advisory")
        self.assertEqual(coordinator.mode_for("case_digest"), "advisory")
        self.assertNotIn("subagent_modes", self.workspace.settings)

    def test_apply_invalid_subagent_modes_rejected_without_state_change(self):
        backend = StubBackend(drafting_handler)
        coordinator = SubagentCoordinator(self.events, backend, mode="shadow")
        self.workspace.subagent_coordinator = coordinator
        for payload in (
            {"subagent_modes": {"unknown_kind": "advisory"}},
            {"subagent_modes": {"case_digest": "loud"}},
            {"subagent_modes": "advisory"},
        ):
            with self.assertRaises(ValueError, msg=str(payload)):
                self.workspace.apply(payload)
        self.assertEqual(coordinator.kind_modes, {})
        kinds = self.workspace.public_settings()["subagents"]["kinds"]
        self.assertEqual(kinds, {kind: "shadow" for kind in ALL_KINDS})

    def test_modes_parked_until_coordinator_attached(self):
        # No coordinator yet: the switch is accepted and reported...
        result = self.workspace.apply(
            {"subagent_modes": {"followup_batch": "advisory"}}
        )
        self.assertEqual(result["subagents"]["kinds"]["followup_batch"], "advisory")
        # ...and replayed onto the coordinator the moment a composition
        # wires it in.
        backend = StubBackend(drafting_handler)
        coordinator = SubagentCoordinator(self.events, backend, mode="off")
        self.workspace.subagent_coordinator = coordinator
        self.assertEqual(coordinator.mode_for("followup_batch"), "off")
        self.workspace.apply({"backend": "demo"})
        self.assertEqual(coordinator.mode_for("followup_batch"), "advisory")
        reported = self.workspace.public_settings()["subagents"]
        self.assertEqual(reported["mode"], "off")
        self.assertEqual(reported["kinds"]["followup_batch"], "advisory")

    def test_server_assembly_passes_env_kind_modes(self):
        with patch.dict(
            os.environ,
            {
                "MEDAGENT_SUBAGENT_MODE": "shadow",
                "MEDAGENT_SUBAGENT_MODE_EVIDENCE_LOOKUP": "advisory",
            },
        ):
            server = MedicalAgentServer(
                ("127.0.0.1", 0), Handler, self.engine, self.events, "demo"
            )
            try:
                self.assertEqual(server.subagents.mode, "shadow")
                self.assertEqual(
                    server.subagents.mode_for("evidence_lookup"), "advisory"
                )
                self.assertEqual(
                    server.subagents.mode_for("case_digest"), "shadow"
                )
                reported = server.workspace.public_settings()["subagents"]
                self.assertEqual(reported["mode"], "shadow")
                self.assertEqual(reported["kinds"]["evidence_lookup"], "advisory")
            finally:
                server.server_close()


class FollowUpDraftGateTests(WorkspaceFixture):
    def _join_batch(self):
        thread = self.workspace._draft_batch_thread
        self.assertIsNotNone(thread)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())

    def test_shadow_global_but_followup_advisory_still_drafts(self):
        state = make_follow_up_state(self.engine)
        backend = StubBackend(drafting_handler)
        coordinator = SubagentCoordinator(
            self.events,
            backend,
            mode="shadow",
            kind_modes={"followup_batch": "advisory"},
        )
        self.workspace.subagent_coordinator = coordinator
        queued = self.workspace.maybe_schedule_followup_drafts()
        self.assertEqual(queued, 1)
        self._join_batch()
        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["session_id"], state.session_id)
        self.assertEqual(items[0]["draft"]["draft_message"], DRAFT_TEXT)

    def test_advisory_global_but_followup_shadow_or_off_never_drafts(self):
        make_follow_up_state(self.engine)
        for kind_mode in ("shadow", "off"):
            with self.subTest(kind_mode=kind_mode):
                backend = StubBackend(drafting_handler)
                coordinator = SubagentCoordinator(
                    self.events,
                    backend,
                    mode="advisory",
                    kind_modes={"followup_batch": kind_mode},
                )
                self.workspace.subagent_coordinator = coordinator
                self.assertEqual(
                    self.workspace.maybe_schedule_followup_drafts(), 0
                )
                self.assertIsNone(self.workspace._draft_batch_thread)
                self.assertEqual(backend.calls, [])
        items = self.workspace.follow_ups()
        self.assertEqual(len(items), 1)
        self.assertNotIn("draft", items[0])


class SubagentMetricsHttpTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, scrub_env(), clear=True)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runtime = build_runtime(
            backend="demo",
            session_dir=root / "sessions",
            event_path=root / "events.jsonl",
        )
        self.context = self.runtime.start()
        self.server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            self.context.services.require("consultation"),
            self.context.services.require("events"),
            "demo",
        )
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.events = self.context.services.require("events")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()

    def _call(self, method, path, body=None):
        request = Request(self.base + path, method=method)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, data=data, timeout=15) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            return error.code, json.loads(error.read().decode())

    def test_metrics_endpoint_is_all_zero_without_events(self):
        status, payload = self._call("GET", "/api/subagents/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload["kinds"]), set(ALL_KINDS))
        for kind in ALL_KINDS:
            self.assertEqual(payload["kinds"][kind], ZERO)
        self.assertTrue(payload["generated_at"])

    def test_metrics_counts_scripted_spawns(self):
        usage = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        state = make_state()
        proposal = {
            "task_kind": "evidence_lookup",
            "objective": "测试",
            "branches": [{"branch_id": "b1", "objective": "核对证据", "scope": {}}],
        }
        good = SubagentCoordinator(
            self.events,
            StubBackend(
                lambda sid, m, c: report(
                    findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)]
                ),
                events=self.events,
                usage=usage,
            ),
            mode="advisory",
        )
        self.assertTrue(good.run_spawn(state, proposal, session_id=state.session_id)["ok"])
        # Fabricated quote: the claim is dropped by validation
        # (dropped_claims=1) and the branch settles
        # "rejected_by_validation", which the aggregate folds into
        # failed.
        bad = SubagentCoordinator(
            self.events,
            StubBackend(
                lambda sid, m, c: report(
                    findings=[case_finding("咳嗽已经两周", "我咳嗽两周了，一直没好")]
                ),
                events=self.events,
                usage=usage,
            ),
            mode="advisory",
        )
        self.assertTrue(bad.run_spawn(state, proposal, session_id=state.session_id)["ok"])
        # Mirror the producer's real payload shape (agent.py emits
        # subagent.disagreement with task_kind at the §5.7.2 flag site);
        # the aggregator counts the family when it appears.
        self.events.append(
            Event(state.session_id, "subagent.disagreement", {"task_kind": "second_opinion"})
        )

        status, payload = self._call("GET", "/api/subagents/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["kinds"]["evidence_lookup"],
            {
                "proposed": 2,
                "completed": 1,
                "partial": 0,
                "failed": 1,
                "dropped": 1,
                "tokens": 300,
                "disagreements": 0,
            },
        )
        self.assertEqual(payload["kinds"]["second_opinion"]["disagreements"], 1)
        self.assertEqual(payload["kinds"]["case_digest"], ZERO)
        self.assertEqual(payload["kinds"]["followup_batch"], ZERO)

    def test_settings_endpoint_reports_kinds_and_switches_modes(self):
        status, payload = self._call("GET", "/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(payload["subagents"]["mode"], "off")
        self.assertEqual(set(payload["subagents"]["kinds"]), set(ALL_KINDS))

        status, _ = self._call(
            "POST", "/api/settings", {"subagent_modes": {"bogus": "advisory"}}
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.server.subagents.kind_modes, {})

        status, payload = self._call(
            "POST",
            "/api/settings",
            {"subagent_modes": {"second_opinion": "advisory"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["subagents"]["kinds"]["second_opinion"], "advisory")
        self.assertEqual(
            self.server.subagents.mode_for("second_opinion"), "advisory"
        )
        # One-click rollback shape: all four kinds set explicitly.
        status, payload = self._call(
            "POST",
            "/api/settings",
            {"subagent_modes": {kind: "shadow" for kind in ALL_KINDS}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["subagents"]["kinds"], {kind: "shadow" for kind in ALL_KINDS}
        )


if __name__ == "__main__":
    unittest.main()
