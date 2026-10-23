"""Durable session storage for closed consultations.

Sessions live in the engine's memory while a consultation runs. The
longitudinal loop breaks that model: a follow-up plan is due 24–72
hours after the consultation closes, long past any app restart. This
store persists each session's state as one JSON file per session
(atomic tmp-write + rename) and restores **closed** sessions when an
engine starts, so history, conclusion sheets, follow-up plans and
outcome recording all survive restarts.

In-progress sessions are written through on every committed turn as a
crash snapshot but are deliberately *not* auto-restored: a half-finished
consultation carries live interaction state (pending tasks, subject
candidates) that cannot be resumed safely by replay alone. Their files
stay on disk untouched; only the phase check keeps them out.

Persistence is fail-open at the engine boundary: a store error never
blocks or breaks a consultation.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import fields
from uuid import uuid4
from pathlib import Path
from typing import Any, Dict, List, Optional

from .domain import (
    Assertion,
    CaseState,
    Fact,
    ObservationQuality,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)

SCHEMA = "case-state/v1"

# Fields whose live values may hold non-JSON interaction objects while a
# consultation is in progress. For a closed session they carry nothing
# the record needs; they are persisted when JSON-safe and dropped
# (never guessed) otherwise.
_SOFT_FIELDS = {"subject_resume", "subject_candidate"}


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _fact_to_dict(fact: Fact) -> Dict[str, Any]:
    return fact.to_dict()


def _fact_from_dict(raw: Dict[str, Any]) -> Fact:
    return Fact(
        key=raw["key"],
        value=raw.get("value"),
        assertion=Assertion(raw.get("assertion", "present")),
        source=Source(raw.get("source", "patient")),
        turn_id=raw.get("turn_id", ""),
        recorded_at=raw.get("recorded_at", ""),
        quality=ObservationQuality(raw.get("quality", "not_assessed")),
        supersedes=raw.get("supersedes"),
        fact_id=raw.get("fact_id") or raw["key"],
        evidence=raw.get("evidence"),
        structured_input=raw.get("structured_input"),
    )


def serialize_state(state: CaseState) -> Dict[str, Any]:
    """Lossless-enough snapshot of a (normally closed) CaseState."""
    raw: Dict[str, Any] = {}
    for item in fields(state):
        name = item.name
        value = getattr(state, name)
        if name == "facts":
            raw[name] = {key: _fact_to_dict(fact) for key, fact in value.items()}
        elif name == "history":
            raw[name] = [_fact_to_dict(fact) for fact in value]
        elif name in ("phase", "committed_phase"):
            raw[name] = value.value if value is not None else None
        elif name == "risk":
            raw[name] = value.to_dict()
        elif name in _SOFT_FIELDS:
            try:
                raw[name] = _json_copy(value)
            except (TypeError, ValueError):
                raw[name] = None
        else:
            raw[name] = _json_copy(value)
    return {"schema": SCHEMA, "state": raw}


def restore_state(payload: Dict[str, Any]) -> CaseState:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported session schema: {payload.get('schema')!r}")
    raw = payload["state"]
    state = CaseState(session_id=raw["session_id"])
    for item in fields(state):
        name = item.name
        if name == "session_id" or name not in raw:
            continue
        value = raw[name]
        if name == "facts":
            state.facts = {key: _fact_from_dict(fact) for key, fact in value.items()}
        elif name == "history":
            state.history = [_fact_from_dict(fact) for fact in value]
        elif name in ("phase", "committed_phase"):
            setattr(state, name, Phase(value) if value is not None else None)
        elif name == "risk":
            state.risk = RiskAssessment(
                level=RiskLevel(value.get("level", "undetermined")),
                triggers=list(value.get("triggers", [])),
                assessed_at=value.get("assessed_at", ""),
            )
        else:
            setattr(state, name, value)
    return state


class SessionStore:
    """One JSON file per session under ``root``; atomic writes."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.skipped_corrupt = 0

    _ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")

    def _path(self, session_id: str) -> Path:
        # Session ids are uuid4 in practice, but a crafted state file
        # restored from disk carries its own id — never let it walk out
        # of the store root via "../" segments.
        if not self._ID_PATTERN.match(session_id or ""):
            raise ValueError(f"invalid session id for storage: {session_id!r}")
        return self.root / f"{session_id}.json"

    def save(self, state: CaseState) -> None:
        payload = serialize_state(state)
        text = json.dumps(payload, ensure_ascii=False)
        target = self._path(state.session_id)
        # Unique tmp name (concurrent persists of one session must not
        # share a tmp file) + fsync before replace (a power cut must
        # not leave a torn file behind the rename).
        tmp = target.with_suffix(f".{os.getpid()}.{uuid4().hex[:8]}.tmp")
        with tmp.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def load_closed(self) -> List[CaseState]:
        """Restore every persisted session whose phase is closed.

        Corrupt or foreign files are skipped (and counted), never fatal.
        """
        restored: List[CaseState] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                state = restore_state(payload)
            except (ValueError, KeyError, TypeError, OSError):
                self.skipped_corrupt += 1
                continue
            if state.phase == Phase.CLOSED:
                restored.append(state)
        return restored
