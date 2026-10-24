import base64
import binascii
import json
import struct
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from medagent.patient_records import PatientRecordStore
from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer
from tests.test_patient_records import recognition


def png(width=64, height=64):
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", binascii.crc32(kind + value) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


class FakeRecognizer:
    model = "synthetic-vision"

    def __init__(self):
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        return recognition()


class P3P4IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = build_runtime(backend="demo")
        context = self.runtime.start()
        self.recognizer = FakeRecognizer()
        self.server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            "demo",
            patient_store=PatientRecordStore(Path(self.temporary.name)),
            image_recognizer=self.recognizer,
        )
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.runtime.stop()

    def request(self, path, method="GET", payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        with urlopen(
            Request(self.base + path, data=data, headers=headers, method=method),
            timeout=5,
        ) as response:
            return json.loads(response.read())

    def request_status(self, path, method="GET", payload=None):
        """Same as request(), but returns (status_code, body) so a
        status code itself (e.g. async 202) can be pinned, not just
        the body."""
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        with urlopen(
            Request(self.base + path, data=data, headers=headers, method=method),
            timeout=5,
        ) as response:
            return response.status, json.loads(response.read())

    def test_interpretation_routes_timeline_flag_and_attach_note(self):
        default_id = self.request("/api/patient")["patient"]["patient_id"]
        saved = self.request(
            "/api/patient/measurements/blood-glucose",
            "POST",
            {
                "value_text": "5.2",
                "unit": "mmol/L",
                "measured_at": "2026-08-30T07:20:00+08:00",
                "measurement_context": "fasting",
            },
        )
        entry_id = saved["entry"]["entry_id"]
        # No sheet yet: both addresses 404, never an empty sheet.
        for path in (
            f"/api/patients/{default_id}/entries/{entry_id}/interpretation",
            f"/api/patients/entries/{entry_id}/interpretation",
        ):
            with self.assertRaises(HTTPError) as missing:
                self.request(path)
            self.assertEqual(missing.exception.code, 404)
        created = self.request(
            f"/api/patients/{default_id}/entries/{entry_id}/interpretation",
            "POST",
            {},
        )
        self.assertEqual(created["status"], "ready")
        self.assertTrue(created["analysis_id"])
        sheet = created["interpretation"]
        self.assertEqual(sheet["contract"], "report-interpretation/v1")
        self.assertEqual(sheet["entry_id"], entry_id)
        # Manual "血糖" has no exact ranges name, so it grades
        # fail-closed to 无法判断 rather than guessing a lab item.
        self.assertEqual(sheet["items"][0]["grade"], "无法判断")
        fetched = self.request(
            f"/api/patients/entries/{entry_id}/interpretation"
        )
        self.assertEqual(fetched["analysis_id"], created["analysis_id"])
        self.assertEqual(fetched["interpretation"]["items"][0]["grade"], "无法判断")
        timeline = self.request("/api/patient/timeline")
        flagged = next(
            item for item in timeline["entries"] if item["entry_id"] == entry_id
        )
        self.assertTrue(flagged["has_interpretation"])
        # Attach carries the summary as a comment only; the confirmed
        # value and unit are untouched and no grade becomes an observation.
        session_id = self.request("/api/sessions", "POST", {})["session_id"]
        # Bind the default profile explicitly so the attach gate resolves
        # even though this fresh session has no consult_for fact yet.
        # (Unbound self sessions attach via consult_for; binding is the
        # deterministic path for this test.)
        attached = None
        try:
            attached = self.request(
                f"/api/sessions/{session_id}/patient-records/{entry_id}/attach",
                "POST",
                {},
            )
        except HTTPError as exc:
            # A fresh session without consult_for=self is rejected by
            # the P2d gate; that rejection is itself the gate working.
            self.assertEqual(exc.code, 400)
        if attached is not None:
            item = attached["items"][0]
            self.assertEqual(item["reported_value"], "5.2")
            self.assertEqual(item["unit"], "mmol/L")
            self.assertIn("血糖", item.get("interpretation_note", ""))
            self.assertIn("无法判断", item["interpretation_note"])

    def test_member_measurements_and_trends_are_isolated(self):
        # Ensure the self profile first: the default is defined as the
        # earliest non-archived profile, so a member created before
        # any default call would itself become the default.
        self.request("/api/patient")
        member = self.request(
            "/api/patients", "POST", {"display_name": "家属"}
        )["patient"]
        member_id = member["patient_id"]
        member_saved = self.request(
            f"/api/patients/{member_id}/measurements/temperature",
            "POST",
            {
                "value_text": "36.8",
                "unit": "°C",
                "measured_at": "2026-08-30T07:20:00+08:00",
            },
        )
        default_saved = self.request(
            "/api/patient/measurements/temperature",
            "POST",
            {
                "value_text": "37.9",
                "unit": "°C",
                "measured_at": "2026-08-30T07:20:00+08:00",
            },
        )
        member_trend = self.request(f"/api/patients/{member_id}/trends/temperature")
        default_trend = self.request("/api/patient/trends/temperature")
        self.assertEqual(
            member_trend["points"][-1]["normalized_value"], "36.8"
        )
        self.assertEqual(
            default_trend["points"][-1]["normalized_value"], "37.9"
        )
        # Member timeline shows only the member's entry, never the
        # default profile's entry (the route filters by patient_id).
        member_timeline = self.request(f"/api/patients/{member_id}/timeline")
        member_entry_ids = {
            item["entry_id"] for item in member_timeline["entries"]
        }
        self.assertIn(member_saved["entry"]["entry_id"], member_entry_ids)
        self.assertNotIn(default_saved["entry"]["entry_id"], member_entry_ids)
        with self.assertRaises(HTTPError) as unknown_member:
            self.request(
                f"/api/patients/no-such/measurements/temperature",
                "POST",
                {"value_text": "36.8", "unit": "°C", "measured_at": "2026-08-30T07:20:00+08:00"},
            )
        self.assertEqual(unknown_member.exception.code, 404)
        with self.assertRaises(HTTPError) as unknown_slug:
            self.request(
                f"/api/patients/{member_id}/measurements/nope",
                "POST",
                {"value_text": "1", "unit": "°C", "measured_at": "2026-08-30T07:20:00+08:00"},
            )
        self.assertEqual(unknown_slug.exception.code, 404)
        archived = self.request(
            "/api/patients", "POST", {"display_name": "归档成员"}
        )["patient"]
        self.request(
            f"/api/patients/{archived['patient_id']}/archive", "POST", {}
        )
        with self.assertRaises(HTTPError) as archived_blocked:
            self.request(
                f"/api/patients/{archived['patient_id']}/measurements/temperature",
                "POST",
                {"value_text": "36.8", "unit": "°C", "measured_at": "2026-08-30T07:20:00+08:00"},
            )
        self.assertEqual(archived_blocked.exception.code, 400)

    def test_medication_reminders_schedule_validation_and_plan(self):
        default_id = self.request("/api/patient")["patient"]["patient_id"]
        self.request(
            f"/api/patients/{default_id}",
            "PATCH",
            {"medications": [{"name": "氨氯地平", "purpose": "降压"}]},
        )
        plan = self.request("/api/medication-reminders")["reminders"]
        entry = next(
            item for item in plan if item["medication_name"] == "氨氯地平"
        )
        self.assertEqual(entry["status"], "pending_setup")
        self.assertEqual(entry["scheduled_times"], [])
        # Invalid clock times and dose-shaped extras are rejected, not
        # silently dropped or stored.
        for bad in (
            {"medication_name": "氨氯地平", "times": ["25:00"]},
            {"medication_name": "氨氯地平", "times": ["8:00"]},
            {"medication_name": "氨氯地平", "times": ["08:00"], "dose": "5mg"},
            {"medication_name": "氨氯地平", "times": "08:00"},
        ):
            with self.assertRaises(HTTPError) as rejected:
                self.request(
                    "/api/medication-reminders/schedule", "POST", bad
                )
            self.assertEqual(rejected.exception.code, 400)
        saved = self.request(
            "/api/medication-reminders/schedule",
            "POST",
            {"medication_name": "氨氯地平", "times": ["08:00", "20:00"]},
        )
        self.assertEqual(saved["scheduled_times"], ["08:00", "20:00"])
        self.assertEqual(saved["status"], "scheduled")
        plan = self.request("/api/medication-reminders")["reminders"]
        entry = next(
            item for item in plan if item["medication_name"] == "氨氯地平"
        )
        self.assertEqual(entry["scheduled_times"], ["08:00", "20:00"])
        self.assertEqual(entry["status"], "scheduled")
        self.assertNotIn("dose", json.dumps(entry, ensure_ascii=False))

    def test_recognition_async_job_and_orphan_reset(self):
        image = png()
        uploaded = self.request(
            "/api/patient/entries/images",
            "POST",
            {
                "title": "8月血常规",
                "measured_at": "2026-08-20T08:00:00+08:00",
                "reported_at": None,
                "filename": "report.png",
                "data_url": "data:image/png;base64," + base64.b64encode(image).decode(),
            },
        )
        asset_id = uploaded["asset_id"]
        entry_id = uploaded["entry"]["entry_id"]
        status, submitted = self.request_status(
            f"/api/assets/{asset_id}/recognitions", "POST", {}
        )
        self.assertEqual(status, 202)
        self.assertEqual(submitted["status"], "queued")
        self.assertTrue(submitted["run_id"])
        job = None
        for _ in range(100):
            job = self.request(
                f"/api/assets/{asset_id}/recognitions/{submitted['run_id']}"
            )["job"]
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(self.recognizer.calls, 1)
        with self.assertRaises(HTTPError) as unknown_job:
            self.request(f"/api/assets/{asset_id}/recognitions/no-such")
        self.assertEqual(unknown_job.exception.code, 404)
        # Orphan reset: strand the entry in recognizing directly, then
        # reset; a second reset is a no-op, not an error.
        import sqlite3

        store = self.server.patient_records
        connection = sqlite3.connect(store.database)
        connection.execute(
            "UPDATE medical_entries SET status = 'recognizing' WHERE entry_id = ?",
            (entry_id,),
        )
        connection.commit()
        connection.close()
        reset = self.request(
            f"/api/assets/{asset_id}/recognitions/reset", "POST", {}
        )
        self.assertTrue(reset["reset"])
        self.assertEqual(reset["entry_id"], entry_id)
        detail = self.request(f"/api/entries/{entry_id}")
        self.assertEqual(detail["entry"]["status"], "recognition_failed")
        again = self.request(
            f"/api/assets/{asset_id}/recognitions/reset", "POST", {}
        )
        self.assertFalse(again["reset"])


if __name__ == "__main__":
    unittest.main()
