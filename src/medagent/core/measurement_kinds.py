"""Measurement-kind registry for P2b (design doc §4.2).

Pure-function module: stdlib only, no database, no server, no consultation
kernel, no conclusion sheet. The coordinator-owned wiring (patient_records.py
``add_measurement`` / ``revise_observation``, server routes, frontend) calls
into this module; nothing here writes anything anywhere.

Design notes
------------
- ``blood_glucose`` reuses the existing semantics from
  ``medagent.extensions.glucose.convert_glucose``: the conventional factor 18
  between mg/dL and mmol/L, Decimal arithmetic, at most 4 displayed decimals
  (ROUND_HALF_UP, trailing zeros stripped). This module deliberately does NOT
  import that extension, so the registry stays dependency-free; the test file
  cross-checks parity against ``convert_glucose`` instead.
- Every kind defines: accepted units (with Chinese aliases), a canonical unit,
  a *plausible* range, a normalizer, and a trend summary. Ranges are
  engineering-v1 plausibility guards, NOT clinical normal ranges and NOT
  diagnostic thresholds: a value outside the range is still accepted, it is
  only reported in ``warnings`` and the result is marked
  ``clarity="out_of_range"``. Nothing here judges high/low, diagnoses, or
  suggests medication.
- Trend statistics are descriptive only (min / max / mean / latest). An empty
  sample returns ``None``, never an exception. Callers must pass samples in
  chronological order; ``latest`` is simply the last element.
- ``saveable_from_observation`` is a read-only classifier for the
  "save transcription as measurement" flow (§4.2: 血压计/体温计照片 → 结构化
  测量）. It never persists anything. A ``document_number`` observation is
  saveable only after the patient has confirmed or corrected it
  (``verification_status`` in {"patient_confirmed", "patient_corrected"}),
  mirroring the shareable-entry governance: model-extracted values alone are
  never promoted to measurements.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Kind specifications
# ---------------------------------------------------------------------------
#
# Each spec:
#   display_name: patient-facing Chinese label.
#   canonical_unit: unit all trend statistics are expressed in.
#   units: accepted input units -> canonical unit (aliases normalized here).
#   components: for scalar kinds a single "value" component; blood_pressure
#     has "systolic" / "diastolic".
#   plausible_range: {component: (low, high)} in canonical units, inclusive.
#     Engineering-v1 plausibility guards only -- see module docstring.
#   default_unit: unit assumed ONLY when the transcription extractor cannot
#     find an explicit unit token (flagged via ``unit_inferred`` so the UI
#     must show it for patient confirmation). None means "no default":
#     the extractor must see an explicit unit.

_SPECS: dict[str, dict[str, Any]] = {
    "blood_glucose": {
        "display_name": "血糖",
        "canonical_unit": "mmol/L",
        "units": {
            "mmol/L": "mmol/L",
            "mmol/l": "mmol/L",
            "mmol／L": "mmol/L",
            "毫摩尔/升": "mmol/L",
            "毫摩尔每升": "mmol/L",
            "mg/dL": "mg/dL",
            "mg/dl": "mg/dL",
            "mg／dL": "mg/dL",
            "毫克/分升": "mg/dL",
            "毫克每分升": "mg/dL",
        },
        "components": ("value",),
        "plausible_range": {"value": (Decimal("1"), Decimal("40"))},
        "default_unit": None,
    },
    "blood_pressure": {
        "display_name": "血压",
        "canonical_unit": "mmHg",
        "units": {
            "mmHg": "mmHg",
            "mmhg": "mmHg",
            "毫米汞柱": "mmHg",
        },
        "components": ("systolic", "diastolic"),
        "plausible_range": {
            "systolic": (Decimal("50"), Decimal("260")),
            "diastolic": (Decimal("30"), Decimal("160")),
        },
        "default_unit": "mmHg",
    },
    "temperature": {
        "display_name": "体温",
        "canonical_unit": "°C",
        "units": {
            "°C": "°C",
            "℃": "°C",
            "C": "°C",
            "摄氏度": "°C",
            "摄氏": "°C",
            "°F": "°F",
            "℉": "°F",
            "F": "°F",
            "华氏度": "°F",
            "华氏": "°F",
        },
        "components": ("value",),
        "plausible_range": {"value": (Decimal("35"), Decimal("42.5"))},
        "default_unit": "°C",
    },
    "weight": {
        "display_name": "体重",
        "canonical_unit": "kg",
        "units": {
            "kg": "kg",
            "千克": "kg",
            "公斤": "kg",
            "lb": "lb",
            "lbs": "lb",
            "磅": "lb",
            "斤": "斤",
        },
        "components": ("value",),
        "plausible_range": {"value": (Decimal("2"), Decimal("350"))},
        "default_unit": "kg",
    },
    "blood_oxygen": {
        "display_name": "血氧饱和度",
        "canonical_unit": "%",
        "units": {
            "%": "%",
            "％": "%",
        },
        "components": ("value",),
        "plausible_range": {"value": (Decimal("70"), Decimal("100"))},
        "default_unit": "%",
    },
}

KNOWN_KINDS: tuple[str, ...] = tuple(_SPECS)

# Conversion constants (documented, conventional -- not clinical judgments).
_GLUCOSE_FACTOR = Decimal("18")  # same factor as extensions.glucose
_LB_PER_KG = Decimal("2.2046226218")
_JIN_PER_KG = Decimal("2")  # 1 kg = 2 斤


class MeasurementKindError(ValueError):
    """Raised for unknown kinds, unknown units, or unparseable values."""


def kind_spec(kind: str) -> dict[str, Any]:
    """Return a copy of the spec for ``kind``; raise on unknown kinds."""
    if not isinstance(kind, str) or kind not in _SPECS:
        raise MeasurementKindError(f"不支持的测量项目：{kind!r}")
    spec = _SPECS[kind]
    return {
        "kind": kind,
        "display_name": spec["display_name"],
        "canonical_unit": spec["canonical_unit"],
        "units": tuple(spec["units"]),
        "components": spec["components"],
        "plausible_range": {
            name: (str(low), str(high))
            for name, (low, high) in spec["plausible_range"].items()
        },
    }


def canonical_unit(kind: str) -> str:
    return _SPECS[_require_kind(kind)]["canonical_unit"]


def _require_kind(kind: Any) -> str:
    if not isinstance(kind, str) or kind not in _SPECS:
        raise MeasurementKindError(f"不支持的测量项目：{kind!r}")
    return kind


def _normalize_unit(kind: str, unit: Any) -> str:
    """Resolve an input unit to its canonical form; raise on unknown units."""
    if not isinstance(unit, str):
        raise MeasurementKindError(f"{_SPECS[kind]['display_name']}单位格式错误")
    cleaned = " ".join(unit.split())
    resolved = _SPECS[kind]["units"].get(cleaned)
    if resolved is None:
        raise MeasurementKindError(
            f"{_SPECS[kind]['display_name']}单位只支持："
            + "、".join(sorted(set(_SPECS[kind]["units"])))
        )
    return resolved


def _parse_decimal(raw: Any, label: str) -> Decimal:
    if isinstance(raw, bool):
        raise MeasurementKindError(f"{label}数值格式错误")
    if isinstance(raw, (int, float)):
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise MeasurementKindError(f"{label}数值格式错误") from None
    elif isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if not re.fullmatch(r"[+\-−－]?\d{1,12}(?:\.\d{1,12})?", text):
            raise MeasurementKindError(f"{label}数值格式错误")
        value = Decimal(text.replace("−", "-").replace("－", "-"))
    else:
        raise MeasurementKindError(f"{label}数值格式错误")
    if not value.is_finite() or value < 0:
        raise MeasurementKindError(f"{label}数值格式错误")
    return value


def _render(value: Decimal) -> str:
    """Mirror the glucose receipt rendering: <=4 decimals, zeros stripped."""
    rounded = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if value and not rounded:
        rounded = value.quantize(
            Decimal(1).scaleb(value.adjusted() - 3), rounding=ROUND_HALF_UP
        )
    rendered = format(rounded, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _to_canonical(kind: str, unit: str, value: Decimal) -> Decimal:
    """Convert one component value into the kind's canonical unit."""
    if kind == "blood_glucose":
        if unit == "mg/dL":
            return value / _GLUCOSE_FACTOR
        return value
    if kind == "temperature":
        if unit == "°F":
            return (value - Decimal("32")) * Decimal("5") / Decimal("9")
        return value
    if kind == "weight":
        if unit == "lb":
            return value / _LB_PER_KG
        if unit == "斤":
            return value / _JIN_PER_KG
        return value
    # blood_pressure (mmHg) and blood_oxygen (%) are already canonical.
    return value


