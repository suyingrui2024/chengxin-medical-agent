"""Shared imports, constants and module-level helpers for the package."""
from __future__ import annotations
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4
from ...extensions.glucose import convert_glucose
from ...measurement_kinds import (
    KNOWN_KINDS,
    MeasurementKindError,
    kind_spec,
    normalize_measurement,
    trend_summary,
)
from ...medical_images import InspectedImage, validate_recognition

from ..constants import BASELINE_FIELDS, GENERIC_MEASUREMENT_CONTEXTS, GLUCOSE_MEASUREMENT_CONTEXTS, SCHEMA_VERSION, _MEASUREMENT_CONTEXT_LABELS


from ..validation import PatientRecordConflict, PatientRecordError, _clean_text, _profile_dict, _render_decimal, _time, default_patient_data_root, utc_now, validate_baseline_patch





__all__ = [
    "json",
    "os",
    "re",
    "sqlite3",
    "sys",
    "contextmanager",
    "datetime",
    "timedelta",
    "timezone",
    "Decimal",
    "sha256",
    "Path",
    "RLock",
    "Any",
    "Iterator",
    "uuid4",
    "convert_glucose",
    "KNOWN_KINDS",
    "MeasurementKindError",
    "kind_spec",
    "normalize_measurement",
    "trend_summary",
    "InspectedImage",
    "validate_recognition",
    "BASELINE_FIELDS",
    "GENERIC_MEASUREMENT_CONTEXTS",
    "GLUCOSE_MEASUREMENT_CONTEXTS",
    "SCHEMA_VERSION",
    "_MEASUREMENT_CONTEXT_LABELS",
    "PatientRecordConflict",
    "PatientRecordError",
    "_clean_text",
    "_profile_dict",
    "_render_decimal",
    "_time",
    "default_patient_data_root",
    "utc_now",
    "validate_baseline_patch",
]
