"""Shadow observation: what the knowledge base would have recognised.

Nothing here influences a decision. The engine calls :func:`observe` once
per turn, gets a list of matches back, and writes them to the event log.
That is deliberate: introducing a knowledge base into a clinical path
must not change behaviour on day one, or a regression in the KB becomes a
regression in the consultation.

What the event buys us is a measurement. Once real transcripts run
through it, we can ask "how often did the graph see a concept the regex
layer missed" — which is the only honest way to decide whether wiring it
in further is worth the risk.

Failure is always silent. A missing or corrupt database must not cost a
patient their turn.
"""

from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

from .normalize import Normalizer
from .store import ConceptStore

DB = Path(__file__).resolve().parents[3] / "data" / "kg" / "kg.db"

_lock = Lock()
_state: dict[str, object] = {"loaded": False, "normalizer": None}


def enabled() -> bool:
    """On by default, because an observation nobody records is no observation."""
    return os.environ.get("MEDAGENT_KG", "on").strip().lower() not in {"off", "0", "false"}


def _normalizer() -> Normalizer | None:
    if not enabled():
        return None
    with _lock:
        if not _state["loaded"]:
            _state["loaded"] = True
            try:
                if DB.is_file():
                    _state["normalizer"] = Normalizer(ConceptStore.open(DB))
            except Exception:  # noqa: BLE001 - observation must never break a turn
                _state["normalizer"] = None
        return _state["normalizer"]  # type: ignore[return-value]


def observe(text: str) -> list[dict]:
    """Concept matches for ``text``, or an empty list if anything is wrong."""
    if not text or not text.strip():
        return []
    try:
        normalizer = _normalizer()
        if normalizer is None:
            return []
        return [
            {
                "concept": match.concept_id,
                "kind": match.kind,
                "canonical": match.canonical,
                "text": match.text,
                "start": match.start,
                "end": match.end,
            }
            for match in normalizer.find(text)
        ]
    except Exception:  # noqa: BLE001
        return []


def reset() -> None:
    """Drop the cached store (tests, or after rebuilding the database)."""
    with _lock:
        normalizer = _state.get("normalizer")
        if normalizer is not None:
            try:
                normalizer.store.close()
            except Exception:  # noqa: BLE001
                pass
        _state["loaded"] = False
        _state["normalizer"] = None
