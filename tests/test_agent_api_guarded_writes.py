"""The agent API's guarded writes.

`server/agent.py` owns the POST routes that change host configuration: which
model the app talks to, whether background review runs, what medication times a
patient has set. Two things were untested and both matter more than the happy
path:

* every one of these routes refuses a request whose ``Origin`` is not the app
  itself. That check is the only thing standing between a page the patient
  happens to have open and their model credentials;
* the payload validation. A time that is not ``HH:MM`` is a 400, never silently
  dropped — a reminder that quietly disappears is worse than one that refuses.
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

FOREIGN_ORIGIN = {"Origin": "http://evil.example"}


@pytest.fixture(scope="module")
def server():
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


def post(base, path, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    all_headers = {"Content-Type": "application/json"}
    all_headers.update(headers or {})
    try:
        with urlopen(
            Request(base + path, data=body, headers=all_headers, method="POST"), timeout=5
        ) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


# --- the origin guard ------------------------------------------------------


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/capabilities/configure", {}),
        ("/api/capabilities/preview", {}),
        ("/api/settings", {}),
        ("/api/settings/test", {}),
        ("/api/reviews/enabled", {"enabled": True}),
    ],
)
def test_a_request_from_another_origin_cannot_change_host_config(server, path, payload):
    """These routes carry model credentials, so the origin check is the guard."""
    status, body = post(server, path, payload, FOREIGN_ORIGIN)
    assert status == 403
    assert body["error"]["code"] == "invalid_origin"


# --- capability preview ----------------------------------------------------


def test_a_preview_without_the_expected_fields_is_refused(server):
    status, body = post(server, "/api/capabilities/preview", {"id": "calculate.units"})
    assert status == 400
    assert body["error"]["code"] == "invalid_capability"


def test_a_preview_whose_id_is_not_a_string_is_refused(server):
    status, body = post(
        server,
        "/api/capabilities/preview",
        {"id": 7, "input": {}, "expected_revision": "r1"},
    )
    assert status == 400
    assert body["error"]["code"] == "invalid_capability"


# --- review toggle ---------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},                              # missing the field
        {"enabled": "yes"},              # not a boolean
        {"enabled": True, "extra": 1},   # an unknown key is a mistake, not noise
    ],
)
def test_a_malformed_review_toggle_is_refused(server, payload):
    status, body = post(server, "/api/reviews/enabled", payload)
    assert status == 400
    assert body["error"]["code"] == "invalid_review_setting"


def test_the_review_toggle_round_trips(server):
    status, body = post(server, "/api/reviews/enabled", {"enabled": True})
    assert status == 200
    assert body == {"enabled": True}

    status, body = post(server, "/api/reviews/enabled", {"enabled": False})
    assert status == 200
    assert body == {"enabled": False}


# --- medication reminder times --------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"medication_name": "", "times": ["08:00"]},          # no name
        {"medication_name": "降压药", "times": "08:00"},        # times not a list
        {"medication_name": "降压药", "times": ["8点"]},        # not HH:MM
        {"medication_name": "降压药", "times": ["08:00", 7]},   # not a string
        {"medication_name": "降压药", "times": ["08:00"], "dose": "1片"},  # no dose accepted
    ],
)
def test_a_malformed_medication_schedule_is_refused(server, payload):
    """A reminder that is silently dropped is worse than one that refuses."""
    status, body = post(server, "/api/medication-reminders/schedule", payload)
    assert status == 400
    assert body["error"]["code"] == "invalid_medication_schedule"


def test_a_medication_schedule_dedupes_and_trims(server):
    status, body = post(
        server,
        "/api/medication-reminders/schedule",
        {"medication_name": " 降压药 ", "times": ["08:00", " 08:00 ", "20:00"]},
    )
    assert status == 200
    assert body["medication_name"] == "降压药"
    assert body["scheduled_times"] == ["08:00", "20:00"]
    assert body["status"] == "scheduled"


def test_an_empty_time_list_clears_the_schedule(server):
    """Clearing is a real state: the medication exists but has no reminder yet."""
    status, body = post(
        server,
        "/api/medication-reminders/schedule",
        {"medication_name": "降压药", "times": []},
    )
    assert status == 200
    assert body["scheduled_times"] == []
    assert body["status"] == "pending_setup"