def _check_range(kind: str, values: dict[str, Decimal]) -> list[str]:
    warnings: list[str] = []
    display = _SPECS[kind]["display_name"]
    component_labels = {"systolic": "收缩压", "diastolic": "舒张压"}
    for component, value in values.items():
        low, high = _SPECS[kind]["plausible_range"][component]
        if value < low or value > high:
            label = component_labels.get(component, display)
            warnings.append(
                f"{label} {_render(value)} {_SPECS[kind]['canonical_unit']} "
                f"超出常见范围（{_render(low)}–{_render(high)}），请核对后仍可保存"
            )
    if kind == "blood_pressure":
        if values["systolic"] <= values["diastolic"]:
            warnings.append("收缩压不高于舒张压，请核对两个数值是否写反")
    return warnings


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_measurement(
    kind: Any, value: Any, unit: Any = None
) -> dict[str, Any]:
    """Normalize one measurement into canonical units.

    Args:
        kind: one of KNOWN_KINDS.
        value: scalar (number / numeric string) for single-component kinds;
            for blood_pressure one of: "120/80" (string), a mapping with
            systolic/diastolic keys, or a 2-tuple/list ``(systolic, diastolic)``.
        unit: input unit; required for scalar kinds, optional for
            blood_pressure (defaults to mmHg).

    Returns a dict with keys: kind, display_name, reported_value (verbatim
    input, stringified), reported_unit, canonical_unit, values (mapping
    component -> Decimal in canonical units), warnings (list of str),
    clarity ("clear" or "out_of_range"). Out-of-range values are accepted,
    never rejected -- warnings are the only signal.
    """
    kind = _require_kind(kind)
    spec = _SPECS[kind]
    if kind == "blood_pressure":
        systolic_raw: Any
        diastolic_raw: Any
        if isinstance(value, str):
            match = re.fullmatch(
                r"\s*(\d{1,3}(?:\.\d{1,2})?)\s*/\s*(\d{1,3}(?:\.\d{1,2})?)\s*",
                value,
            )
            if match is None:
                raise MeasurementKindError("血压数值请使用“收缩压/舒张压”格式，如 120/80")
            systolic_raw, diastolic_raw = match.groups()
        elif isinstance(value, Mapping):
            if "systolic" not in value or "diastolic" not in value:
                raise MeasurementKindError("血压数值须同时包含收缩压与舒张压")
            systolic_raw, diastolic_raw = value["systolic"], value["diastolic"]
        elif isinstance(value, (tuple, list)) and len(value) == 2:
            systolic_raw, diastolic_raw = value
        else:
            raise MeasurementKindError("血压数值请使用“收缩压/舒张压”格式，如 120/80")
        resolved_unit = (
            _normalize_unit(kind, unit) if unit not in (None, "") else "mmHg"
        )
        if resolved_unit != "mmHg":
            raise MeasurementKindError("血压单位只支持 mmHg")
        systolic = _parse_decimal(systolic_raw, "收缩压")
        diastolic = _parse_decimal(diastolic_raw, "舒张压")
        if systolic == 0 or diastolic == 0:
            raise MeasurementKindError("血压数值须大于 0")
        values = {"systolic": systolic, "diastolic": diastolic}
        reported = f"{_render(systolic)}/{_render(diastolic)}"
    else:
        if unit in (None, ""):
            raise MeasurementKindError(f"{spec['display_name']}单位不能为空")
        resolved_unit = _normalize_unit(kind, unit)
        parsed = _parse_decimal(value, spec["display_name"])
        if parsed == 0 and kind != "blood_glucose":
            # Zero is not a plausible reading for temp/weight/SpO2 either;
            # glucose keeps the existing ">= 0" rule (see patient_records).
            raise MeasurementKindError(f"{spec['display_name']}数值须大于 0")
        values = {"value": _to_canonical(kind, resolved_unit, parsed)}
        reported = str(value).strip() if isinstance(value, str) else str(value)
    warnings = _check_range(kind, values)
    return {
        "kind": kind,
        "display_name": spec["display_name"],
        "reported_value": reported,
        "reported_unit": resolved_unit,
        "canonical_unit": spec["canonical_unit"],
        "values": values,
        "warnings": warnings,
        "clarity": "out_of_range" if any(
            _range_hit(kind, component, val)
            for component, val in values.items()
        ) else "clear",
    }


