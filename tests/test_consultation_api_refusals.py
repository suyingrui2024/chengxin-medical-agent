"""The consultation API's refusal paths.

`server/consultation.py` handles the requests that mutate a case: submitting an
answer, correcting a recorded fact, undoing a correction. The happy paths are
exercised elsewhere; what was untested is what happens when the request is
malformed — and those are the branches that decide whether a hand-edited or
stale payload can reach the engine.

Every refusal here is a 4xx with a machine-readable code, because the browser
turns the code into a message. A route that instead returned the case state, or
silently accepted a versionless correction, would be the bug.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from medagent.server import Handler, MedicalAgentServer
from medagent.server.runtime import build_runtime


@pytest.fixture(scope="module")
def server():
    """One demo-backed server for the whole module: startup costs ~1s."""
    scrubbed = {k: v for k, v in os.environ.items() if not k.startswith("MEDAGENT_")}
    env = patch.dict(os.environ, scrubbed, clear=True)
    env.start()
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    runtime = build_runtime(
        backend="demo",
        session_dir=root / "sessions",
        event_path=root / "events.jsonl",
    )
    context = runtime.start()
    httpd = MedicalAgentServer(
        ("127.0.0.1", 0),
        Handler,
        context.services.require("consultation"),
        context.services.require("events"),
        "demo",
    )
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        worker.join(timeout=2)
        httpd.server_close()
        runtime.stop()
        tmp.cleanup()
        env.stop()


def request(base, path, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body else {}
    try:
        with urlopen(
            Request(base + path, data=body, headers=headers, method=method), timeout=5
        ) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


# --- fact correction -------------------------------------------------------


def test_a_correction_without_text_is_refused(server):
    status, body = request(
        server, "/api/sessions/abc/facts/fact-1", "PATCH", {"expected_fact_id": "v1"}
    )
    assert status == 400
    assert body["error"]["code"] == "invalid_correction"


def test_a_blank_correction_is_refused(server):
    status, body = request(
        server,
        "/api/sessions/abc/facts/fact-1",
        "PATCH",
        {"text": "   ", "expected_fact_id": "v1"},
    )
    assert status == 400
    assert body["error"]["code"] == "invalid_correction"


def test_a_correction_without_a_version_is_refused(server):
    """Without ``expected_fact_id`` the engine cannot tell a correction from a
    late write against a fact the patient has already changed."""
    status, body = request(
        server, "/api/sessions/abc/facts/fact-1", "PATCH", {"text": "其实是右边疼"}
    )
    assert status == 400
    assert body["error"]["code"] == "invalid_fact_version"


def test_a_correction_to_an_unknown_session_is_a_404(server):
    status, body = request(
        server,
        "/api/sessions/does-not-exist/facts/fact-1",
        "PATCH",
        {"text": "其实是右边疼", "expected_fact_id": "v1"},
    )
    assert status == 404
    assert body["error"]["code"] == "session_not_found"


# --- fact restore ----------------------------------------------------------


def test_a_restore_without_a_version_is_refused(server):
    status, body = request(
        server, "/api/sessions/abc/facts/fact-1/restore", "PATCH", {}
    )
    assert status == 400
    assert body["error"]["code"] == "invalid_fact_version"


def test_a_restore_in_an_unknown_session_is_a_404(server):
    status, body = request(
        server,
        "/api/sessions/does-not-exist/facts/fact-1/restore",
        "PATCH",
        {"expected_fact_id": "v1"},
    )
    assert status == 404
    assert body["error"]["code"] == "session_not_found"


# --- unknown routes --------------------------------------------------------


def test_an_unknown_subpath_under_a_session_is_a_404_not_the_state(server):
    """An unknown route must not fall through and return case data."""
    status, body = request(server, "/api/sessions/abc/not-a-route", "POST", {})
    assert status == 404
    assert body["error"]["code"] == "not_found"
