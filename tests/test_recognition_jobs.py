"""Recognition async jobs (P3c): job bookkeeping + background wrapper.

Covers the job record, state transitions, the failure-rollback rules,
the demo/offline guarantee (no recognizer -> no job, no request, entry
untouched), and the background wrapper in medical_images. The sync
recognition path (recognizer.recognize + validate_recognition) is not
re-implemented here; fakes only stand in for the provider call.
"""

import binascii
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from medagent.medical_images import (
    BackgroundRecognition,
    MedicalImageError,
    RecognitionError,
    inspect_image,
    recognize_in_background,
    recognizer_from_settings,
    validate_recognition,
    vision_configuration_status,
)
from medagent.patient_records import PatientRecordStore
from medagent.recognition_jobs import (
    RecognitionJobError,
    RecognitionJobStore,
    get_job,
    run_job,
    submit_job,
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


def recognition_result(unclear=False):
    numbers = [
        {
            "number_id": "n-1",
            "value_text": "76",
            "context": "血红蛋白 76 g/L",
            "clarity": "clear",
            "alternatives": [],
            "uncertainty_reason": None,
            "page_index": 1,
        }
    ]
    if unclear:
        numbers.append(
            {
                "number_id": "n-2",
                "value_text": None,
                "context": "白细胞计数",
                "clarity": "unclear",
                "alternatives": ["5.1", "5.7"],
                "uncertainty_reason": "字迹模糊",
                "page_index": 1,
            }
        )
    return {
        "schema": "medical-image-recognition/v1",
        "document_type": "laboratory_report",
        "numbers": numbers,
        "content": {
            "description": "血常规报告",
            "clarity": "clear",
            "unclear_parts": [],
        },
        "warnings": [],
    }


def hollow_result(clarity):
    """No numbers and an empty overall description.

    With clarity="clear" this is the P1-D-2 contradiction (a "success"
    that transcribed nothing); with clarity="unreadable" it is an
    honest could-not-read report that must stay accepted.
    """
    return {
        "schema": "medical-image-recognition/v1",
        "document_type": "laboratory_report",
        "numbers": [],
        "content": {
            "description": "",
            "clarity": clarity,
            "unclear_parts": (
                [] if clarity == "clear" else ["整页模糊无法辨认"]
            ),
        },
        "warnings": [],
    }


class FakeRecognizer:
    """Stand-in for DeepSeekVisionRecognizer: counts calls, no network."""

    def __init__(self, result=None, error=None, delay=0.0):
        self.model = "fake-vision"
        self.result = result if result is not None else recognition_result()
        self.error = error
        self.delay = delay
        self.calls = 0

    def recognize(self, image):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result


class RecognitionJobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = PatientRecordStore(Path(self.temporary.name))
        self.patient = self.store.create_patient("测试档案")
        detail = self.store.create_image_entry(
            self.patient["patient_id"],
            "血常规报告",
            "2026-10-20T10:00:00+08:00",
            None,
            "report.png",
            inspect_image(png()),
        )
        self.entry_id = detail["entry"]["entry_id"]
        self.asset_id = detail["asset_id"]
        self.image = inspect_image(png())
        self.jobs = RecognitionJobStore()

    def entry_status(self):
        return self.store.entry(self.entry_id)["entry"]["status"]

    def poll_job(self, run_id, timeout=5.0):
        deadline = time.monotonic() + timeout
        while True:
            job = get_job(self.jobs, run_id)
            if job["status"] in ("succeeded", "failed"):
                return job
            if time.monotonic() > deadline:
                self.fail(f"作业 {run_id} 未在 {timeout}s 内结束，状态 {job['status']}")
            time.sleep(0.05)

    def test_submit_returns_immediately_and_succeeds(self):
        recognizer = FakeRecognizer(delay=0.3)
        job = submit_job(
            self.jobs,
            self.store,
            recognizer,
            entry_id=self.entry_id,
            asset_id=self.asset_id,
            image=self.image,
        )
        # Returned at once: the worker has not finished yet.
        self.assertIn(job["status"], ("queued", "running"))
        self.assertEqual(job["entry_id"], self.entry_id)
        self.assertIsNone(job["error"])
        finished = self.poll_job(job["run_id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertIsNotNone(finished["recognition_id"])
        self.assertEqual(recognizer.calls, 1)
        self.assertEqual(self.entry_status(), "ready")

    def test_run_job_sync_success_with_unclear_number_needs_confirmation(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(
            self.jobs,
            self.store,
            FakeRecognizer(result=recognition_result(unclear=True)),
            job,
            self.image,
        )
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(self.entry_status(), "needs_confirmation")

    def test_provider_failure_rolls_entry_back_to_recognition_failed(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(
            self.jobs,
            self.store,
            FakeRecognizer(error=RecognitionError("视觉模型连接失败或超时")),
            job,
            self.image,
        )
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "provider_failed")
        self.assertEqual(self.entry_status(), "recognition_failed")

    def test_hollow_clear_result_rejected_at_contract(self):
        # P1-D-2: empty numbers + empty description + clear.
        with self.assertRaises(MedicalImageError):
            validate_recognition(hollow_result("clear"))
        # Whitespace-only description normalizes to empty: same hole.
        hollow = hollow_result("clear")
        hollow["content"]["description"] = "   "
        with self.assertRaises(MedicalImageError):
            validate_recognition(hollow)

    def test_hollow_unreadable_result_still_accepted(self):
        # P1-D-2 scope guard: the same emptiness with unreadable
        # overall clarity is an honest report, not a contradiction.
        accepted = validate_recognition(hollow_result("unreadable"))
        self.assertEqual(accepted["numbers"], [])
        self.assertEqual(accepted["content"]["description"], "")

    def test_hollow_clear_result_does_not_land_as_ready(self):
        # P1-D-2 end to end: the hollow clear result is refused by
        # finish_recognition's validation, so the job fails and the
        # entry rolls back instead of landing as ready.
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(
            self.jobs,
            self.store,
            FakeRecognizer(result=hollow_result("clear")),
            job,
            self.image,
        )
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "provider_failed")
        self.assertEqual(self.entry_status(), "recognition_failed")
        self.assertEqual(self.store.entry(self.entry_id)["observations"], [])

    def test_hollow_unreadable_result_still_lands(self):
        # P1-D-2 scope guard end to end: unreadable emptiness is
        # accepted, landing as needs_confirmation (not ready), with
        # the single content observation honestly marked unreadable.
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(
            self.jobs,
            self.store,
            FakeRecognizer(result=hollow_result("unreadable")),
            job,
            self.image,
        )
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(self.entry_status(), "needs_confirmation")
        observations = self.store.entry(self.entry_id)["observations"]
        self.assertEqual(len(observations), 1)
        self.assertIsNone(observations[0]["value_text"])
        self.assertEqual(observations[0]["clarity"], "unreadable")

    def test_invalid_contract_never_lands_as_observations(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(
            self.jobs,
            self.store,
            FakeRecognizer(result={"schema": "not-a-real-contract"}),
            job,
            self.image,
        )
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "provider_failed")
        self.assertEqual(self.entry_status(), "recognition_failed")
        self.assertEqual(self.store.entry(self.entry_id)["observations"], [])

    def test_already_recognizing_does_not_roll_entry_back(self):
        # Another run already owns this entry: it stays recognizing.
        self.store.start_recognition(self.asset_id, "deepseek", "fake-vision")
        self.assertEqual(self.entry_status(), "recognizing")
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        with self.assertRaises(RecognitionJobError):
            run_job(self.jobs, self.store, FakeRecognizer(), job, self.image)
        finished = get_job(self.jobs, job["run_id"])
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "already_recognizing")
        self.assertEqual(self.entry_status(), "recognizing")

    def test_second_success_is_superseded_and_good_result_survives(self):
        first = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        run_job(self.jobs, self.store, FakeRecognizer(), first, self.image)
        self.assertEqual(self.entry_status(), "ready")
        second = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        finished = run_job(self.jobs, self.store, FakeRecognizer(), second, self.image)
        self.assertEqual(finished["status"], "failed")
        self.assertEqual(finished["error"], "superseded")
        self.assertEqual(self.entry_status(), "ready")

    def test_concurrent_jobs_for_one_asset_serialize_cleanly(self):
        recognizer = FakeRecognizer(delay=0.2)
        first = submit_job(
            self.jobs, self.store, recognizer,
            entry_id=self.entry_id, asset_id=self.asset_id, image=self.image,
        )
        second = submit_job(
            self.jobs, self.store, recognizer,
            entry_id=self.entry_id, asset_id=self.asset_id, image=self.image,
        )
        outcomes = sorted(
            self.poll_job(job["run_id"])["status"]
            for job in (first, second)
        )
        self.assertEqual(outcomes, ["failed", "succeeded"])
        failed = [
            self.poll_job(job["run_id"])
            for job in (first, second)
            if self.poll_job(job["run_id"])["status"] == "failed"
        ]
        self.assertEqual(failed[0]["error"], "superseded")
        self.assertEqual(self.entry_status(), "ready")
        self.assertEqual(recognizer.calls, 1)

    def test_on_complete_callback_receives_final_job(self):
        seen = []
        job = submit_job(
            self.jobs, self.store, FakeRecognizer(),
            entry_id=self.entry_id, asset_id=self.asset_id, image=self.image,
            on_complete=seen.append,
        )
        self.poll_job(job["run_id"])
        deadline = time.monotonic() + 5.0
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["status"], "succeeded")
        self.assertEqual(seen[0]["run_id"], job["run_id"])

    def test_demo_mode_refuses_before_any_job_or_request(self):
        self.assertEqual(
            vision_configuration_status({}, "demo")["status"], "offline"
        )
        self.assertIsNone(recognizer_from_settings({}, "demo"))
        with self.assertRaises(RecognitionJobError):
            submit_job(
                self.jobs,
                self.store,
                None,
                entry_id=self.entry_id,
                asset_id=self.asset_id,
                image=self.image,
            )
        self.assertEqual(self.jobs.list_for_entry(self.entry_id), [])
        self.assertEqual(self.entry_status(), "awaiting_recognition")

    def test_run_job_with_none_recognizer_marks_failed_and_entry_untouched(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        with self.assertRaises(RecognitionJobError):
            run_job(self.jobs, self.store, None, job, self.image)
        self.assertEqual(get_job(self.jobs, job["run_id"])["error"], "vision_unavailable")
        self.assertEqual(self.entry_status(), "awaiting_recognition")

    def test_job_store_transitions_and_copies(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        self.assertEqual(job["status"], "queued")
        # Returned records are copies: mutating one cannot corrupt the store.
        job["status"] = "succeeded"
        self.assertEqual(get_job(self.jobs, job["run_id"])["status"], "queued")
        with self.assertRaises(RecognitionJobError):
            self.jobs.mark_succeeded(job["run_id"])
        self.jobs.mark_running(job["run_id"], "rec-1")
        self.assertEqual(
            get_job(self.jobs, job["run_id"])["recognition_id"], "rec-1"
        )
        self.jobs.mark_succeeded(job["run_id"])
        with self.assertRaises(RecognitionJobError):
            self.jobs.mark_failed(job["run_id"], "provider_failed")
        with self.assertRaises(KeyError):
            get_job(self.jobs, "no-such-run")
        with self.assertRaises(RecognitionJobError):
            self.jobs.create(entry_id="", asset_id=self.asset_id)

    def test_list_for_entry_only_returns_that_entry(self):
        job = self.jobs.create(entry_id=self.entry_id, asset_id=self.asset_id)
        self.assertEqual(
            [item["run_id"] for item in self.jobs.list_for_entry(self.entry_id)],
            [job["run_id"]],
        )
        self.assertEqual(self.jobs.list_for_entry("other-entry"), [])


class BackgroundRecognitionTests(unittest.TestCase):
    def test_background_success_and_sync_path_unchanged(self):
        recognizer = FakeRecognizer(delay=0.1)
        handle = recognize_in_background(recognizer, inspect_image(png()))
        self.assertFalse(handle.done)
        self.assertEqual(handle.join(), recognition_result())
        self.assertTrue(handle.done)
        self.assertIsNone(handle.error)
        # The synchronous path is untouched and still works directly.
        self.assertEqual(recognizer.recognize(inspect_image(png())), recognition_result())
        self.assertEqual(recognizer.calls, 2)

    def test_background_failure_surfaces_on_join(self):
        handle = recognize_in_background(
            FakeRecognizer(error=RecognitionError("视觉模型连接失败或超时")),
            inspect_image(png()),
        )
        with self.assertRaises(RecognitionError):
            handle.join()
        self.assertTrue(handle.done)
        self.assertIsInstance(handle.error, RecognitionError)

    def test_background_handle_rejects_double_start_and_unstarted_join(self):
        handle = BackgroundRecognition(FakeRecognizer(), inspect_image(png()))
        with self.assertRaises(RuntimeError):
            handle.join()
        handle.start()
        with self.assertRaises(RuntimeError):
            handle.start()
        handle.join()


if __name__ == "__main__":
    unittest.main()