def _range_hit(kind: str, component: str, value: Decimal) -> bool:
    low, high = _SPECS[kind]["plausible_range"][component]
    return value < low or value > high


# ---------------------------------------------------------------------------
# Trend statistics
# ---------------------------------------------------------------------------

def trend_summary(kind: Any, samples: Any) -> dict[str, Any] | None:
    """Descriptive trend statistics for one kind; None for an empty sample.

    Each sample may be either a dict returned by ``normalize_measurement``
    (recognized by its ``values`` key) or a raw measurement dict with
    ``value``/``unit`` keys (scalar kinds) or ``systolic``/``diastolic``/
    ``unit`` keys (blood_pressure). Samples are used in the order given;
    ``latest`` is the last sample. Statistics are rendered as strings with
    the same <=4-decimal policy as the glucose receipt; no interpretation
    (no high/low judgment) is attached.
    """
    kind = _require_kind(kind)
    if samples is None:
        return None
    normalized: list[dict[str, Any]] = []
    for sample in samples:
        if isinstance(sample, Mapping) and "values" in sample:
            if sample.get("kind") != kind:
                raise MeasurementKindError("趋势样本的测量项目不一致")
            normalized.append(sample)  # type: ignore[arg-type]
        elif isinstance(sample, Mapping):
            if kind == "blood_pressure":
                normalized.append(
                    normalize_measurement(
                        kind,
                        {
                            "systolic": sample.get("systolic"),
                            "diastolic": sample.get("diastolic"),
                        },
                        sample.get("unit"),
                    )
                )
            else:
                normalized.append(
                    normalize_measurement(kind, sample.get("value"), sample.get("unit"))
                )
        else:
            raise MeasurementKindError("趋势样本格式错误")
    if not normalized:
        return None
    series: dict[str, dict[str, Any]] = {}
    for component in _SPECS[kind]["components"]:
        column = [entry["values"][component] for entry in normalized]
        mean = sum(column) / Decimal(len(column))
        series[component] = {
            "minimum": _render(min(column)),
            "maximum": _render(max(column)),
            "mean": _render(mean),
            "latest": _render(column[-1]),
            "count": len(column),
        }
    return {
        "kind": kind,
        "display_name": _SPECS[kind]["display_name"],
        "canonical_unit": _SPECS[kind]["canonical_unit"],
        "count": len(normalized),
        "series": series,
        "interpretation": "仅计算描述性统计；未判断高低、诊断或用药。",
    }


