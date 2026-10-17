from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
        encoded = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._events.append(event)
            if self._path:
                with self._path.open("a", encoding="utf-8") as stream:
                    stream.write(encoded + "\n")

    def for_session(self, session_id: str) -> List[Event]:
        with self._lock:
            return [event for event in self._events if event.session_id == session_id]

