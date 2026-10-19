"""Bounded, advisory DSH review tasks for submitted consultation turns."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from threading import Event as CancelEvent, Lock, Thread
from typing import Any, Dict, Optional
from uuid import uuid4

from .events import Event


MAX_ITEMS = 4
MAX_ITEM_LENGTH = 180

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
    cancelled: CancelEvent = field(default_factory=CancelEvent)


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
    """Runs one cancellable, version-bound advisory review per consultation."""

    def __init__(self, events):
        self.events = events
        self._jobs: Dict[str, ReviewJob] = {}
        self._lock = Lock()
        self._closed = False

    @staticmethod
    def _enabled(engine, state) -> bool:
        backend = getattr(engine, "backend", None)
        return (
            getattr(backend, "name", None) == "dsh"
            and callable(getattr(backend, "decide_cancellable", None))
            and state.risk.level.value not in {"emergency", "urgent"}
            and state.phase.value != "closed"
        )

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
        if not self._enabled(engine, state):
            return
        self.invalidate(state, "superseded_after_reply")
        turn_id = next(
            (
                event.turn_id
                for event in reversed(self.events.for_session(state.session_id))
                if event.event_type == "turn.user_message"
            ),
            None,
        )
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
            "source_revision": state.revision,
            "source_turn_id": turn_id,
            "identity": job.identity,
        }
        self.events.append(
            Event(
                state.session_id,
                "advisor.queued",
                {"source_revision": state.revision},
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
        }

    def _run(self, job: ReviewJob) -> None:
        try:
            state = job.engine.state(job.session_id)
            with job.engine._session_locks[job.session_id]:
                if job.cancelled.is_set() or not self._current(job):
                    return
                snapshot = self._snapshot(state, job)
                state.background_review = {
                    "status": "running",
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                    "identity": job.identity,
                }
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.started",
                        {"source_revision": job.revision},
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
            review = validate_review(command)
        except Exception as exc:
            self._finish_failure(job, type(exc).__name__)
            return
        self._finish_success(job, review)

    def _current(self, job: ReviewJob) -> bool:
        with self._lock:
            return not self._closed and self._jobs.get(job.session_id) is job

    def _finish_failure(self, job: ReviewJob, error_type: str) -> None:
        try:
            state = job.engine.state(job.session_id)
            with job.engine._session_locks[job.session_id]:
                if job.cancelled.is_set() or not self._current(job):
                    return
                state.background_review = {
                    "status": "unavailable",
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                }
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.unavailable",
                        {"source_revision": job.revision, "error_type": error_type},
                        turn_id=job.turn_id,
                    )
                )
        finally:
            with self._lock:
                if self._jobs.get(job.session_id) is job:
                    self._jobs.pop(job.session_id, None)

    def _finish_success(self, job: ReviewJob, review: Dict[str, list[str]]) -> None:
        try:
            state = job.engine.state(job.session_id)
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
                state.background_review = {
                    "status": "completed",
                    "source_revision": job.revision,
                    "source_turn_id": job.turn_id,
                    "advice": review,
                }
                self.events.append(
                    Event(
                        job.session_id,
                        "advisor.completed",
                        {
                            "source_revision": job.revision,
                            "counts": {key: len(value) for key, value in review.items()},
                        },
                        turn_id=job.turn_id,
                    )
                )
        finally:
            with self._lock:
                if self._jobs.get(job.session_id) is job:
                    self._jobs.pop(job.session_id, None)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
            self._jobs.clear()
        for job in jobs:
            job.cancelled.set()