# ---------------------------------------------------------------------------
# Save-as-measurement classification for transcribed observations
# ---------------------------------------------------------------------------

_CONFIRMED = {"patient_confirmed", "patient_corrected"}

# (regex, kind, unit token group) -- searched in the observation's combined
# text (context + display_name + value_text). Order matters: blood pressure's
# slash pair is checked before any single-number pattern.
_BP_PAIR = re.compile(
    r"(\d{1,3}(?:\.\d{1,2})?)\s*/\s*(\d{1,3}(?:\.\d{1,2})?)"
)
_NUMBER = re.compile(r"(\d{1,12}(?:\.\d{1,12})?)")

_LABELS: dict[str, tuple[str, ...]] = {
    "blood_pressure": ("血压", "blood pressure", "bp"),
    "temperature": ("体温", "温度", "temperature"),
    "weight": ("体重", "weight"),
    "blood_oxygen": ("血氧", "氧饱和度", "spo2", "血氧饱和度"),
    "blood_glucose": ("血糖", "blood glucose"),
}

_UNIT_TOKENS: dict[str, tuple[str, ...]] = {
    "blood_pressure": ("mmhg", "毫米汞柱"),
    "temperature": ("°c", "℃", "摄氏", "°f", "℉", "华氏"),
    "weight": ("kg", "千克", "公斤", "lb", "磅", "斤"),
    "blood_oxygen": ("%", "％"),
    "blood_glucose": ("mmol/l", "mg/dl", "毫摩尔", "毫克"),
}


def _observation_text(observation: Mapping[str, Any]) -> str:
    parts = [
        str(observation.get("context") or ""),
        str(observation.get("display_name") or ""),
        str(observation.get("value_text") or ""),
    ]
    return " ".join(part for part in parts if part)


def _has_label(text: str, kind: str) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in _LABELS[kind])


def _explicit_unit(text: str, kind: str) -> str | None:
    lowered = text.lower()
    for token in _UNIT_TOKENS[kind]:
        if token.lower() in lowered:
            # Map the matched token back through the spec's unit table.
            for accepted in _SPECS[kind]["units"]:
                if accepted.lower() == token.lower() or (
                    token in ("°c", "℃") and accepted == "°C"
                ) or (token in ("°f", "℉") and accepted == "°F"):
                    return accepted
            return token
    return None


