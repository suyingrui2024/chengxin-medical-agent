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

from .constants import BASELINE_FIELDS, BASELINE_LIST_LIMIT, BASELINE_STRING_LIMIT, _ALLERGY_KINDS

def _render_decimal(value: Decimal) -> str:
    """Receipt rendering shared with the glucose converter: <=4 decimals,
    trailing zeros stripped. Used for non-glucose trend points."""
    output = format(value.quantize(Decimal("0.0001")), "f")
    return output.rstrip("0").rstrip(".")


class PatientRecordError(ValueError):
    pass


class PatientRecordConflict(PatientRecordError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_patient_data_root() -> Path:
    override = os.environ.get("MEDAGENT_PATIENT_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "Chengxin" / "patient-data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chengxin" / "patient-data"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "chengxin" / "patient-data"


def _clean_text(value: Any, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise PatientRecordError(f"{name}格式错误")
    text = " ".join(value.split())
    if (required and not text) or len(text) > maximum:
        raise PatientRecordError(f"{name}格式错误")
    return text


def _time(value: Any, name: str, *, required: bool = False) -> str | None:
    if value in {None, ""} and not required:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise PatientRecordError(f"{name}格式错误")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PatientRecordError(f"{name}格式错误") from None
    if parsed.tzinfo is None:
        raise PatientRecordError(f"{name}必须包含时区")
    return parsed.isoformat().replace("+00:00", "Z")


def _baseline_optional_text(value: Any, name: str, maximum: int = BASELINE_STRING_LIMIT) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PatientRecordError(f"{name}格式错误")
    text = " ".join(value.split())
    if len(text) > maximum:
        raise PatientRecordError(f"{name}格式错误")
    return text or None


def _baseline_number(value: Any, name: str, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PatientRecordError(f"{name}格式错误")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:  # NaN / infinite
        raise PatientRecordError(f"{name}格式错误")
    if not minimum <= number <= maximum:
        raise PatientRecordError(f"{name}格式错误")
    return number


def _baseline_string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > BASELINE_LIST_LIMIT:
        raise PatientRecordError(f"{name}格式错误")
    items = []
    for item in value:
        if not isinstance(item, str):
            raise PatientRecordError(f"{name}格式错误")
        text = " ".join(item.split())
        if not text or len(text) > BASELINE_STRING_LIMIT:
            raise PatientRecordError(f"{name}格式错误")
        items.append(text)
    return items


def _baseline_object_list(
    value: Any, name: str, required: tuple[str, ...], optional: tuple[str, ...]
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > BASELINE_LIST_LIMIT:
        raise PatientRecordError(f"{name}格式错误")
    items = []
    for entry in value:
        if not isinstance(entry, dict):
            raise PatientRecordError(f"{name}格式错误")
        if set(entry) - set(required) - set(optional):
            raise PatientRecordError(f"{name}格式错误")
        cleaned: dict[str, Any] = {}
        for key in required:
            text = _baseline_optional_text(entry.get(key), name)
            if text is None:
                raise PatientRecordError(f"{name}格式错误")
            cleaned[key] = text
        for key in optional:
            cleaned[key] = _baseline_optional_text(entry.get(key), name)
        items.append(cleaned)
    return items


def validate_baseline_patch(patch: Any) -> dict[str, Any]:
    """Validate a partial baseline update; unknown keys are rejected.

    Returns only the normalized fields present in ``patch``. Raises
    PatientRecordError before any write, so a rejected patch cannot
    pollute stored data.
    """
    if not isinstance(patch, dict):
        raise PatientRecordError("基线信息格式错误")
    allowed = {"display_name"} | set(BASELINE_FIELDS)
    if set(patch) - allowed:
        raise PatientRecordError("基线信息格式错误")
    normalized: dict[str, Any] = {}
    if "display_name" in patch:
        normalized["display_name"] = _clean_text(patch["display_name"], "档案名称", 80)
    if "birth_year" in patch:
        year = patch["birth_year"]
        if year is None:
            normalized["birth_year"] = None
        elif isinstance(year, bool) or not isinstance(year, int):
            raise PatientRecordError("出生年格式错误")
        elif not 1900 <= year <= 2100:
            raise PatientRecordError("出生年格式错误")
        else:
            normalized["birth_year"] = year
    if "sex" in patch:
        normalized["sex"] = _baseline_optional_text(patch["sex"], "性别")
    if "height_cm" in patch:
        normalized["height_cm"] = _baseline_number(patch["height_cm"], "身高", 20, 300)
    if "weight_kg" in patch:
        normalized["weight_kg"] = _baseline_number(patch["weight_kg"], "体重", 0.5, 1000)
    if "chronic_conditions" in patch:
        normalized["chronic_conditions"] = _baseline_string_list(
            patch["chronic_conditions"], "慢病清单"
        )
    if "allergies" in patch:
        allergies = _baseline_object_list(
            patch["allergies"], "过敏史", ("name",), ("kind",)
        )
        for allergy in allergies:
            kind = allergy.get("kind")
            if kind is not None and kind not in _ALLERGY_KINDS:
                raise PatientRecordError("过敏史格式错误")
            allergy["kind"] = kind if kind is not None else "other"
        normalized["allergies"] = allergies
    if "medications" in patch:
        normalized["medications"] = _baseline_object_list(
            patch["medications"], "长期用药", ("name",), ("purpose",)
        )
    if "surgical_history" in patch:
        normalized["surgical_history"] = _baseline_optional_text(
            patch["surgical_history"], "手术史"
        )
    if "birth_year_month" in patch:
        month = patch["birth_year_month"]
        if month is None:
            normalized["birth_year_month"] = None
        elif not isinstance(month, str) or not re.fullmatch(
            r"(19|20)\d\d-(0[1-9]|1[0-2])", month
        ):
            raise PatientRecordError("出生年月格式错误")
        else:
            normalized["birth_year_month"] = month
    return normalized


def _profile_dict(row: sqlite3.Row) -> dict[str, Any]:
    profile = dict(row)
    for key in ("chronic_conditions", "allergies", "medications"):
        raw = profile.get(key)
        try:
            profile[key] = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            profile[key] = []
        if not isinstance(profile[key], list):
            profile[key] = []
    profile["archived"] = bool(profile.get("archived_at"))
    return profile
