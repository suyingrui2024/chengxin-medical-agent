"""Server-layer hardening from the assembly audit:

- routes that mutate a case directly (report drafts, report items) must
  persist it, so the change survives a restart of a closed case;
- an unknown /api/sessions/{id}/<subpath> is a 404, not the state;
- POST /api/sessions is idempotent per X-Request-ID;
- /api/health reports durable-layer failure counters;
- an OSError inside any route becomes a structured 500;
- a snapshot GET re-arms education enrichment when the education
  module's predicate says the sheet still needs it.
"""
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from medagent.consultation import UserInput
from medagent.domain import Phase
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer

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

DRAFT_TEXT = "报告日期 2026-10-20\n空腹血糖 4.2 mmol/L 3.9-6.1"


class ServerHardeningTests(unittest.TestCase):
    def setUp(self):
        # Scrub only MEDAGENT_* configuration. Clearing the whole
        # environment would also drop no_proxy, and once any earlier
        # test has built urllib's global opener with this machine's
        # egress proxy configured, localhost requests would be proxied
        # and killed instead of bypassing it.
        scrubbed = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MEDAGENT_")
        }
        self.env = patch.dict(os.environ, scrubbed, clear=True)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session_dir = root / "sessions"
        self.runtime = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
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

    def tearDown(self):
        self.server.shutdown()
        self.worker.join(timeout=2)
        self.server.server_close()
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()

    def request(self, path, method="GET", payload=None, headers=None):
        body = json.dumps(payload).encode() if payload is not None else None
        all_headers = {"Content-Type": "application/json"} if body else {}
        all_headers.update(headers or {})
        try:
            with urlopen(
                Request(self.base + path, data=body, headers=all_headers, method=method),
                timeout=5,
            ) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def create_session(self, headers=None):
        status, payload = self.request("/api/sessions", "POST", {}, headers)
        self.assertEqual(status, 201)
        return payload

    def close_session_via_engine(self):
        engine = self.server.workspace.engine
        state, reply = engine.create_session()
        for _ in range(25):
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = engine.submit(state.session_id, UserInput("暂时跳过", "skip"))
                continue
            text, choice = ANSWERS[reply.question_id]
            reply = engine.submit(state.session_id, UserInput(text, choice))
        self.assertEqual(reply.kind, "summary")
        engine.submit(state.session_id, UserInput("确认", "confirm_summary"))
        self.assertEqual(state.phase, Phase.CLOSED)
        return state

    def restored_state(self, session_id):
        fresh = build_runtime(backend="demo", session_dir=self.session_dir)
        context = fresh.start()
        try:
            return context.services.require("consultation").state(session_id)
        finally:
            fresh.stop()

    # -- unknown subpaths -------------------------------------------------

    def test_unknown_session_subpath_is_404(self):
        session_id = self.create_session()["session_id"]
        status, payload = self.request(f"/api/sessions/{session_id}/bogus")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "not_found")
        status, _ = self.request(f"/api/sessions/{session_id}/bogus/deeper")
        self.assertEqual(status, 404)
        # Known surfaces keep their behaviour.
        status, payload = self.request(f"/api/sessions/{session_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["state"]["session_id"], session_id)
        for sub in ("snapshot", "handoff", "events"):
            status, _ = self.request(f"/api/sessions/{session_id}/{sub}")
            self.assertEqual(status, 200, sub)

    # -- idempotent create -------------------------------------------------

    def test_create_session_is_idempotent_per_request_id(self):
        headers = {"X-Request-ID": "retry-create-42"}
        first = self.create_session(headers)
        second = self.create_session(headers)
        self.assertEqual(first["session_id"], second["session_id"])
        status, listing = self.request("/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["sessions"]), 1)
        # Requests without an id keep minting fresh sessions.
        third = self.create_session()
        fourth = self.create_session()
        self.assertNotEqual(third["session_id"], fourth["session_id"])
        self.assertNotEqual(first["session_id"], third["session_id"])

    # -- health ------------------------------------------------------------

    def test_health_reports_persistence_counters(self):
        status, payload = self.request("/api/health")
        self.assertEqual(status, 200)
        persistence = payload["persistence"]
        self.assertEqual(persistence["event_append_failures"], 0)
        self.assertEqual(persistence["event_skipped_corrupt"], 0)
        self.assertEqual(persistence["session_persist_failures"], 0)
        self.server.events.append_failures = 5
        self.server.events.skipped_corrupt = 2
        self.server.workspace.engine._persist_failures = 3
        status, payload = self.request("/api/health")
        persistence = payload["persistence"]
        self.assertEqual(persistence["event_append_failures"], 5)
        self.assertEqual(persistence["event_skipped_corrupt"], 2)
        self.assertEqual(persistence["session_persist_failures"], 3)

    # -- IO error surface ----------------------------------------------------

    def test_oserror_in_route_becomes_structured_500(self):
        with patch.object(
            self.server.workspace, "sessions", side_effect=OSError("disk gone")
        ):
            status, payload = self.request("/api/sessions")
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], "server_io_error")

    # -- direct-mutation routes persist --------------------------------------

    def test_report_draft_confirm_and_delete_survive_restart(self):
        state = self.close_session_via_engine()
        session_id = state.session_id
        status, payload = self.request(
            f"/api/sessions/{session_id}/report-drafts", "POST", {"text": DRAFT_TEXT}
        )
        self.assertEqual(status, 201)
        draft = payload["draft"]
        # The draft itself is part of the persisted case snapshot.
        restored = self.restored_state(session_id)
        self.assertIn(draft["draft_id"], restored.report_drafts)

        status, payload = self.request(
            f"/api/sessions/{session_id}/report-drafts/{draft['draft_id']}/confirm",
            "POST",
            {"source_item_id": draft["items"][0]["source_item_id"]},
        )
        self.assertEqual(status, 200)
        item_id = payload["item"]["report_item_id"]
        restored = self.restored_state(session_id)
        self.assertEqual(len(restored.report_items), 1)
        self.assertEqual(restored.report_items[0]["reported_value"], "4.2")

        status, payload = self.request(
            f"/api/sessions/{session_id}/report-items/{item_id}", "DELETE"
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])
        restored = self.restored_state(session_id)
        self.assertEqual(restored.report_items, [])

    # -- education re-check on snapshot ---------------------------------------

    def test_snapshot_schedules_education_when_predicate_says_so(self):
        session_id = self.create_session()["session_id"]
        calls = []

        class FakeEnricher:
            def needs_enrichment(self, state):
                return True

            def schedule(self, engine, state):
                calls.append(state.session_id)
                return True

        original = self.server.enricher
        self.server.enricher = FakeEnricher()
        try:
            status, _ = self.request(f"/api/sessions/{session_id}/snapshot")
            self.assertEqual(status, 200)
            self.assertEqual(calls, [session_id])

            class DecliningEnricher:
                def needs_enrichment(self, state):
                    return False

                def schedule(self, engine, state):
                    calls.append(state.session_id)
                    return True

            self.server.enricher = DecliningEnricher()
            status, _ = self.request(f"/api/sessions/{session_id}/snapshot")
            self.assertEqual(status, 200)
            self.assertEqual(calls, [session_id])
        finally:
            self.server.enricher = original

    def test_snapshot_survives_enricher_without_predicate(self):
        session_id = self.create_session()["session_id"]

        class BareEnricher:
            def schedule(self, engine, state):
                raise AssertionError("schedule must not run without a predicate")

        original = self.server.enricher
        self.server.enricher = BareEnricher()
        try:
            with patch("medagent.server.consultation.education_module") as module:
                module.needs_enrichment = None
                status, payload = self.request(f"/api/sessions/{session_id}/snapshot")
            self.assertEqual(status, 200)
            self.assertEqual(payload["state"]["session_id"], session_id)
        finally:
            self.server.enricher = original

    def test_a_route_that_ignores_its_body_still_closes_cleanly(self):
        """POST /api/sessions answers without parsing its body, and the
        browser sends ``{}`` with it (app-flow.js). Those bytes used to sit
        unread in the socket, and closing a socket with unread data sends
        RST instead of FIN — a client that had not finished reading lost the
        201, surfacing as an intermittent ConnectionResetError and an
        intermittently failed 「开始问诊」. Idle loopback usually won the race,
        which is why only CI saw it.

        Read the response to EOF here: a drained body closes with FIN and the
        loop ends on an empty chunk; an undrained one resets the connection
        and ``recv`` raises.
        """
        # Just under MAX_BODY_BYTES: big enough that the unread tail
        # survives in the socket at close time, small enough to be drained.
        body = b"{" + b" " * 60000 + b"}"
        request = (
            b"POST /api/sessions HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Connection: close\r\n"
            + f"Content-Length: {len(body)}\r\n".encode()
            + b"\r\n"
            + body
        )
        with socket.create_connection(
            ("127.0.0.1", self.server.server_port), timeout=5
        ) as connection:
            connection.sendall(request)
            received = b""
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    break
                received += chunk
        self.assertTrue(
            received.startswith(b"HTTP/1.0 201") or received.startswith(b"HTTP/1.1 201"),
            f"expected a 201 response, got {received[:80]!r}",
        )
        self.assertTrue(received.rstrip().endswith(b"}"), "response body truncated")


if __name__ == "__main__":
    unittest.main()
