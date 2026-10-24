"""Asynchronous recognition jobs: bookkeeping around the existing chain.

This module owns only the job record and its state transitions. It does
not rewrite the transcription prompt or the validation contract (both
stay in ``medical_images``), and it does not own entry persistence (that
stays in ``patient_records``: ``start_recognition`` /
``finish_recognition`` / ``fail_recognition``). A job drives exactly the
same store calls the synchronous server route drives today, so the entry
state machine is unchanged:

    awaiting_recognition -> recognizing -> ready / needs_confirmation
    recognizing -> recognition_failed   (only when a started run fails)

Job statuses are a superset of the store's run statuses: a job is
``queued`` before its worker thread starts, then ``running`` while the
store run is ``running``, and finally ``succeeded`` or ``failed``.

Failure rollback rule
---------------------
Only a recognition run that was actually started may roll its entry
back to ``recognition_failed``, and only via the store's
``fail_recognition``. Concretely:

- recognizer raises (or returns an invalid contract) -> the started run
  is failed with ``provider_failed`` and the entry becomes
  ``recognition_failed``; the job ends ``failed`` with the same code.
- ``start_recognition`` itself raises a conflict (the entry is already
  ``recognizing``) -> no run was started by this job, so nothing is
  rolled back; the entry keeps its current state and the job ends
  ``failed`` with code ``already_recognizing``.
- ``finish_recognition`` raises a conflict (another run already
  succeeded for this asset) -> the entry already holds a good result,
  so nothing is rolled back; the job ends ``failed`` with code
  ``superseded`` and the entry is left untouched.

Serialization and the superseded pre-check
-------------------------------------------
Jobs for the same asset are serialized with a per-asset lock held for
the whole flow, and ``run_job`` checks the entry before starting: an
entry already ``ready`` / ``needs_confirmation`` (i.e. a previous run
already produced a good result) makes the job ``failed`` with code
``superseded`` without starting a run, so the entry is never left
stuck in ``recognizing``. The finish-conflict guard remains only as
belt-and-braces for interleavings with paths that bypass this module
(the synchronous route); in that residual case the entry keeps the
status the interleaved run left, and a store-level restore would be
needed to recompute it -- this module cannot restore it without a
store API for that, so it does not guess.

Demo / offline rule
-------------------
A ``None`` recognizer (demo mode returns no recognizer, see
``recognizer_from_settings``) is refused before any job is created and
before the store is touched: ``submit_job`` raises
``RecognitionJobError("vision_unavailable")``, no HTTP request is made,
and the entry stays ``awaiting_recognition``.

Threading
---------
``submit_job`` returns the job record immediately (status ``queued``)
and runs the flow in a daemon thread. Polling must therefore tolerate
both ``queued`` and ``running``. Provider failures are terminal job
outcomes, not exceptions: ``run_job`` returns the failed job instead of
raising. Only pre-start problems raise ``RecognitionJobError`` (no
recognizer, already recognizing, unknown job transitions).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from .medical_images import recognize_in_background
from .patient_records import PatientRecordConflict, PatientRecordError

JOB_STATUSES = ("queued", "running", "succeeded", "failed")

# Error codes stored on failed jobs. These are short, user-safe codes;
# raw provider or store messages are never copied into a job record.
ERROR_VISION_UNAVAILABLE = "vision_unavailable"
ERROR_ALREADY_RECOGNIZING = "already_recognizing"
ERROR_PROVIDER_FAILED = "provider_failed"
ERROR_SUPERSEDED = "superseded"
ERROR_JOB_FAILED = "job_failed"


class RecognitionJobError(RuntimeError):
    """Pre-start or bookkeeping failure (not a provider failure)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecognitionJobStore:
    """Thread-safe in-memory registry of recognition jobs.

    The registry is deliberately in-memory: durable state lives in the
    store's ``recognition_runs`` rows and the entry status. A process
    restart drops queued/running jobs; the entry stays ``recognizing``
    and can be re-submitted, which the conflict rule handles safely.
    ``get`` returns a copy so callers cannot mutate registry state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, *, entry_id: str, asset_id: str) -> dict[str, Any]:
        if not entry_id or not asset_id:
            raise RecognitionJobError("作业需要 entry_id 与 asset_id")
        now = _now()
        job = {
            "run_id": str(uuid4()),
            "entry_id": entry_id,
            "asset_id": asset_id,
            "status": "queued",
            "recognition_id": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[job["run_id"]] = job
        return dict(job)

    def get(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                raise KeyError(run_id)
            return dict(job)

    def list_for_entry(self, entry_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(job)
                for job in self._jobs.values()
                if job["entry_id"] == entry_id
            ]

    def mark_running(self, run_id: str, recognition_id: str) -> dict[str, Any]:
        return self._transition(
            run_id, ("queued",), "running", recognition_id=recognition_id
        )

    def mark_succeeded(self, run_id: str) -> dict[str, Any]:
        return self._transition(run_id, ("running",), "succeeded")

    def mark_failed(self, run_id: str, error: str) -> dict[str, Any]:
        return self._transition(run_id, ("queued", "running"), "failed", error=error)

    def _transition(
        self, run_id: str, allowed: tuple[str, ...], status: str, **fields: Any
    ) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(run_id)
            if job is None:
                raise KeyError(run_id)
            if job["status"] not in allowed:
                raise RecognitionJobError(
                    f"作业 {run_id} 处于 {job['status']}，不能转为 {status}"
                )
            job["status"] = status
            job.update(fields)
            job["updated_at"] = _now()
            return dict(job)


def get_job(job_store: RecognitionJobStore, run_id: str) -> dict[str, Any]:
    """Return one job record; raises ``KeyError`` for an unknown run_id."""
    return job_store.get(run_id)


_asset_locks_guard = threading.Lock()
_asset_locks: dict[str, threading.Lock] = {}


@contextmanager
def _asset_lock(asset_id: str) -> Iterator[None]:
    """Serialize all job flows for one asset (see module docstring)."""
    with _asset_locks_guard:
        lock = _asset_locks.setdefault(asset_id, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _entry_already_recognized(records: Any, entry_id: str) -> bool:
    """True when the entry already holds a good recognition result."""
    try:
        detail = records.entry(entry_id)
    except Exception:
        return False
    if detail["entry"]["status"] not in ("ready", "needs_confirmation"):
        return False
    return any(
        observation.get("source_type") == "model_extracted_document"
        for observation in detail["observations"]
    )


def _fail_started_run(
    records: Any, recognition_id: str, job_store: RecognitionJobStore, job: dict
) -> dict[str, Any]:
    """Roll a started run back: entry -> recognition_failed, job failed."""
    try:
        records.fail_recognition(recognition_id, ERROR_PROVIDER_FAILED)
    except Exception:
        pass  # the job record still reports the failure
    return job_store.mark_failed(job["run_id"], ERROR_PROVIDER_FAILED)


def run_job(
    job_store: RecognitionJobStore,
    records: Any,
    recognizer: Any,
    job: dict[str, Any],
    image: Any,
) -> dict[str, Any]:
    """Run one job's full flow synchronously; return the final job record.

    Provider failures do not raise: they end the job as ``failed`` (see
    the module docstring for the rollback rules). Only pre-start
    problems raise ``RecognitionJobError``. ``image`` is the already
    inspected image (``inspect_image`` output); this function never
    re-reads the asset bytes. Flows for the same asset are serialized
    by a per-asset lock, so two concurrent jobs for one asset cannot
    interleave into a stuck ``recognizing`` entry.
    """
    with _asset_lock(job["asset_id"]):
        return _run_job_locked(job_store, records, recognizer, job, image)


def _run_job_locked(
    job_store: RecognitionJobStore,
    records: Any,
    recognizer: Any,
    job: dict[str, Any],
    image: Any,
) -> dict[str, Any]:
    """Inner flow; the caller holds the per-asset lock."""
    run_id = job["run_id"]
    asset_id = job["asset_id"]
    if recognizer is None:
        job_store.mark_failed(run_id, ERROR_VISION_UNAVAILABLE)
        raise RecognitionJobError("当前没有可用的视觉模型配置")
    if _entry_already_recognized(records, job["entry_id"]):
        # A previous run already produced a good result: never start a
        # new run (its finish would conflict and strand the entry in
        # recognizing), just report the job as superseded.
        return job_store.mark_failed(run_id, ERROR_SUPERSEDED)
    try:
        recognition_id = records.start_recognition(
            asset_id, "deepseek", recognizer.model
        )
    except PatientRecordConflict:
        job_store.mark_failed(run_id, ERROR_ALREADY_RECOGNIZING)
        raise RecognitionJobError("这张图片正在识别") from None
    except PatientRecordError as exc:
        job_store.mark_failed(run_id, str(exc))
        raise RecognitionJobError(str(exc)) from None
    job_store.mark_running(run_id, recognition_id)
    try:
        result = recognize_in_background(recognizer, image).join()
    except Exception:
        # A provider failure (RecognitionError) or any unexpected
        # recognizer error rolls the started run back; the job record
        # is the error channel.
        return _fail_started_run(records, recognition_id, job_store, job)
    try:
        records.finish_recognition(recognition_id, result)
    except PatientRecordConflict:
        # Another run already succeeded for this asset: the entry holds
        # a good result, so never roll it back to recognition_failed.
        return job_store.mark_failed(run_id, ERROR_SUPERSEDED)
    except Exception:
        return _fail_started_run(records, recognition_id, job_store, job)
    return job_store.mark_succeeded(run_id)


def _run_job_in_thread(
    job_store: RecognitionJobStore,
    records: Any,
    recognizer: Any,
    job: dict[str, Any],
    image: Any,
    on_complete: Any = None,
) -> None:
    try:
        final = run_job(job_store, records, recognizer, job, image)
    except RecognitionJobError:
        # The job was already marked failed by run_job.
        final = job_store.get(job["run_id"])
    except Exception:
        # Last-resort guard: never leave a job stuck in running, and
        # never touch entry state from here (the run may already hold
        # a good result).
        try:
            current = job_store.get(job["run_id"])
            if current["status"] in ("queued", "running"):
                job_store.mark_failed(job["run_id"], ERROR_JOB_FAILED)
        except Exception:
            pass
        final = job_store.get(job["run_id"])
    if on_complete is not None:
        try:
            on_complete(final)
        except Exception:
            pass  # a callback failure must never rewrite the job outcome


def submit_job(
    job_store: RecognitionJobStore,
    records: Any,
    recognizer: Any,
    *,
    entry_id: str,
    asset_id: str,
    image: Any,
    on_complete: Any = None,
) -> dict[str, Any]:
    """Create a job and run it in a daemon thread; return it immediately.

    Raises ``RecognitionJobError("vision_unavailable")`` before creating
    any job when ``recognizer`` is ``None`` (demo/offline): no request
    is made and the entry is untouched. Otherwise the returned record
    has status ``queued`` (it may flip to ``running`` at once); poll
    with :func:`get_job` until it reaches ``succeeded`` or ``failed``.
    ``on_complete`` (optional) is called once with the final job record
    -- e.g. for the server to emit ``patient.asset_recognized``; a
    callback failure never rewrites the job outcome.
    """
    if recognizer is None:
        raise RecognitionJobError("当前没有可用的视觉模型配置")
    job = job_store.create(entry_id=entry_id, asset_id=asset_id)
    thread = threading.Thread(
        target=_run_job_in_thread,
        args=(job_store, records, recognizer, job, image, on_complete),
        name="recognition-job",
        daemon=True,
    )
    thread.start()
    return dict(job)