def saveable_from_observation(
    observation: Any,
) -> dict[str, Any]:
    """Classify whether a transcribed observation can be saved as a measurement.

    Always returns a dict (never raises on shape problems, so callers can
    render a reason): keys ``saveable`` (bool), ``kind`` (str | None),
    ``values`` (normalized ``normalize_measurement`` output | None),
    ``unit`` (str | None), ``unit_inferred`` (bool),
    ``extraction_rule`` (str | None), ``reason`` (str | None).

    Rules:
    - Only ``kind == "document_number"`` observations whose
      ``verification_status`` is patient_confirmed/patient_corrected qualify.
      Model-extracted or still-pending values are never saveable.
    - The numeric value comes from ``value_text`` when it parses, else from
      the first number in the combined context text.
    - Blood pressure: a ``systolic/diastolic`` slash pair in the text, with a
      血压 label or an explicit mmHg token.
    - Other kinds: a matching label in context/display_name plus a number;
      the unit must be explicit in the text, except that the kind's
      ``default_unit`` may be inferred (flagged) when the label is present
      but no unit token appears -- EXCEPT blood_glucose, which never infers
      a unit (mmol/L vs mg/dL changes the value by 18x).
    - If two different kinds both match, the observation is ambiguous and
      not saveable; the patient must disambiguate. This function never
      writes to the database; the coordinator-owned save path re-validates
      through ``normalize_measurement`` before persisting.
    """
    if not isinstance(observation, Mapping):
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "观测记录格式错误",
        }
    if observation.get("kind") != "document_number":
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "只有图片转录的数值观测可以另存为测量",
        }
    if observation.get("verification_status") not in _CONFIRMED:
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "该数值尚未经患者确认，确认后才可另存为测量",
        }
    text = _observation_text(observation)
    value_text = str(observation.get("value_text") or "").strip()

    # Blood pressure first: its slash pair must not be mistaken for two
    # single-value readings.
    pair = _BP_PAIR.search(text)
    if pair and (_has_label(text, "blood_pressure") or _explicit_unit(text, "blood_pressure")):
        try:
            normalized = normalize_measurement(
                "blood_pressure", f"{pair.group(1)}/{pair.group(2)}", "mmHg"
            )
        except MeasurementKindError as exc:
            return {
                "saveable": False,
                "kind": None,
                "values": None,
                "unit": None,
                "unit_inferred": False,
                "extraction_rule": None,
                "reason": str(exc),
            }
        return {
            "saveable": True,
            "kind": "blood_pressure",
            "values": normalized,
            "unit": "mmHg",
            "unit_inferred": _explicit_unit(text, "blood_pressure") is None,
            "extraction_rule": "bp_slash_pair: 文本中的“收缩压/舒张压”双值，单位 mmHg",
            "reason": None,
        }

    if _has_label(text, "blood_pressure"):
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "血压需要“收缩压/舒张压”双值（如 120/80），文本中未找到双值",
        }
    candidates: list[str] = []
    for kind in ("temperature", "weight", "blood_oxygen", "blood_glucose"):
        if _has_label(text, kind):
            candidates.append(kind)
    if not candidates:
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "文本中没有可识别的测量项目标签（血压/体温/体重/血氧/血糖）",
        }
    if len(candidates) > 1:
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "文本同时匹配多个测量项目（"
            + "、".join(candidates)
            + "），请患者先确认是哪一项",
        }
    kind = candidates[0]
    # Numeric source: value_text first, else first number in combined text.
    number: str | None = None
    if value_text and re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", value_text):
        number = value_text
    else:
        match = _NUMBER.search(text)
        if match:
            number = match.group(1)
    if number is None:
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": "文本中没有可提取的数值",
        }
    explicit = _explicit_unit(text, kind)
    if explicit is None:
        if kind == "blood_glucose":
            return {
                "saveable": False,
                "kind": None,
                "values": None,
                "unit": None,
                "unit_inferred": False,
                "extraction_rule": None,
                "reason": "血糖未标明单位（mmol/L 与 mg/dL 相差 18 倍），请患者先确认单位",
            }
        unit = _SPECS[kind]["default_unit"]
        inferred = True
    else:
        unit = explicit
        inferred = False
    try:
        normalized = normalize_measurement(kind, number, unit)
    except MeasurementKindError as exc:
        return {
            "saveable": False,
            "kind": None,
            "values": None,
            "unit": None,
            "unit_inferred": False,
            "extraction_rule": None,
            "reason": str(exc),
        }
    return {
        "saveable": True,
        "kind": kind,
        "values": normalized,
        "unit": normalized["reported_unit"],
        "unit_inferred": inferred,
        "extraction_rule": (
            "number_plus_unit_in_context: value_text 数值 + 文本中的单位"
            if not inferred
            else "number_with_label_in_context: value_text 数值 + 项目标签，单位为默认推断，保存前须患者确认"
        ),
        "reason": None,
    }
