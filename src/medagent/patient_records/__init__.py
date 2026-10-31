"""Persistent, patient-visible longitudinal records for the desktop prototype."""

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
from ..extensions.glucose import convert_glucose
from ..measurement_kinds import (
    KNOWN_KINDS,
    MeasurementKindError,
    kind_spec,
    normalize_measurement,
    trend_summary,
)
from ..medical_images import InspectedImage, validate_recognition

from .constants import (SCHEMA_VERSION, BASELINE_FIELDS, BASELINE_LIST_LIMIT, BASELINE_STRING_LIMIT, _ALLERGY_KINDS, MEASUREMENT_CONTEXTS, GENERIC_MEASUREMENT_CONTEXTS, GLUCOSE_MEASUREMENT_CONTEXTS, _MEASUREMENT_CONTEXT_LABELS)
from .store import (PatientRecordStore)
from .validation import (_render_decimal, PatientRecordError, PatientRecordConflict, utc_now, default_patient_data_root, _clean_text, _time, _baseline_optional_text, _baseline_number, _baseline_string_list, _baseline_object_list, validate_baseline_patch, _profile_dict)
