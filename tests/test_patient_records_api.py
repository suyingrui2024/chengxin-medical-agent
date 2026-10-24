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

from medagent.medical_images import inspect_image
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

    def request_status(self, path, method="GET", payload=None):
        """Same as request(), but returns (status_code, body) so a
        status code itself (e.g. async 202) can be pinned, not just
        the body."""
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        with urlopen(
            Request(self.base + path, data=data, headers=headers, method=method), timeout=3
        ) as response:
            return response.status, json.loads(response.read())

    def test_member_crud_archive_and_default_stability(self):
        catalog = self.request("/api/patient")
        self.assertEqual(set(catalog), {"patient", "vision_available"})
        self.assertEqual(catalog["patient"]["display_name"], "我的健康档案")
        default_id = catalog["patient"]["patient_id"]
        created = self.request(
            "/api/patients",
            "POST",
            {
                "display_name": "家属档案",
                "birth_year": 2018,
                "birth_year_month": "2018-05",
                "chronic_conditions": ["哮喘"],
            },
        )
        member = created["patient"]
        self.assertEqual(member["display_name"], "家属档案")
        self.assertEqual(member["birth_year_month"], "2018-05")
        self.assertEqual(member["chronic_conditions"], ["哮喘"])
        self.assertNotEqual(member["patient_id"], default_id)
        members = self.request("/api/patients")["patients"]
        self.assertEqual(
            [row["patient_id"] for row in members], [default_id, member["patient_id"]]
        )
        # The default (self) profile is unaffected by adding a member.
        self.assertEqual(
            self.request("/api/patient")["patient"]["patient_id"], default_id
        )
        self.assertEqual(
            self.request(f"/api/patients/{member['patient_id']}")["patient"]["display_name"],
            "家属档案",
        )
        updated = self.request(
            f"/api/patients/{member['patient_id']}",
            "PATCH",
            {
                "display_name": "孩子",
                "allergies": [{"kind": "drug", "name": "青霉素"}],
            },
        )["patient"]
        self.assertEqual(updated["display_name"], "孩子")
        self.assertEqual(updated["allergies"], [{"kind": "drug", "name": "青霉素"}])
        self.assertEqual(updated["birth_year"], 2018)  # untouched fields stay
        # Invalid PATCHes are rejected with 400 and pollute nothing.
        for bad in (
            {"allergies": [{"kind": "smell", "name": "花粉"}]},
            {"birth_year": "2018"},
            {"unknown_field": 1},
            {},
        ):
            with self.assertRaises(HTTPError) as blocked:
                self.request(f"/api/patients/{member['patient_id']}", "PATCH", bad)
            self.assertEqual(blocked.exception.code, 400)
        unchanged = self.request(f"/api/patients/{member['patient_id']}")["patient"]
        self.assertEqual(unchanged["display_name"], "孩子")
        self.assertEqual(unchanged["allergies"], [{"kind": "drug", "name": "青霉素"}])
        # Invalid creates are rejected and do not add a profile.
        with self.assertRaises(HTTPError) as blocked:
            self.request(
                "/api/patients", "POST", {"display_name": "坏数据", "birth_year": "2018"}
            )
        self.assertEqual(blocked.exception.code, 400)
        self.assertEqual(len(self.request("/api/patients")["patients"]), 2)
        archived = self.request(
            f"/api/patients/{member['patient_id']}/archive", "POST", {}
        )["patient"]
        self.assertTrue(archived["archived"])
        self.assertEqual(
            self.request("/api/patient")["patient"]["patient_id"], default_id
        )
        # Unknown member ids are 404, not 400.
        with self.assertRaises(HTTPError) as missing:
            self.request("/api/patients/no-such-id", "PATCH", {"display_name": "x"})
        self.assertEqual(missing.exception.code, 404)
        with self.assertRaises(HTTPError) as missing:
            self.request("/api/patients/no-such-id/archive", "POST", {})
        self.assertEqual(missing.exception.code, 404)

    def test_archive_default_patient_is_refused_and_default_unchanged(self):
        # M3: archiving the default must not promote a member to self.
        default_id = self.request("/api/patient")["patient"]["patient_id"]
        with self.assertRaises(HTTPError) as blocked:
            self.request(f"/api/patients/{default_id}/archive", "POST", {})
        self.assertEqual(blocked.exception.code, 400)
        self.assertEqual(
            self.request("/api/patient")["patient"]["patient_id"], default_id
        )
        # Archiving a member still works, and the default is untouched.
        member = self.request("/api/patients", "POST", {"display_name": "孩子"})[
            "patient"
        ]
        archived = self.request(
            f"/api/patients/{member['patient_id']}/archive", "POST", {}
        )["patient"]
        self.assertTrue(archived["archived"])
        self.assertEqual(
            self.request("/api/patient")["patient"]["patient_id"], default_id
        )

    def test_bind_patient_rebuilds_baseline_and_gates_attach(self):
        # M4: minimal binding — snapshot key + member baseline + attach gate.
        store = self.server.patient_records
        default = self.request("/api/patient")["patient"]
        member = self.request("/api/patients", "POST", {"display_name": "孩子"})[
            "patient"
        ]
        self.request(
            f"/api/patients/{member['patient_id']}",
            "PATCH",
            {"chronic_conditions": ["哮喘"]},
        )
        member_entry = store.add_measurement(
            member["patient_id"],
            kind="blood_glucose",
            value_text="5.9",
            unit="mmol/L",
            measured_at="2026-08-30T07:20:00+08:00",
            measurement_context="fasting",
        )
        default_entry = self.request(
            "/api/patient/measurements/blood-glucose",
            "POST",
            {
                "value_text": "6.8",
                "unit": "mmol/L",
                "measured_at": "2026-08-30T07:20:00+08:00",
                "measurement_context": "fasting",
            },
        )
        session_id = self.request("/api/sessions", "POST", {})["session_id"]
        bind_path = f"/api/sessions/{session_id}/bind-patient"
        # Unknown session / unknown patient / bad payload / archived member.
        with self.assertRaises(HTTPError) as missing_session:
            self.request("/api/sessions/no-such/bind-patient", "POST", {"patient_id": member["patient_id"]})
        self.assertEqual(missing_session.exception.code, 404)
        with self.assertRaises(HTTPError) as missing_patient:
            self.request(bind_path, "POST", {"patient_id": "no-such"})
        self.assertEqual(missing_patient.exception.code, 404)
        with self.assertRaises(HTTPError) as bad_payload:
            self.request(bind_path, "POST", {})
        self.assertEqual(bad_payload.exception.code, 400)
        archived_member = self.request(
            "/api/patients", "POST", {"display_name": "已归档成员"}
        )["patient"]
        self.request(
            f"/api/patients/{archived_member['patient_id']}/archive", "POST", {}
        )
        with self.assertRaises(HTTPError) as archived_blocked:
            self.request(bind_path, "POST", {"patient_id": archived_member["patient_id"]})
        self.assertEqual(archived_blocked.exception.code, 400)
        # Bind the member: snapshot key set, baseline rebuilt from the
        # member (not the default), nothing written into facts.
        bound = self.request(bind_path, "POST", {"patient_id": member["patient_id"]})
        self.assertEqual(bound["patient"]["patient_id"], member["patient_id"])
        bound_state = bound["state"]
        self.assertEqual(
            bound_state["runtime_snapshot"]["consultation_patient_id"],
            member["patient_id"],
        )
        self.assertEqual(
            bound_state["profile_baseline"]["chronic_conditions"], ["哮喘"]
        )
        self.assertNotIn("chronic_conditions", bound_state["facts"])
        # The binding survives a fresh state read (persisted, not memory-only).
        reread = self.request(f"/api/sessions/{session_id}")["state"]
        self.assertEqual(
            reread["runtime_snapshot"]["consultation_patient_id"],
            member["patient_id"],
        )
        # Attach gate now follows the binding: member entry 200, self entry 400.
        attached = self.request(
            f"/api/sessions/{session_id}/patient-records/{member_entry['entry']['entry_id']}/attach",
            "POST",
            {},
        )
        self.assertEqual(len(attached["items"]), 1)
        with self.assertRaises(HTTPError) as wrong_owner:
            self.request(
                f"/api/sessions/{session_id}/patient-records/{default_entry['entry']['entry_id']}/attach",
                "POST",
                {},
            )
        self.assertEqual(wrong_owner.exception.code, 400)

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
        # P3d async: POST returns 202 + run_id at once; the entry
        # itself is the authority, so poll its detail (the same
        # address the frontend polls) until recognition lands. The
        # transcription assertions below are unchanged, not weakened.
        import time as _time

        status, submitted = self.request_status(
            f"/api/assets/{asset_id}/recognitions", "POST", {}
        )
        self.assertEqual(status, 202)
        self.assertEqual(submitted["status"], "queued")
        self.assertEqual(submitted["entry_id"], entry_id)
        self.assertEqual(submitted["asset_id"], asset_id)
        self.assertTrue(submitted["run_id"])
        result = None
        for _ in range(100):
            result = self.request(f"/api/entries/{entry_id}")
            # awaiting_recognition is the pre-thread-start state; both
            # intermediate states must be waited out, not mistaken for
            # a terminal one.
            if result["entry"]["status"] not in (
                "awaiting_recognition",
                "recognizing",
            ):
                break
            _time.sleep(0.05)
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

    def test_p2b_measurement_routes_and_trends_follow_frontend_slugs(self):
        pressure = self.request(
            "/api/patient/measurements/blood-pressure",
            "POST",
            {
                "systolic": "120",
                "diastolic": "80",
                "unit": "mmHg",
                "measured_at": "2026-08-30T07:20:00+08:00",
            },
        )
        self.assertEqual(pressure["entry"]["title"], "血压自测")
        self.assertEqual(pressure["observations"][0]["value_text"], "120/80")
        self.assertIsNone(pressure["observations"][0]["numeric_value"])
        self.request(
            "/api/patient/measurements/temperature",
            "POST",
            {
                "value_text": "98.6",
                "unit": "°F",
                "measured_at": "2026-08-30T07:25:00+08:00",
            },
        )
        trend = self.request("/api/patient/trends/blood-pressure")
        self.assertEqual(trend["count"], 1)
        self.assertEqual(trend["summary"]["systolic"]["average"], "120")
        self.assertEqual(trend["summary"]["latest"]["reported_value"], "120/80")
        temp_trend = self.request("/api/patient/trends/temperature")
        self.assertEqual(temp_trend["summary"]["latest"]["normalized_value"], "37")
        self.assertEqual(temp_trend["summary"]["unit"], "°C")
        with self.assertRaises(HTTPError) as unknown:
            self.request(
                "/api/patient/measurements/steps",
                "POST",
                {
                    "value_text": "1000",
                    "unit": "步",
                    "measured_at": "2026-08-30T07:20:00+08:00",
                },
            )
        self.assertEqual(unknown.exception.code, 404)

    def test_p2b_from_observation_never_autosaves_inferred_units(self):
        store = self.server.patient_records
        patient = store.ensure_default_patient()
        uploaded = store.create_image_entry(
            patient["patient_id"],
            "体温计读数",
            "2026-08-20T08:00:00+08:00",
            None,
            "temp.png",
            inspect_image(png()),
        )
        run = store.start_recognition(uploaded["asset_id"], "deepseek", "fixture")
        recognized = store.finish_recognition(
            run,
            {
                "schema": "medical-image-recognition/v1",
                "document_type": "laboratory_report",
                "numbers": [
                    {
                        "number_id": "n-1",
                        "value_text": "37.2",
                        "context": "体温 37.2",
                        "clarity": "clear",
                        "alternatives": [],
                        "uncertainty_reason": None,
                        "page_index": 1,
                    }
                ],
                "content": {
                    "description": "体温计读数。",
                    "clarity": "clear",
                    "unclear_parts": [],
                },
                "warnings": [],
            },
        )
        entry_id = recognized["entry"]["entry_id"]
        observation = next(
            item
            for item in recognized["observations"]
            if item["kind"] == "document_number"
        )
        from_path = "/api/patient/measurements/from-observation"
        with self.assertRaises(HTTPError) as unconfirmed:
            self.request(
                from_path,
                "POST",
                {"entry_id": entry_id, "observation_id": observation["observation_id"]},
            )
        self.assertEqual(unconfirmed.exception.code, 400)
        confirmed = self.request(
            f"/api/observations/{observation['observation_id']}/revisions",
            "POST",
            {
                "value_text": "37.2",
                "unit": "",
                "expected_revision_id": observation["current_revision_id"],
                "reason": "confirmation",
            },
        )
        self.assertEqual(
            next(
                item
                for item in confirmed["observations"]
                if item["observation_id"] == observation["observation_id"]
            )["verification_status"],
            "patient_confirmed",
        )
        preview = self.request(
            from_path,
            "POST",
            {"entry_id": entry_id, "observation_id": observation["observation_id"]},
        )
        self.assertTrue(preview["requires_confirmation"])
        self.assertTrue(preview["unit_inferred"])
        self.assertEqual(preview["unit"], "°C")
        self.assertEqual(preview["kind"], "temperature")
        self.assertEqual(
            len(self.request("/api/patient/timeline")["entries"]), 1
        )
        saved = self.request(
            from_path,
            "POST",
            {
                "entry_id": entry_id,
                "observation_id": observation["observation_id"],
                "unit": "°C",
            },
        )
        self.assertEqual(saved["observations"][0]["kind"], "temperature")
        self.assertEqual(saved["observations"][0]["value_text"], "37.2")
        self.assertTrue(saved["from_observation"]["unit_inferred"])
        original = self.request(f"/api/entries/{entry_id}")
        kept = next(
            item
            for item in original["observations"]
            if item["observation_id"] == observation["observation_id"]
        )
        self.assertEqual(kept["kind"], "document_number")
        self.assertEqual(kept["value_text"], "37.2")
        self.assertEqual(
            len(self.request("/api/patient/timeline")["entries"]), 2
        )

    def test_p2b_from_observation_rejects_mismatched_explicit_unit(self):
        # N4: transcription text carries an explicit °C; a second submit
        # claiming °F must be refused (400) without saving, while the
        # alias ℃ (same canonical unit) still saves.
        store = self.server.patient_records
        patient = store.ensure_default_patient()
        uploaded = store.create_image_entry(
            patient["patient_id"],
            "体温计读数",
            "2026-08-20T08:00:00+08:00",
            None,
            "temp.png",
            inspect_image(png()),
        )
        run = store.start_recognition(uploaded["asset_id"], "deepseek", "fixture")
        recognized = store.finish_recognition(
            run,
            {
                "schema": "medical-image-recognition/v1",
                "document_type": "laboratory_report",
                "numbers": [
                    {
                        "number_id": "n-1",
                        "value_text": "37.2",
                        "context": "体温 37.2°C",
                        "clarity": "clear",
                        "alternatives": [],
                        "uncertainty_reason": None,
                        "page_index": 1,
                    }
                ],
                "content": {
                    "description": "体温计读数。",
                    "clarity": "clear",
                    "unclear_parts": [],
                },
                "warnings": [],
            },
        )
        entry_id = recognized["entry"]["entry_id"]
        observation = next(
            item
            for item in recognized["observations"]
            if item["kind"] == "document_number"
        )
        self.request(
            f"/api/observations/{observation['observation_id']}/revisions",
            "POST",
            {
                "value_text": "37.2",
                "unit": "",
                "expected_revision_id": observation["current_revision_id"],
                "reason": "confirmation",
            },
        )
        from_path = "/api/patient/measurements/from-observation"
        base = {"entry_id": entry_id, "observation_id": observation["observation_id"]}
        with self.assertRaises(HTTPError) as mismatched:
            self.request(from_path, "POST", {**base, "unit": "°F"})
        self.assertEqual(mismatched.exception.code, 400)
        # Nothing was saved by the refused submit: still one entry.
        self.assertEqual(len(self.request("/api/patient/timeline")["entries"]), 1)
        saved = self.request(from_path, "POST", {**base, "unit": "℃"})
        self.assertEqual(saved["observations"][0]["kind"], "temperature")
        self.assertEqual(saved["observations"][0]["value_text"], "37.2")

    def test_from_observation_rejects_archived_member(self):
        # N3: match the manual per-member measurement route — an
        # archived member's confirmed transcription must not gain a
        # new measurement through the from-observation path.
        self.request("/api/patient")
        member = self.request(
            "/api/patients", "POST", {"display_name": "家属"}
        )["patient"]
        store = self.server.patient_records
        uploaded = store.create_image_entry(
            member["patient_id"],
            "体温计读数",
            "2026-08-20T08:00:00+08:00",
            None,
            "temp.png",
            inspect_image(png()),
        )
        run = store.start_recognition(uploaded["asset_id"], "deepseek", "fixture")
        recognized = store.finish_recognition(
            run,
            {
                "schema": "medical-image-recognition/v1",
                "document_type": "laboratory_report",
                "numbers": [
                    {
                        "number_id": "n-1",
                        "value_text": "37.2",
                        "context": "体温 37.2°C",
                        "clarity": "clear",
                        "alternatives": [],
                        "uncertainty_reason": None,
                        "page_index": 1,
                    }
                ],
                "content": {
                    "description": "体温计读数。",
                    "clarity": "clear",
                    "unclear_parts": [],
                },
                "warnings": [],
            },
        )
        entry_id = recognized["entry"]["entry_id"]
        observation = next(
            item
            for item in recognized["observations"]
            if item["kind"] == "document_number"
        )
        self.request(
            f"/api/observations/{observation['observation_id']}/revisions",
            "POST",
            {
                "value_text": "37.2",
                "unit": "",
                "expected_revision_id": observation["current_revision_id"],
                "reason": "confirmation",
            },
        )
        self.request(
            f"/api/patients/{member['patient_id']}/archive", "POST", {}
        )
        with self.assertRaises(HTTPError) as archived:
            self.request(
                "/api/patient/measurements/from-observation",
                "POST",
                {
                    "entry_id": entry_id,
                    "observation_id": observation["observation_id"],
                    "unit": "°C",
                },
            )
        self.assertEqual(archived.exception.code, 400)
        # Nothing was saved for the archived member: the timeline
        # still holds only the image entry, no measurement entry.
        self.assertEqual(
            len(
                self.request(f"/api/patients/{member['patient_id']}/timeline")[
                    "entries"
                ]
            ),
            1,
        )

    def test_p2d_member_timeline_and_p2c_baseline_reference_switch(self):
        member = self.request(
            "/api/patients", "POST", {"display_name": "孩子"}
        )["patient"]
        timeline = self.request(f"/api/patients/{member['patient_id']}/timeline")
        self.assertEqual(timeline["patient"]["display_name"], "孩子")
        self.assertEqual(timeline["entries"], [])
        default = self.request("/api/patient")["patient"]
        self.request(
            f"/api/patients/{default['patient_id']}",
            "PATCH",
            {
                "chronic_conditions": ["高血压"],
                "allergies": [{"kind": "drug", "name": "青霉素"}],
            },
        )
        created = self.request("/api/sessions", "POST", {})
        state = created["state"]
        self.assertTrue(state["baseline_reference_enabled"])
        self.assertEqual(state["profile_baseline"]["chronic_conditions"], ["高血压"])
        session_id = created["session_id"]
        switched = self.request(
            f"/api/sessions/{session_id}/baseline-reference",
            "POST",
            {"enabled": False},
        )
        self.assertFalse(switched["state"]["baseline_reference_enabled"])
        self.assertEqual(switched["state"]["profile_baseline"], {})
        events = self.request(f"/api/sessions/{session_id}/events")["events"]
        self.assertTrue(
            any(
                event["event_type"] == "baseline.reference_disabled"
                for event in events
            )
        )
        reopened = self.request(
            f"/api/sessions/{session_id}/baseline-reference",
            "POST",
            {"enabled": True},
        )
        self.assertTrue(reopened["state"]["baseline_reference_enabled"])
        self.assertEqual(
            reopened["state"]["profile_baseline"]["chronic_conditions"],
            ["高血压"],
        )


if __name__ == "__main__":
    unittest.main()
