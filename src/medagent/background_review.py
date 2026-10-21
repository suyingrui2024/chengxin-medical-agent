"""Bounded, advisory DSH review tasks for submitted consultation turns."""

from __future__ import annotations

import json
from time import monotonic
from dataclasses import dataclass, field
from threading import BoundedSemaphore, Event as CancelEvent, Lock, Thread, Timer
from typing import Any, Dict, Optional
from uuid import uuid4

from .events import Event


MAX_ITEMS = 4
MAX_ITEM_LENGTH = 180
MAX_CONCURRENT_REVIEWS = 2
REVIEW_DEADLINE_SECONDS = 8.0
REVIEW_MODES = {"shadow", "advisory"}
MATERIAL_EVENT_TYPES = {
    "fact.recorded",
    "fact.invalidated",
    "fact.restored",
    "self_check.completed",
    "capability.completed",
    "agent.summary_ready",
    "report.item_confirmed",
    "patient_record.attached",
}

REVIEW_INSTRUCTIONS = """You are an asynchronous clinical-review assistant.
You receive only an already-submitted, structured consultation snapshot. Do not
diagnose, address the patient, call tools, add facts, or override emergency
routing. Look only for: explicit contradictions, information that is important
to clarify next, and concise candidate follow-up questions. Return exactly one
JSON object in this envelope:
{"tool":"inspect_case","arguments":{"review":{"contradictions":[],"missing_information":[],"suggested_questions":[]}}}
Every list item must be grounded in the supplied snapshot. Use empty arrays when
there is nothing useful to add. Return no other keys or prose."""


@dataclass
class ReviewJob:
    identity: str
    engine: Any
    session_id: str
    revision: int
    turn_id: Optional[str]
    created_at: float = field(default_factory=monotonic)
    cancelled: CancelEvent = field(default_factory=CancelEvent)
    timed_out: CancelEvent = field(default_factory=CancelEvent)


def _items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text or len(text) > MAX_ITEM_LENGTH or text in result:
            continue
        result.append(text)
        if len(result) == MAX_ITEMS:
            break
    return result


def validate_review(command: Any) -> Dict[str, list[str]]:
    """Accept only the small advisory envelope used by the controller."""
    if not isinstance(command, dict) or command.get("tool") != "inspect_case":
        raise ValueError("review command must be inspect_case")
    arguments = command.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != {"review"}:
        raise ValueError("review arguments are invalid")
    review = arguments["review"]
    if not isinstance(review, dict) or set(review) - {
        "contradictions",
        "missing_information",
        "suggested_questions",
    }:
        raise ValueError("review fields are invalid")
    return {
        key: _items(review.get(key, []))
        for key in ("contradictions", "missing_information", "suggested_questions")
    }


