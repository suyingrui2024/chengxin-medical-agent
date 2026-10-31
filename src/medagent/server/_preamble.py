"""Shared imports, constants and route helpers for the server package."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
from uuid import uuid4
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs

from ..consultation import (
    ConsultationEngine,
    StaleActionError,
    UserInput,
    ConsultationBusyError,
)
from ..domain import utc_now
from ..events import Event, EventStore
from ..agent import AdaptiveConsultationEngine
from ..background_review import BackgroundReviewCoordinator
from ..education import EducationEnricher
from ..subagents import SubagentCoordinator
from ..web_tools import WebTools
from ..handoff import build_handoff
from ..medical_images import (
    MedicalImageError,
    RecognitionError,
    decode_image_data_url,
    inspect_image,
    recognizer_from_settings,
)
from .. import education as education_module
from ..baseline_projection import build_baseline_projection
from ..medication_reminders import build_medication_reminders
from ..recognition_jobs import (
    RecognitionJobError,
    RecognitionJobStore,
    get_job,
    submit_job,
)
from ..report_interpretation import build_interpretation, interpretation_summary
from ..measurement_kinds import (
    MeasurementKindError,
    normalize_measurement,
    saveable_from_observation,
)
from ..patient_records import (
    PatientRecordConflict,
    PatientRecordError,
    PatientRecordStore,
)
from ..report_drafts import parse_report_draft
from ..runtime import build_runtime
from ..workspace import Workspace, StaleCompositionError, subagent_kind_overrides
from .. import __version__
from ..execution import ExecutionManager, ExecutionError, ExecutionBusy
from ..execution_files import DirectoryChanged


MAX_BODY_BYTES = 64 * 1024
MAX_IMAGE_BODY_BYTES = 6 * 1024 * 1024


# P2b: frontend measurement slugs (patient-records-ui.js MEASUREMENT_KINDS)
# mapped to registry kinds. blood-glucose keeps its legacy route shape.
MEASUREMENT_SLUGS = {
    "blood-glucose": "blood_glucose",
    "blood-pressure": "blood_pressure",
    "temperature": "temperature",
    "weight": "weight",
    "blood-oxygen": "blood_oxygen",
}


def _default_baseline_projection(store) -> dict:
    """Build the P2c projection for the default (self) profile.

    Fail-open by contract: archive errors must never break session
    creation, so any failure yields an empty projection.
    """
    try:
        patient = store.ensure_default_patient()
        recent = store.recent_confirmed_measurements(patient["patient_id"])
        return build_baseline_projection(patient, recent)
    except Exception:
        return {}


def _session_baseline_projection(store, state=None) -> dict:
    """Build the P2c projection for a session's bound subject (P2d).

    A session bound via POST /api/sessions/{id}/bind-patient projects
    the bound member; an unbound session keeps the old semantics — the
    default (self) profile. Only an explicit snapshot binding counts:
    an unbound proxy session (consult_for != self, no binding) still
    gets the default projection at creation, exactly as before, and
    never an empty one via the attach-gate resolver. Fail-open like
    _default_baseline_projection.
    """
    try:
        bound = None
        snapshot = getattr(state, "runtime_snapshot", None)
        if isinstance(snapshot, dict):
            candidate = snapshot.get("consultation_patient_id")
            if isinstance(candidate, str) and candidate:
                bound = candidate
        if bound is not None:
            patient = store.patient(bound)
            recent = store.recent_confirmed_measurements(patient["patient_id"])
            return build_baseline_projection(patient, recent)
        return _default_baseline_projection(store)
    except Exception:
        return {}


def _consultation_patient_id(state, default_patient_id):
    """Resolve the archive owner for an attach gate (P2d).

    A session bound to a member (runtime_snapshot key written by a
    future member picker) resolves to that member; an unbound session
    keeps the old semantics — self consultations use the default
    profile, proxy consultations without a binding resolve to nothing
    and therefore cannot attach (same rejection as before, with a
    message that names the missing binding instead of demanding self).
    """
    snapshot = state.runtime_snapshot
    if isinstance(snapshot, dict):
        bound = snapshot.get("consultation_patient_id")
        if isinstance(bound, str) and bound:
            return bound
    if state.get("consult_for") == "self":
        return default_patient_id
    return None


# P4 medication-reminder schedules: patient-set clock times only, kept
# in the patient data root next to patients.sqlite3 (never in a session
# dir, so they survive restarts). Keyed by normalized medication name;
# no dose, no frequency advice, no interaction data is ever stored here.
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
        tmp.write_text(
            json.dumps(schedules, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)


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
    "argparse",
    "json",
    "mimetypes",
    "os",
    "re",
    "secrets",
    "uuid4",
    "HTTPStatus",
    "BaseHTTPRequestHandler",
    "ThreadingHTTPServer",
    "resources",
    "Path",
    "Any",
    "Dict",
    "Optional",
    "urlparse",
    "parse_qs",
    "ConsultationEngine",
    "StaleActionError",
    "UserInput",
    "ConsultationBusyError",
    "utc_now",
    "Event",
    "EventStore",
    "AdaptiveConsultationEngine",
    "BackgroundReviewCoordinator",
    "EducationEnricher",
    "SubagentCoordinator",
    "WebTools",
    "build_handoff",
    "MedicalImageError",
    "RecognitionError",
    "decode_image_data_url",
    "inspect_image",
    "recognizer_from_settings",
    "education_module",
    "build_baseline_projection",
    "build_medication_reminders",
    "RecognitionJobError",
    "RecognitionJobStore",
    "get_job",
    "submit_job",
    "build_interpretation",
    "interpretation_summary",
    "MeasurementKindError",
    "normalize_measurement",
    "saveable_from_observation",
    "PatientRecordConflict",
    "PatientRecordError",
    "PatientRecordStore",
    "parse_report_draft",
    "build_runtime",
    "Workspace",
    "StaleCompositionError",
    "subagent_kind_overrides",
    "__version__",
    "ExecutionManager",
    "ExecutionError",
    "ExecutionBusy",
    "DirectoryChanged",
    "MAX_BODY_BYTES",
    "MAX_IMAGE_BODY_BYTES",
    "MEASUREMENT_SLUGS",
    "_default_baseline_projection",
    "_session_baseline_projection",
    "_consultation_patient_id",
    "_MEDICATION_SCHEDULES_FILENAME",
    "_TIME_RE",
    "_medication_schedules_path",
    "_load_medication_schedules",
    "_save_medication_schedules",
    "_persist_engine_state",
]
