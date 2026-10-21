import base64
import binascii
import struct
import tempfile
import threading
import unittest
import zlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from medagent.medical_images import (
    DeepSeekVisionRecognizer,
    MedicalImageError,
    decode_image_data_url,
    inspect_image,
    validate_recognition,
)
from medagent.patient_records import (
    PatientRecordConflict,
    PatientRecordError,
    PatientRecordStore,
)


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


def recognition():
    return {
        "schema": "medical-image-recognition/v1",
        "document_type": "laboratory_report",
        "numbers": [
            {
                "number_id": "n-1",
                "value_text": "76",
                "context": "血红蛋白 76 g/L",
                "clarity": "clear",
                "alternatives": [],
                "uncertainty_reason": None,
                "page_index": 1,
            },
            {
                "number_id": "n-2",
                "value_text": None,
                "context": "血小板计数 1?6 ×10^9/L",
                "clarity": "unclear",
                "alternatives": ["106", "136", "196"],
                "uncertainty_reason": "中间数字受到反光遮挡",
                "page_index": 1,
            },
        ],
        "content": {
            "description": "这是一份血常规检查报告。",
            "clarity": "clear",
            "unclear_parts": [],
        },
        "warnings": [],
    }


class MedicalImageContractTests(unittest.TestCase):
    def test_intake_uses_actual_image_content_and_dimensions(self):
        data = png(80, 48)
        inspected = inspect_image(data)
        self.assertEqual((inspected.width, inspected.height), (80, 48))
        self.assertEqual(inspected.mime_type, "image/png")
        decoded = decode_image_data_url(
            "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        )
        self.assertEqual(decoded.data, data)
        with self.assertRaises(MedicalImageError):
            decode_image_data_url(
                "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
            )

    def test_unclear_number_cannot_carry_a_selected_value(self):
        value = recognition()
        value["numbers"][1]["value_text"] = "136"
        with self.assertRaises(MedicalImageError):
            validate_recognition(value)

    def test_contract_rejects_extra_model_fields(self):
        value = recognition()
        value["diagnosis"] = "贫血"
        with self.assertRaises(MedicalImageError):
            validate_recognition(value)

    def test_deepseek_adapter_sends_an_original_image_and_validates_json(self):
        class Provider(BaseHTTPRequestHandler):
            request = None

            def log_message(self, *args):
                return

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                Provider.request = json.loads(self.rfile.read(length))
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(recognition(), ensure_ascii=False)
                                }
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        worker = threading.Thread(target=provider.serve_forever, daemon=True)
        worker.start()
        try:
            adapter = DeepSeekVisionRecognizer(
                f"http://127.0.0.1:{provider.server_port}", "test-key", "deepseek-flash"
            )
            result = adapter.recognize(inspect_image(png()))
            self.assertEqual(result["numbers"][0]["value_text"], "76")
            request = Provider.request
            self.assertEqual(request["model"], "deepseek-flash")
            image_part = request["messages"][1]["content"][1]
            self.assertEqual(image_part["image_url"]["detail"], "original")
            self.assertTrue(
                image_part["image_url"]["url"].startswith("data:image/png;base64,")
            )
        finally:
            provider.shutdown()
            provider.server_close()
            worker.join(timeout=2)


class PatientRecordStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = PatientRecordStore(self.root)
        self.patient = self.store.create_patient("测试档案")

    def upload(self, measured_at):
        return self.store.create_image_entry(
            self.patient["patient_id"],
            "血常规报告",
            measured_at,
            None,
            "report.png",
            inspect_image(png()),
        )

    def test_store_keeps_one_self_profile(self):
        again = self.store.create_patient("家属档案")
        self.assertEqual(again["patient_id"], self.patient["patient_id"])
        self.assertEqual(again["display_name"], "测试档案")

    def test_different_measurement_dates_remain_separate_timeline_events(self):
        first = self.upload("2026-08-20T08:00:00+08:00")
        second = self.upload("2026-08-30T08:00:00+08:00")
        timeline = self.store.timeline(self.patient["patient_id"])
        self.assertEqual(
            [item["entry_id"] for item in timeline["entries"]],
            [second["entry"]["entry_id"], first["entry"]["entry_id"]],
        )
        self.assertNotEqual(first["asset_id"], second["asset_id"])

    def test_model_result_patient_correction_and_original_are_preserved(self):
        created = self.upload("2026-08-20T08:00:00+08:00")
        run = self.store.start_recognition(created["asset_id"], "deepseek", "fixture")
        detail = self.store.finish_recognition(run, recognition())
        self.assertEqual(detail["entry"]["status"], "needs_confirmation")
        unclear = next(
            item for item in detail["observations"] if item["clarity"] == "unclear"
        )
        request = unclear["clarifications"][0]
        corrected = self.store.answer_clarification(
            request["clarification_id"],
            value_text="136",
            unit="×10^9/L",
            expected_revision_id=unclear["current_revision_id"],
        )
        current = next(
            item
            for item in corrected["observations"]
            if item["observation_id"] == unclear["observation_id"]
        )
        self.assertEqual(current["value_text"], "136")
        self.assertEqual(current["verification_status"], "patient_corrected")
        self.assertEqual(len(current["revisions"]), 2)
        self.assertIsNone(current["revisions"][0]["value_text"])
        self.assertEqual(
            corrected["recognitions"][0]["result"]["numbers"][1]["value_text"],
            None,
        )
        reopened = PatientRecordStore(self.root).entry(created["entry"]["entry_id"])
        self.assertEqual(
            next(
                item
                for item in reopened["observations"]
                if item["observation_id"] == unclear["observation_id"]
            )["value_text"],
            "136",
        )

    def test_manual_glucose_is_versioned_and_stale_edit_is_rejected(self):
        detail = self.store.add_measurement(
            self.patient["patient_id"],
            kind="blood_glucose",
            value_text="6.8",
            unit="mmol/L",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="fasting",
        )
        observation = detail["observations"][0]
        updated = self.store.revise_observation(
            observation["observation_id"],
            value_text="6.6",
            unit="mmol/L",
            expected_revision_id=observation["current_revision_id"],
        )
        self.assertEqual(updated["observations"][0]["numeric_value"], "6.6")
        with self.assertRaises(PatientRecordConflict):
            self.store.revise_observation(
                observation["observation_id"],
                value_text="7.0",
                unit="mmol/L",
                expected_revision_id=observation["current_revision_id"],
            )

    def test_timeline_highlights_values_and_only_verified_values_are_shareable(self):
        manual = self.store.add_measurement(
            self.patient["patient_id"],
            kind="blood_glucose",
            value_text="6.2",
            unit="mmol/L",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="fasting",
        )
        uploaded = self.upload("2026-08-20T08:00:00+08:00")
        run = self.store.start_recognition(uploaded["asset_id"], "deepseek", "fixture")
        recognized = self.store.finish_recognition(run, recognition())

        timeline = self.store.timeline(self.patient["patient_id"])
        manual_row = next(
            item for item in timeline["entries"] if item["entry_id"] == manual["entry"]["entry_id"]
        )
        self.assertEqual(manual_row["highlights"][0]["value_text"], "6.2")
        self.assertEqual(manual_row["confirmed_count"], 1)
        self.assertEqual(
            self.store.shareable_entry(uploaded["entry"]["entry_id"])["observations"],
            [],
        )
        uploaded_row = next(
            item
            for item in timeline["entries"]
            if item["entry_id"] == uploaded["entry"]["entry_id"]
        )
        self.assertEqual(uploaded_row["review_count"], 3)

        clear = next(
            item
            for item in recognized["observations"]
            if item["verification_status"] == "model_extracted"
            and item["kind"] == "document_number"
        )
        self.store.revise_observation(
            clear["observation_id"],
            value_text=clear["value_text"],
            unit=clear["unit"],
            expected_revision_id=clear["current_revision_id"],
            reason="confirmation",
        )
        shared = self.store.shareable_entry(uploaded["entry"]["entry_id"])
        self.assertEqual(len(shared["observations"]), 1)
        self.assertEqual(shared["observations"][0]["verification_status"], "patient_confirmed")

    def test_glucose_trend_uses_confirmed_values_and_registered_conversion(self):
        for value, unit, measured_at in (
            ("5.5", "mmol/L", "2026-08-20T07:00:00+08:00"),
            ("108", "mg/dL", "2026-08-30T07:00:00+08:00"),
        ):
            self.store.add_measurement(
                self.patient["patient_id"],
                kind="blood_glucose",
                value_text=value,
                unit=unit,
                measured_at=measured_at,
                measurement_context="fasting",
            )
        trend = self.store.glucose_trend(self.patient["patient_id"])
        self.assertEqual(trend["count"], 2)
        self.assertEqual(trend["summary"]["latest"]["normalized_value"], "6")
        self.assertEqual(trend["summary"]["average"], "5.75")
        self.assertEqual(
            trend["summary"]["latest"]["conversion"]["capability"],
            "calculate.glucose",
        )
        self.assertIn("未判断高低", trend["interpretation"])

    def test_deleting_entry_removes_asset_and_timeline_visibility(self):
        created = self.upload("2026-08-20T08:00:00+08:00")
        entry_id = created["entry"]["entry_id"]
        detail = self.store.entry(entry_id)
        asset_path = self.root / "assets" / f"{created['asset_id']}.png"
        self.assertTrue(asset_path.exists())
        self.store.delete_entry(entry_id, detail["entry"]["version"])
        self.assertFalse(asset_path.exists())
        self.assertEqual(self.store.timeline(self.patient["patient_id"])["entries"], [])
        with self.assertRaises(PatientRecordError):
            self.store.entry(entry_id)


if __name__ == "__main__":
    unittest.main()
