"""Persistent, patient-visible longitudinal records for the desktop prototype.

The store class is assembled from schema/assets/queries mixins; this module
re-exports it beside the two error types and the data-root helper. Everything
else that used to be bound here -- sqlite3, Decimal, the schema constants, the
baseline validators -- belongs to those mixins and was left over from when
this file was the whole store.
"""

from __future__ import annotations

from .store import PatientRecordStore
from .validation import (
    PatientRecordConflict,
    PatientRecordError,
    default_patient_data_root,
)

__all__ = [
    "PatientRecordConflict",
    "PatientRecordError",
    "PatientRecordStore",
    "default_patient_data_root",
]
