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

    def test_full_upload_recognition_confirmation_edit_and_delete_flow(self):
        catalog = self.request("/api/patients")
        patient_id = catalog["default_patient_id"]
        self.assertTrue(catalog["vision_available"])
        image = png()
        uploaded = self.request(
            f"/api/patients/{patient_id}/entries/images",
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
        timeline = self.request(f"/api/patients/{patient_id}/timeline")
        self.assertEqual(timeline["entries"][0]["entry_id"], entry_id)
        self.assertEqual(
            self.request(f"/api/patients/{patient_id}/trends/blood-glucose")["count"],
            0,
        )
        with urlopen(self.base + f"/api/assets/{asset_id}/content", timeout=2) as response:
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertEqual(response.read(), image)
        self.assertEqual(
            self.request(
                f"/api/entries/{entry_id}",
                "DELETE",
                {"expected_version": edited["entry"]["version"]},
            ),
            {"deleted": True},
        )
        self.assertEqual(
            self.request(f"/api/patients/{patient_id}/timeline")["entries"], []
        )

    def test_stale_revision_is_http_conflict(self):
        patient_id = self.request("/api/patients")["default_patient_id"]
        detail = self.request(
            f"/api/patients/{patient_id}/measurements/blood-glucose",
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


if __name__ == "__main__":
    unittest.main()
