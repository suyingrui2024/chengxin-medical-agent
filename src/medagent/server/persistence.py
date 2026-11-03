"""Writing back what a route changed, and the medication reminder schedule.

Some routes mutate a session state directly instead of going through the
engine, so the engine's own write-through never happens and a restart would
restore a stale snapshot.  ``_persist_engine_state`` is the explicit write
those routes owe.

The medication schedule is patient-set data kept beside the archive rather
than in a session, so it survives a restart.  Only names and clock times are
stored: no dose, no frequency advice, no interaction data.
"""
from __future__ import annotations

import json
from pathlib import Path
import re


_MEDICATION_SCHEDULES_FILENAME = "medication_schedules.json"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

def _medication_schedules_path(store) -> Path:
    return store.root / _MEDICATION_SCHEDULES_FILENAME

def _load_medication_schedules(store) -> dict:
    """{normalized_name: {"name": original, "times": [HH:MM]}}.

    Corrupt or missing files read as empty (fail-open: reminders just
    fall back to pending_setup); the file is never auto-repaired on read.
    """
    path = _medication_schedules_path(store)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cleaned = {}
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        times = [
            item
            for item in (entry.get("times") or [])
            if isinstance(item, str) and _TIME_RE.match(item)
        ]
        name = entry.get("name") if isinstance(entry.get("name"), str) else ""
        if key and name:
            cleaned[key] = {"name": name, "times": times}
    return cleaned

def _save_medication_schedules(store, schedules: dict) -> None:
    path = _medication_schedules_path(store)
    tmp = path.with_suffix(".json.tmp")
    with store.lock:
        try:
            tmp.write_text(
                json.dumps(schedules, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError:
            # Reads only ever look at ``path``, so a half-written temp file is
            # harmless once the failure is reported — but nothing would ever
            # remove it either. Clean up, then let the caller answer 500.
            tmp.unlink(missing_ok=True)
            raise

def _persist_engine_state(engine, state) -> None:
    """Persist a state a route mutated directly, inside its session lock.

    These routes bypass the engine's own write-through points, so
    without this the change lives only in memory and a restart restores
    the stale snapshot. The engine's ``persist_state`` is fail-open by
    contract; the getattr guard only covers engines composed before the
    method existed.
    """
    persist_state = getattr(engine, "persist_state", None)
    if persist_state is not None:
        persist_state(state)


__all__ = [
    "_load_medication_schedules",
    "_medication_schedules_path",
    "_persist_engine_state",
    "_save_medication_schedules",
]
