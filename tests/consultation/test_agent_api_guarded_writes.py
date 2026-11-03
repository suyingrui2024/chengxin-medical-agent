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
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


FOREIGN_ORIGIN = {"Origin": "http://evil.example"}




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
def test_a_request_from_another_origin_cannot_change_host_config(demo_server_factory, path, payload):
    """These routes carry model credentials, so the origin check is the guard."""
    status, body = post(demo_server_factory, path, payload, FOREIGN_ORIGIN)
    assert status == 403
    assert body["error"]["code"] == "invalid_origin"


# --- capability preview ----------------------------------------------------


def test_a_preview_without_the_expected_fields_is_refused(demo_server_factory):
    status, body = post(demo_server_factory, "/api/capabilities/preview", {"id": "calculate.units"})
    assert status == 400
    assert body["error"]["code"] == "invalid_capability"


def test_a_preview_whose_id_is_not_a_string_is_refused(demo_server_factory):
    status, body = post(
        demo_server_factory,
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
def test_a_malformed_review_toggle_is_refused(demo_server_factory, payload):
    status, body = post(demo_server_factory, "/api/reviews/enabled", payload)
    assert status == 400
    assert body["error"]["code"] == "invalid_review_setting"


def test_the_review_toggle_round_trips(demo_server_factory):
    status, body = post(demo_server_factory, "/api/reviews/enabled", {"enabled": True})
    assert status == 200
    assert body == {"enabled": True}

    status, body = post(demo_server_factory, "/api/reviews/enabled", {"enabled": False})
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
def test_a_malformed_medication_schedule_is_refused(demo_server_factory, payload):
    """A reminder that is silently dropped is worse than one that refuses."""
    status, body = post(demo_server_factory, "/api/medication-reminders/schedule", payload)
    assert status == 400
    assert body["error"]["code"] == "invalid_medication_schedule"


def test_a_medication_schedule_dedupes_and_trims(demo_server_factory):
    status, body = post(
        demo_server_factory,
        "/api/medication-reminders/schedule",
        {"medication_name": " 降压药 ", "times": ["08:00", " 08:00 ", "20:00"]},
    )
    assert status == 200
    assert body["medication_name"] == "降压药"
    assert body["scheduled_times"] == ["08:00", "20:00"]
    assert body["status"] == "scheduled"


def test_an_empty_time_list_clears_the_schedule(demo_server_factory):
    """Clearing is a real state: the medication exists but has no reminder yet."""
    status, body = post(
        demo_server_factory,
        "/api/medication-reminders/schedule",
        {"medication_name": "降压药", "times": []},
    )
    assert status == 200
    assert body["scheduled_times"] == []
    assert body["status"] == "pending_setup"


# --- temp file hygiene -----------------------------------------------------


def test_a_failed_schedule_write_leaves_no_temp_file(tmp_path, monkeypatch):
    """The write goes through ``.json.tmp`` and a rename.

    When the rename fails the temp file used to stay behind forever: no read
    path touches it, so nothing ever cleans it up. One was sitting in a real
    data directory when this was found.
    """
    from pathlib import Path

    from medagent.patient_records.store import PatientRecordStore
    from medagent.server.persistence import _save_medication_schedules

    store = PatientRecordStore(tmp_path / "patient-data")
    target = tmp_path / "patient-data" / "medication_schedules.json"
    target.write_text("{}", encoding="utf-8")

    def disk_full(self, other):
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "replace", disk_full)
    with pytest.raises(OSError):
        _save_medication_schedules(store, {"降压药": {"name": "降压药", "times": ["08:00"]}})

    assert target.with_suffix(".json.tmp").exists() is False
    assert target.read_text(encoding="utf-8") == "{}"

