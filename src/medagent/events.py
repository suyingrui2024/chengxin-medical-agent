from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from .domain import utc_now


@dataclass(frozen=True)
class Event:
    session_id: str
    event_type: str
    payload: Dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: str = field(default_factory=utc_now)
    turn_id: Optional[str] = None
    trace_id: Optional[str] = None
    request_id: Optional[str] = None
    decision_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventStore:
    """Append-only JSONL event store.

    If no path is supplied, events stay in memory. Production deployments should
    replace this service with an encrypted, access-controlled implementation.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._events: List[Event] = []
        self._path = path
        self._lock = Lock()
        self._traces: Dict[str, Dict[str, str]] = {}
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                self._events = list(self._read_file(path))

    @staticmethod
    def _read_file(path: Path) -> Iterable[Event]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            yield Event(**raw)

    def append(self, event: Event) -> None:
        with self._lock:
            trace = self._traces.get(event.session_id, {})
            if trace:
                event = replace(
                    event,
                    trace_id=event.trace_id or trace.get("trace_id"),
                    request_id=event.request_id or trace.get("request_id"),
                    decision_id=event.decision_id or trace.get("decision_id"),
                )
            encoded = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
            self._events.append(event)
            if self._path:
                with self._path.open("a", encoding="utf-8") as stream:
                    stream.write(encoded + "\n")

    def bind_trace(
        self,
        session_id: str,
        trace_id: str,
        request_id: Optional[str] = None,
        decision_id: Optional[str] = None,
    ) -> None:
        """Attach one foreground request's correlation IDs to new session events."""
        with self._lock:
            self._traces[session_id] = {
                key: value
                for key, value in {
                    "trace_id": trace_id,
                    "request_id": request_id,
                    "decision_id": decision_id,
                }.items()
                if value
            }

    def clear_trace(self, session_id: str) -> None:
        with self._lock:
            self._traces.pop(session_id, None)

    def for_session(self, session_id: str) -> List[Event]:
        with self._lock:
            return [event for event in self._events if event.session_id == session_id]

    def discard_session(self, session_id: str) -> None:
        """Remove all events for one session, including optional JSONL storage."""
        with self._lock:
            remaining = [event for event in self._events if event.session_id != session_id]
            if len(remaining) == len(self._events):
                return
            self._events = remaining
            if self._path:
                encoded = "\n".join(
                    json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
                    for event in self._events
                )
                temporary = self._path.with_suffix(self._path.suffix + ".deleting")
                temporary.write_text(encoded + ("\n" if encoded else ""), encoding="utf-8")
                temporary.replace(self._path)

