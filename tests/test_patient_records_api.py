import base64
import binascii
import json
import struct
import tempfile
import threading
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
        self.last_mime = image.mime_type
        return recognition()


class PatientRecordApiTests(unittest.TestCase):
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
            Request(self.base + path, data=data, headers=headers, method=method), timeout=3
        ) as response:
            return json.loads(response.read())

    def test_api_exposes_only_the_self_profile_and_rejects_family_profiles(self):
        catalog = self.request("/api/patient")
        self.assertEqual(set(catalog), {"patient", "vision_available"})
        self.assertEqual(catalog["patient"]["display_name"], "我的健康档案")
        with self.assertRaises(HTTPError) as blocked:
            self.request(
                "/api/patients", "POST", {"display_name": "家属档案"}
            )
        self.assertEqual(blocked.exception.code, 405)
        self.assertEqual(
            self.request("/api/patient")["patient"]["patient_id"],
            catalog["patient"]["patient_id"],
        )

    def test_full_upload_recognition_confirmation_edit_and_delete_flow(self):
        catalog = self.request("/api/patient")
        self.assertTrue(catalog["vision_available"])
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
        entry_id = uploaded["entry"]["entry_id"]
        asset_id = uploaded["asset_id"]
        result = self.request(f"/api/assets/{asset_id}/recognitions", "POST", {})
        self.assertEqual(result["entry"]["status"], "needs_confirmation")
        self.assertEqual(self.recognizer.calls, 1)
        unclear = next(item for item in result["observations"] if item["clarity"] == "unclear")
        clarification = unclear["clarifications"][0]
        answered = self.request(
            f"/api/clarifications/{clarification['clarification_id']}/answers",
            "POST",
            {
                "value_text": "136",
                "unit": "×10^9/L",
                "expected_revision_id": unclear["current_revision_id"],
            },
        )
        current = next(
            item
            for item in answered["observations"]
            if item["observation_id"] == unclear["observation_id"]
        )
        edited = self.request(
            f"/api/observations/{current['observation_id']}/revisions",
            "POST",
            {
                "value_text": "137",
                "unit": "×10^9/L",
                "expected_revision_id": current["current_revision_id"],
            },
        )
        self.assertEqual(
            next(
                item
                for item in edited["observations"]
                if item["observation_id"] == current["observation_id"]
            )["value_text"],
            "137",
        )
        timeline = self.request("/api/patient/timeline")
        self.assertEqual(timeline["entries"][0]["entry_id"], entry_id)
        self.assertEqual(
            self.request("/api/patient/trends/blood-glucose")["count"],
            0,
        )
        with urlopen(self.base + f"/api/assets/{asset_id}/content", timeout=2) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(), image)
        deleted = self.request(
            f"/api/entries/{entry_id}",
            "DELETE",
            {"expected_version": edited["entry"]["version"]},
        )
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["detached_session_count"], 0)
        self.assertEqual(
            self.request("/api/patient/timeline")["entries"], []
        )

    def test_stale_revision_is_http_conflict(self):
        detail = self.request(
            "/api/patient/measurements/blood-glucose",
            "POST",
            {
                "value_text": "6.8",
                "unit": "mmol/L",
                "measured_at": "2026-08-30T07:20:00+08:00",
                "measurement_context": "fasting",
            },
        )
        observation = detail["observations"][0]
        payload = {
            "value_text": "6.6",
            "unit": "mmol/L",
            "expected_revision_id": observation["current_revision_id"],
        }
        path = f"/api/observations/{observation['observation_id']}/revisions"
        self.request(path, "POST", payload)
        with self.assertRaises(HTTPError) as error:
            self.request(path, "POST", payload)
        self.assertEqual(error.exception.code, 409)

    def test_verified_record_can_be_attached_only_to_a_self_consultation(self):
        detail = self.request(
            "/api/patient/measurements/blood-glucose",
            "POST",
            {
                "value_text": "6.8",
                "unit": "mmol/L",
                "measured_at": "2026-08-30T07:20:00+08:00",
                "measurement_context": "fasting",
            },
        )
        entry_id = detail["entry"]["entry_id"]
        session_id = self.request("/api/sessions", "POST", {})["session_id"]
        attach_path = f"/api/sessions/{session_id}/patient-records/{entry_id}/attach"
        with self.assertRaises(HTTPError) as blocked:
            self.request(attach_path, "POST", {})
        self.assertEqual(blocked.exception.code, 400)

        family_session = self.request("/api/sessions", "POST", {})["session_id"]
        family = self.request(
            f"/api/sessions/{family_session}/messages",
            "POST",
            {"text": "我的孩子", "choice_value": "child"},
        )
        self.assertEqual(
            family["state"]["facts"]["consult_for"]["value"], "child"
        )
        with self.assertRaises(HTTPError) as family_blocked:
            self.request(
                f"/api/sessions/{family_session}/patient-records/{entry_id}/attach",
                "POST",
                {},
            )
        self.assertEqual(family_blocked.exception.code, 400)

        identity = self.request(
            f"/api/sessions/{session_id}/messages", "POST", {"text": "我自己"}
        )
        self.assertEqual(identity["state"]["facts"]["consult_for"]["value"], "self")
        attached = self.request(attach_path, "POST", {})
        self.assertEqual(len(attached["items"]), 1)
        item = attached["items"][0]
        self.assertEqual(item["source"], "patient_record")
        self.assertEqual(item["reported_value"], "6.8")
        self.assertEqual(item["measured_at"], "2026-08-30T07:20:00+08:00")
        handoff = self.request(f"/api/sessions/{session_id}/handoff")["handoff"]
        self.assertEqual(handoff["report_items"][0]["record_title"], "血糖自测")

        observation = detail["observations"][0]
        revised = self.request(
            f"/api/observations/{observation['observation_id']}/revisions",
            "POST",
            {
                "value_text": "6.6",
                "unit": "mmol/L",
                "expected_revision_id": observation["current_revision_id"],
            },
        )
        refreshed = self.request(attach_path, "POST", {})
        matching = [
            row
            for row in refreshed["state"]["report_items"]
            if row.get("source_observation_id") == observation["observation_id"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["reported_value"], "6.6")
        self.assertEqual(
            matching[0]["source_revision_id"],
            revised["observations"][0]["current_revision_id"],
        )
        deleted = self.request(
            f"/api/entries/{entry_id}",
            "DELETE",
            {"expected_version": revised["entry"]["version"]},
        )
        self.assertEqual(deleted["detached_session_count"], 1)
        self.assertEqual(
            self.request(f"/api/sessions/{session_id}/handoff")["handoff"][
                "report_items"
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