class BackgroundReviewCoordinator:
    """Runs bounded, version-bound DSH reviews after material case changes."""

    def __init__(
        self,
        events,
        maximum: int = MAX_CONCURRENT_REVIEWS,
        deadline_seconds: float = REVIEW_DEADLINE_SECONDS,
        mode: str = "shadow",
    ):
        if maximum < 1 or deadline_seconds <= 0:
            raise ValueError("review capacity and deadline must be positive")
        if mode not in REVIEW_MODES:
            raise ValueError("review mode must be shadow or advisory")
        self.events = events
        self.mode = mode
        self._jobs: Dict[str, ReviewJob] = {}
        self._lock = Lock()
        self._slots = BoundedSemaphore(maximum)
        self._deadline_seconds = deadline_seconds
        self._closed = False
        self._enabled_for_process = True

    @staticmethod
    def _enabled(engine, state) -> bool:
        backend = getattr(engine, "backend", None)
        return (
            getattr(backend, "name", None) == "dsh"
            and callable(getattr(backend, "decide_cancellable", None))
            and state.risk.level.value not in {"emergency", "urgent"}
            and state.phase.value != "closed"
        )

    @property
    def enabled_for_process(self) -> bool:
        with self._lock:
            return self._enabled_for_process

    def set_enabled_for_process(self, enabled: bool) -> None:
        with self._lock:
            self._enabled_for_process = enabled

    def invalidate(self, state, reason: str) -> None:
        """Invalidate before a new submitted turn can change the case."""
        with self._lock:
            job = self._jobs.pop(state.session_id, None)
        if job:
            job.cancelled.set()
            self.events.append(
                Event(
                    state.session_id,
                    "advisor.cancelled",
                    {"source_revision": job.revision, "reason": reason},
                    turn_id=job.turn_id,
                )
            )
        if state.background_review:
            state.background_review = None

    def schedule(self, engine, state) -> None:
        """Queue a review only after the foreground reply has been committed."""
        if not self.enabled_for_process or not self._enabled(engine, state):
            return
        self.invalidate(state, "superseded_after_reply")
        events = self.events.for_session(state.session_id)
        turn_id = next(
            (event.turn_id for event in reversed(events) if event.event_type == "turn.agent_reply"),
            None,
        )
        # The no-turn fallback keeps the coordinator independently testable;
        # production scheduling always runs after turn.agent_reply.
        if turn_id is not None and not any(
            event.turn_id == turn_id and event.event_type in MATERIAL_EVENT_TYPES
            for event in events
        ):
            self.events.append(
                Event(
                    state.session_id,
                    "advisor.skipped",
                    {"reason": "no_material_case_change", "mode": self.mode},
                    turn_id=turn_id,
                )
            )
            return
        job = ReviewJob(
            identity=str(uuid4()),
            engine=engine,
            session_id=state.session_id,
            revision=state.revision,
            turn_id=turn_id,
        )
        with self._lock:
            if self._closed:
                return
            self._jobs[state.session_id] = job
        state.background_review = {
            "status": "queued",
            "mode": self.mode,
            "source_revision": state.revision,
            "source_turn_id": turn_id,
            "identity": job.identity,
        }
        self.events.append(
            Event(
                state.session_id,
                "advisor.queued",
                {"source_revision": state.revision, "mode": self.mode},
                turn_id=turn_id,
            )
        )
        Thread(target=self._run, args=(job,), name="medical-advisor", daemon=True).start()

    def _snapshot(self, state, job: ReviewJob) -> Dict[str, Any]:
        facts = {
            key: {
                name: value
                for name, value in fact.to_dict().items()
                if name in {"key", "value", "assertion", "evidence", "quality"}
            }
            for key, fact in state.facts.items()
        }
        latest_user_text = next(
            (
                item["content"]
                for item in reversed(state.transcript)
                if item.get("role") == "user"
            ),
            "",
        )
        return {
            "case_revision": job.revision,
            "source_turn_id": job.turn_id,
            "latest_submitted_text": latest_user_text[:2000],
            "facts": facts,
            "active_question": state.active_question,
            "risk_level": state.risk.level.value,
            "asked_questions": state.asked_questions[-12:],
            "patient_confirmed_report_items": [
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "reported_value",
                        "unit",
                        "reference_range",
                        "record_title",
                        "measured_at",
                        "source",
                    )
                }
                for item in state.report_items[-20:]
            ],
        }

    def _run(self, job: ReviewJob) -> None:
        if not self._slots.acquire(blocking=False):
            self._finish_failure(job, "capacity")
            return
        timer = Timer(self._deadline_seconds, self._expire, args=(job,))
        timer.daemon = True
        timer.start()
        try:
            state = job.engine.state(job.session_id)
            with job.engine._session_locks[job.session_id]:
                if not self._current(job):
                    return
                if job.cancelled.is_set():
                    if job.timed_out.is_set():
                        self._finish_failure(job, "deadline")
                    return
                snapshot = self._snapshot(state, job)
                state.background_review = {
                    "status": "running",
                    "mode": self.mode,
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                    "identity": job.identity,
                }
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.started",
                        {"source_revision": job.revision, "mode": self.mode},
                        turn_id=job.turn_id,
                    )
                )
            command = job.engine.backend.decide_cancellable(
                job.session_id,
                [
                    {"role": "system", "content": REVIEW_INSTRUCTIONS},
                    {
                        "role": "user",
                        "content": json.dumps(snapshot, ensure_ascii=False),
                    },
                ],
                job.cancelled,
            )
            if job.timed_out.is_set():
                self._finish_failure(job, "deadline")
                return
            if job.cancelled.is_set():
                return
            review = validate_review(command)
        except Exception as exc:
            self._finish_failure(job, type(exc).__name__)
            return
        else:
            self._finish_success(job, review)
        finally:
            timer.cancel()
            self._slots.release()

    @staticmethod
    def _expire(job: ReviewJob) -> None:
        job.timed_out.set()
        job.cancelled.set()

    def _current(self, job: ReviewJob) -> bool:
        with self._lock:
            return not self._closed and self._jobs.get(job.session_id) is job

    def _finish_failure(self, job: ReviewJob, error_type: str) -> None:
        try:
            try:
                state = job.engine.state(job.session_id)
            except KeyError:
                return
            with job.engine._session_locks[job.session_id]:
                if not self._current(job) or (
                    job.cancelled.is_set() and not job.timed_out.is_set()
                ):
                    return
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.unavailable",
                        {
                            "source_revision": job.revision,
                            "error_type": error_type,
                            "mode": self.mode,
                            "duration_ms": self._duration_ms(job),
                        },
                        turn_id=job.turn_id,
                    )
                )
                state.background_review = {
                    "status": "unavailable",
                    "mode": self.mode,
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                    "duration_ms": self._duration_ms(job),
                }
        finally:
            with self._lock:
                if self._jobs.get(job.session_id) is job:
                    self._jobs.pop(job.session_id, None)

    def _finish_success(self, job: ReviewJob, review: Dict[str, list[str]]) -> None:
        try:
            try:
                state = job.engine.state(job.session_id)
            except KeyError:
                return
            with job.engine._session_locks[job.session_id]:
                if (
                    job.cancelled.is_set()
                    or not self._current(job)
                    or state.revision != job.revision
                ):
                    self.events.append(
                        Event(
                            job.session_id,
                            "advisor.discarded",
                            {"source_revision": job.revision, "reason": "stale_case"},
                            turn_id=job.turn_id,
                        )
                    )
                    return
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.completed",
                        {
                            "source_revision": job.revision,
                            "mode": self.mode,
                            "duration_ms": self._duration_ms(job),
                            "counts": {key: len(value) for key, value in review.items()},
                        },
                        turn_id=job.turn_id,
                    )
                )
                state.background_review = {
                    "status": "completed",
                    "mode": self.mode,
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                    "duration_ms": self._duration_ms(job),
                    "advice": review,
                }
        finally:
            with self._lock:
                if self._jobs.get(job.session_id) is job:
                    self._jobs.pop(job.session_id, None)

    @staticmethod
    def _duration_ms(job: ReviewJob) -> int:
        return max(0, round((monotonic() - job.created_at) * 1000))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.cancelled.set()
