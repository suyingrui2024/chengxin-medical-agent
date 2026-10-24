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
    recognizer_from_settings,
    validate_recognition,
    vision_configuration_status,
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
    def test_runtime_settings_can_configure_a_distinct_image_model(self):
        settings = {
            "MEDAGENT_LLM_BASE_URL": "https://model.example/v1",
            "MEDAGENT_LLM_API_KEY": "test-key",
            "MEDAGENT_VISION_MODEL": "vision-fixture",
        }
        self.assertEqual(
            vision_configuration_status(settings, "compatible")["status"],
            "configured",
        )
        self.assertEqual(
            recognizer_from_settings(settings, "compatible").model,
            "vision-fixture",
        )

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

    def test_multiple_profiles_default_is_first_created(self):
        # P2d overturns single_patient_only: later profiles are members,
        # and the default (self) profile stays the first one created.
        member = self.store.create_patient("家属档案")
        self.assertNotEqual(member["patient_id"], self.patient["patient_id"])
        self.assertEqual(member["display_name"], "家属档案")
        self.assertEqual(
            self.store.ensure_default_patient()["patient_id"],
            self.patient["patient_id"],
        )
        self.assertEqual(
            [row["display_name"] for row in self.store.list_patients()],
            ["测试档案", "家属档案"],
        )

    def test_baseline_create_update_and_invalid_patch_pollutes_nothing(self):
        member = self.store.add_member(
            "孩子",
            {
                "birth_year": 2018,
                "birth_year_month": "2018-05",
                "sex": "女",
                "height_cm": 110,
                "weight_kg": 19.5,
                "chronic_conditions": ["哮喘"],
                "allergies": [{"kind": "drug", "name": "青霉素"}],
                "medications": [{"name": "沙丁胺醇", "purpose": "哮喘发作时"}],
                "surgical_history": "2023 年疝气手术",
            },
        )
        self.assertEqual(member["birth_year"], 2018)
        self.assertEqual(member["birth_year_month"], "2018-05")
        self.assertEqual(member["allergies"], [{"kind": "drug", "name": "青霉素"}])
        self.assertEqual(member["medications"][0]["purpose"], "哮喘发作时")
        updated = self.store.update_patient(
            member["patient_id"],
            {"display_name": "孩子改名", "weight_kg": 20, "chronic_conditions": []},
        )
        self.assertEqual(updated["display_name"], "孩子改名")
        self.assertEqual(updated["weight_kg"], 20)
        self.assertEqual(updated["chronic_conditions"], [])
        self.assertEqual(updated["birth_year"], 2018)  # untouched fields stay
        for bad in (
            {"birth_year": "2018"},
            {"birth_year": 1800},
            {"birth_year_month": "2018-13"},
            {"birth_year_month": "2018/05"},
            {"sex": "x" * 201},
            {"height_cm": -5},
            {"height_cm": "高"},
            {"chronic_conditions": ["x"] * 21},
            {"chronic_conditions": ["ok", 3]},
            {"allergies": [{"kind": "smell", "name": "花粉"}]},
            {"allergies": [{"kind": "drug"}]},
            {"allergies": [{"kind": "drug", "name": "x", "extra": 1}]},
            {"medications": [{"purpose": "无名药"}]},
            {"surgical_history": "x" * 201},
            {"unknown_field": 1},
            {},
        ):
            with self.assertRaises(PatientRecordError):
                self.store.update_patient(member["patient_id"], bad)
            unchanged = self.store.patient(member["patient_id"])
            self.assertEqual(unchanged["display_name"], "孩子改名")
            self.assertEqual(unchanged["weight_kg"], 20)
            self.assertEqual(unchanged["allergies"], [{"kind": "drug", "name": "青霉素"}])

    def test_archived_member_is_listed_but_not_default(self):
        member = self.store.add_member("家属档案")
        archived = self.store.archive_patient(member["patient_id"])
        self.assertTrue(archived["archived"])
        self.assertIsNotNone(archived["archived_at"])
        listed = self.store.list_patients()
        self.assertEqual(len(listed), 2)
        self.assertTrue(listed[1]["archived"])
        self.assertEqual(
            self.store.ensure_default_patient()["patient_id"],
            self.patient["patient_id"],
        )
        self.assertEqual(self.store.list_patients(include_archived=False), [self.patient])
        # Archiving the default is refused (M3): it must not silently
        # promote a family member to the self profile.
        second = self.store.add_member("第二位成员")
        with self.assertRaises(PatientRecordError):
            self.store.archive_patient(self.patient["patient_id"])
        self.assertEqual(
            self.store.ensure_default_patient()["patient_id"],
            self.patient["patient_id"],
        )
        self.assertFalse(self.store.patient(self.patient["patient_id"])["archived"])

    def test_v1_database_migrates_with_entries_intact(self):
        import sqlite3

        root = Path(self.temporary.name) / "v1db"
        root.mkdir()
        database = root / "patients.sqlite3"
        connection = sqlite3.connect(database)
        try:
            connection.executescript(
                """
                CREATE TABLE patient_profiles (
                    patient_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE medical_entries (
                    entry_id TEXT PRIMARY KEY,
                    patient_id TEXT NOT NULL REFERENCES patient_profiles(patient_id),
                    entry_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    measured_at TEXT,
                    reported_at TEXT,
                    uploaded_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    deleted_at TEXT
                );
                CREATE TABLE observations (
                    observation_id TEXT PRIMARY KEY,
                    entry_id TEXT NOT NULL REFERENCES medical_entries(entry_id),
                    kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    value_text TEXT,
                    numeric_value TEXT,
                    unit TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '',
                    measurement_context TEXT,
                    clarity TEXT NOT NULL,
                    verification_status TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    measured_at TEXT,
                    current_revision_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO patient_profiles VALUES (?, ?, 1, 1, ?, ?)",
                ("patient-v1", "老档案", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                """INSERT INTO medical_entries VALUES
                   ('entry-v1', 'patient-v1', 'manual_measurement', '血糖自测',
                    '2026-08-30T07:20:00+08:00', NULL, '2026-08-30T07:20:00Z',
                    'ready', 1, NULL)"""
            )
            connection.execute(
                """INSERT INTO observations VALUES
                   ('obs-v1', 'entry-v1', 'blood_glucose', '血糖', '6.2', '6.2',
                    'mmol/L', '空腹血糖 6.2 mmol/L', 'fasting', 'clear',
                    'patient_confirmed', 'patient_manual', 'entry-v1',
                    '2026-08-30T07:20:00+08:00', 'rev-v1',
                    '2026-08-30T07:20:00Z', '2026-08-30T07:20:00Z')"""
            )
            connection.commit()
            user_version_before = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(user_version_before, 0)
        migrated = PatientRecordStore(root)
        profile = migrated.patient("patient-v1")
        self.assertEqual(profile["display_name"], "老档案")
        self.assertEqual(profile["schema_version"], 2)
        self.assertIsNone(profile["birth_year"])
        self.assertIsNone(profile["birth_year_month"])
        self.assertEqual(profile["chronic_conditions"], [])
        self.assertEqual(profile["allergies"], [])
        self.assertEqual(profile["medications"], [])
        self.assertFalse(profile["archived"])
        timeline = migrated.timeline("patient-v1")
        self.assertEqual(len(timeline["entries"]), 1)
        self.assertEqual(timeline["entries"][0]["entry_id"], "entry-v1")
        self.assertEqual(migrated.glucose_trend("patient-v1")["count"], 1)
        with sqlite3.connect(database) as check:
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 2)
        # The migrated profile stays fully usable.
        migrated.update_patient("patient-v1", {"birth_year": 1980})
        self.assertEqual(migrated.patient("patient-v1")["birth_year"], 1980)

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

    def test_p2b_measurement_kinds_generalize_add_measurement(self):
        pid = self.patient["patient_id"]
        pressure = self.store.add_measurement(
            pid,
            kind="blood_pressure",
            value_text="120/80",
            unit="mmHg",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="random",
        )
        self.assertEqual(pressure["entry"]["title"], "血压自测")
        observation = pressure["observations"][0]
        self.assertEqual(observation["value_text"], "120/80")
        self.assertIsNone(observation["numeric_value"])
        temperature = self.store.add_measurement(
            pid,
            kind="temperature",
            value_text="37.2",
            unit="°C",
            measured_at="2026-08-30T07:25:00+08:00",
            measurement_context="random",
        )
        self.assertEqual(temperature["entry"]["title"], "体温自测")
        self.assertEqual(temperature["observations"][0]["numeric_value"], "37.2")
        with self.assertRaises(PatientRecordError):
            self.store.add_measurement(
                pid,
                kind="weight",
                value_text="70",
                unit="kg",
                measured_at="2026-08-30T07:30:00+08:00",
                measurement_context="fasting",
            )
        with self.assertRaises(PatientRecordError):
            self.store.add_measurement(
                pid,
                kind="steps",
                value_text="1000",
                unit="步",
                measured_at="2026-08-30T07:30:00+08:00",
                measurement_context="random",
            )
        with self.assertRaises(PatientRecordError):
            self.store.add_measurement(
                pid,
                kind="blood_pressure",
                value_text="120",
                unit="mmHg",
                measured_at="2026-08-30T07:30:00+08:00",
                measurement_context="random",
            )

    def test_p2b_out_of_range_warns_but_still_saves(self):
        detail = self.store.add_measurement(
            self.patient["patient_id"],
            kind="temperature",
            value_text="45",
            unit="°C",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="random",
        )
        self.assertTrue(detail["warnings"])
        self.assertEqual(detail["observations"][0]["clarity"], "out_of_range")

    def test_p2b_revise_observation_revalidates_through_registry(self):
        detail = self.store.add_measurement(
            self.patient["patient_id"],
            kind="blood_pressure",
            value_text="120/80",
            unit="mmHg",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="random",
        )
        observation = detail["observations"][0]
        updated = self.store.revise_observation(
            observation["observation_id"],
            value_text="130/85",
            unit="mmHg",
            expected_revision_id=observation["current_revision_id"],
        )
        self.assertEqual(updated["observations"][0]["value_text"], "130/85")
        self.assertIsNone(updated["observations"][0]["numeric_value"])
        with self.assertRaises(PatientRecordError):
            self.store.revise_observation(
                observation["observation_id"],
                value_text="130",
                unit="mmHg",
                expected_revision_id=updated["observations"][0][
                    "current_revision_id"
                ],
            )

    def test_p2b_measurement_trend_covers_scalar_and_pressure(self):
        pid = self.patient["patient_id"]
        for value, measured_at in (
            ("36.8", "2026-08-29T07:00:00+08:00"),
            ("37.4", "2026-08-30T07:00:00+08:00"),
        ):
            self.store.add_measurement(
                pid,
                kind="temperature",
                value_text=value,
                unit="°C",
                measured_at=measured_at,
                measurement_context="random",
            )
        trend = self.store.measurement_trend("temperature", pid)
        self.assertEqual(trend["count"], 2)
        self.assertEqual(trend["summary"]["average"], "37.1")
        self.assertEqual(trend["summary"]["latest"]["normalized_value"], "37.4")
        self.assertIn("未判断高低", trend["interpretation"])
        for value in ("120/80", "130/85"):
            self.store.add_measurement(
                pid,
                kind="blood_pressure",
                value_text=value,
                unit="mmHg",
                measured_at="2026-08-30T08:00:00+08:00",
                measurement_context="random",
            )
        pressure = self.store.measurement_trend("blood_pressure", pid)
        self.assertEqual(pressure["count"], 2)
        self.assertEqual(pressure["summary"]["systolic"]["average"], "125")
        self.assertEqual(pressure["summary"]["diastolic"]["latest"], "85")
        self.assertEqual(pressure["summary"]["latest"]["reported_value"], "130/85")
        with self.assertRaises(PatientRecordError):
            self.store.measurement_trend("steps", pid)

    def test_p2b_recent_confirmed_measurements_window_and_status(self):
        pid = self.patient["patient_id"]
        self.store.add_measurement(
            pid,
            kind="temperature",
            value_text="36.9",
            unit="°C",
            measured_at="2026-08-30T07:00:00+08:00",
            measurement_context="random",
        )
        recent = self.store.recent_confirmed_measurements(pid, days=365 * 10)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["kind"], "temperature")
        self.assertAlmostEqual(recent[0]["value"], 36.9)
        self.assertEqual(recent[0]["unit"], "°C")
        self.assertEqual(self.store.recent_confirmed_measurements(pid), [])


if __name__ == "__main__":
    unittest.main()
