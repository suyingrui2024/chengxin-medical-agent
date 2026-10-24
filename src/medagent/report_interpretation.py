"""Deterministic report interpretation (contract ``report-interpretation/v1``).

P3a (design doc SS5.1): this module sits *above* the faithful transcription
layer and never replaces it. It takes only patient-confirmed observations,
grades each one against ``content/reference_ranges.json`` with deterministic
rules, and drafts plain-language explanations from fixed templates.

Rules that must never be relaxed here:

- Grading is computed by code, never by a model. A model may later draft
  explanation wording, but the grade always comes from this module, and a
  model draft still passes through the lint below before it is shown.
- No reference range, no grade: an item without a usable range is
  ``无法判断`` (fail-closed) -- it is explained, never graded.
- Unconfirmed observations (anything not ``patient_confirmed`` /
  ``patient_corrected``) are excluded from grading and from explanations,
  and are listed under ``excluded`` only. They never leak into the
  interpretation summary used by the attach extension.
- No diagnosis, no dosing, no prescription wording. A lint hit empties
  that item's explanation and marks ``lint_failed``; it never voids the
  whole sheet (the grade itself is computed, not prose, and stays).

Ranges file format (owned by the P3-B content worker; this module only
reads it)::

    {"version": "1.0",
     "items": {"<item_key>": {"name": "...", "unit": "...",
                              "ref_low": <number>, "ref_high": <number>,
                              "yellow_low": <number>, "yellow_high": <number>,
                              "red_low": <number>, "red_high": <number>}}}

All thresholds are in the item's ``unit`` and form nested bands
(``red`` outermost, then ``yellow``, then ``ref``), as the ranges table
itself enforces (present low thresholds sort ``red_low <= yellow_low <=
ref_low``; high thresholds sort ``ref_high <= yellow_high <= red_high``):

- Value inside ``[ref_low, ref_high]`` -> ``绿``. A side with no ``ref``
  bound is one-sided: values on that side are green (e.g. creatinine
  has no lower bound, so a low creatinine is green, not unknown).
- Value outside the ref interval but inside the yellow band -> ``黄``.
- Value beyond the yellow band -> ``红``. The ``red_*`` thresholds are
  the nominal outer extent of the red band, not grading gates: a value
  beyond ``red_high`` is still red, never unknown.
- Fail-closed: on a side with a ``ref`` bound but no ``yellow`` edge,
  an out-of-range value grades ``无法判断`` -- the yellow edge is the
  gate that separates yellow from red, so without it the two cannot be
  told apart. (This is the urea case in the corpus: low side has a ref
  bound but no yellow_low, so a low urea is 无法判断.)

Two further optional keys are honoured when present and ignored when
absent: ``emergency_low`` / ``emergency_high`` -- a red value at or
beyond them maps the *action* (not the grade) to ``急诊``; without them
a red value maps to ``尽快就诊``. This module never outputs ``急诊``
on its own thresholds alone.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from medagent.conclusion import BANNED_PHRASES

CONTRACT_VERSION = "report-interpretation/v1"
DISCLAIMER = "由 AI 生成，未经医生审核；分级依据为工程版参考区间"

CONFIRMED_STATUSES = ("patient_confirmed", "patient_corrected")

GRADE_GREEN = "绿"
GRADE_YELLOW = "黄"
GRADE_RED = "红"
GRADE_UNKNOWN = "无法判断"

ACTION_OBSERVE = "观察"
ACTION_RECHECK = "复查"
ACTION_SEEK_CARE = "尽快就诊"
ACTION_EMERGENCY = "急诊"

# Dose / prescription wording must never reach the patient area. The
# numeric pattern mirrors education.DOSE_PATTERN (same intent: a concrete
# medication instruction smuggled in prose); the prescription terms are
# added here because interpretation drafts, unlike web citations, could
# also carry a bare "take this" instruction without a number.
_DOSE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mg|毫克|ml|片|粒|次\s*/\s*日)"
    r"|每日\s*\d+\s*次"
    r"|\d+\s*次\s*/\s*日"
    r"|服用"
    r"|处方"
)

_NUMBER_RE = re.compile(r"^[+\-]?\d+(?:\.\d+)?$")

# Unit aliases normalised before comparison. Glucose conversion uses the
# established factor 18 (mg/dL / 18 = mmol/L), the same factor as
# extensions.glucose.convert_glucose and measurement_kinds.py.
_UNIT_ALIASES = {
    "mmol/l": "mmol/L",
    "mmol／l": "mmol/L",
    "毫摩尔/升": "mmol/L",
    "毫摩尔每升": "mmol/L",
    "mg/dl": "mg/dL",
    "mg／dl": "mg/dL",
    "毫克/分升": "mg/dL",
    "毫克每分升": "mg/dL",
    "℃": "°C",
    "摄氏度": "°C",
    "摄氏": "°C",
    "℉": "°F",
    "华氏度": "°F",
    "华氏": "°F",
    "千克": "kg",
    "公斤": "kg",
    "磅": "lb",
    "毫米汞柱": "mmHg",
    "％": "%",
}


def _norm_unit(unit: Any) -> str:
    if not isinstance(unit, str):
        return ""
    cleaned = " ".join(unit.split())
    return _UNIT_ALIASES.get(cleaned, _UNIT_ALIASES.get(cleaned.lower(), cleaned))


def _to_unit(value: Decimal, from_unit: str, to_unit: str) -> Decimal | None:
    """Convert ``value`` from ``from_unit`` to ``to_unit``; None if unknown."""
    if from_unit == to_unit:
        return value
    if from_unit == "mg/dL" and to_unit == "mmol/L":
        return value / Decimal("18")
    if from_unit == "mmol/L" and to_unit == "mg/dL":
        return value * Decimal("18")
    if from_unit == "°F" and to_unit == "°C":
        return (value - Decimal("32")) * Decimal("5") / Decimal("9")
    if from_unit == "°C" and to_unit == "°F":
        return value * Decimal("9") / Decimal("5") + Decimal("32")
    if from_unit == "lb" and to_unit == "kg":
        return value / Decimal("2.2046226218")
    if from_unit == "kg" and to_unit == "lb":
        return value * Decimal("2.2046226218")
    if from_unit == "斤" and to_unit == "kg":
        return value / Decimal("2")
    if from_unit == "kg" and to_unit == "斤":
        return value * Decimal("2")
    return None


def _parse_number(raw: Any) -> Decimal | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            return None
    elif isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, str):
        text = raw.strip().replace(",", "").replace("，", "")
        if not _NUMBER_RE.match(text):
            return None
        try:
            value = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    return value if value.is_finite() else None


def _threshold(item: Mapping[str, Any], key: str) -> Decimal | None:
    return _parse_number(item.get(key))


def lint_explanation(text: Any) -> tuple[str, bool, list[str]]:
    """Lint one explanation draft.

    Returns ``(clean_text, lint_failed, reasons)``. On any hit the clean
    text is ``""``: the item keeps its grade but shows no explanation.
    """
    if not isinstance(text, str) or not text:
        return "", False, []
    reasons: list[str] = []
    for phrase in BANNED_PHRASES:
        if phrase in text:
            reasons.append(f"禁用语：{phrase}")
    if _DOSE_PATTERN.search(text):
        reasons.append("剂量/处方措辞")
    if reasons:
        return "", True, reasons
    return text, False, []


def grade_observation_value(
    value_text: Any, unit: Any, item: Mapping[str, Any]
) -> tuple[str, str, Decimal | None]:
    """Grade one raw value against one ranges item.

    Returns ``(grade, reason, canonical_value)`` where ``canonical_value``
    is the value converted into the item's unit (None when grading was
    impossible). ``reason`` is "" for a clean green/yellow/red grade and
    explains every ``无法判断`` outcome.

    An item with neither ``ref_low`` nor ``ref_high`` has no usable
    interval at all and always grades ``无法判断``; a side with no ``ref``
    bound is unbounded on that side (green), and a side with a ``ref``
    bound but no ``yellow`` edge fails closed to ``无法判断``.
    """
    range_unit = _norm_unit(item.get("unit"))
    ref_low = _threshold(item, "ref_low")
    ref_high = _threshold(item, "ref_high")
    if ref_low is None and ref_high is None:
        return GRADE_UNKNOWN, "无可用参考区间", None
    raw = _parse_number(value_text)
    if raw is None:
        return GRADE_UNKNOWN, "数值无法解析", None
    obs_unit = _norm_unit(unit)
    if obs_unit == "":
        if range_unit != "":
            return GRADE_UNKNOWN, "数值未标明单位，无法与参考区间比对", None
        value = raw
    else:
        if range_unit == "":
            return GRADE_UNKNOWN, "单位无法与参考区间比对", None
        converted = _to_unit(raw, obs_unit, range_unit)
        if converted is None:
            return GRADE_UNKNOWN, "单位无法与参考区间比对", None
        value = converted
    if ref_low is not None and value < ref_low:
        yellow_low = _threshold(item, "yellow_low")
        if yellow_low is None:
            return GRADE_UNKNOWN, "超出参考区间方向缺少分级阈值", value
        if value >= yellow_low:
            return GRADE_YELLOW, "", value
        return GRADE_RED, "", value
    if ref_high is not None and value > ref_high:
        yellow_high = _threshold(item, "yellow_high")
        if yellow_high is None:
            return GRADE_UNKNOWN, "超出参考区间方向缺少分级阈值", value
        if value <= yellow_high:
            return GRADE_YELLOW, "", value
        return GRADE_RED, "", value
    return GRADE_GREEN, "", value


def _action_for(grade: str, value: Decimal | None, item: Mapping[str, Any]) -> str:
    if grade == GRADE_GREEN:
        return ACTION_OBSERVE
    if grade == GRADE_YELLOW:
        return ACTION_RECHECK
    if grade == GRADE_RED and value is not None:
        emergency_low = _threshold(item, "emergency_low")
        emergency_high = _threshold(item, "emergency_high")
        if emergency_low is not None and value <= emergency_low:
            return ACTION_EMERGENCY
        if emergency_high is not None and value >= emergency_high:
            return ACTION_EMERGENCY
        return ACTION_SEEK_CARE
    if grade == GRADE_RED:
        return ACTION_SEEK_CARE
    return ACTION_OBSERVE


def _template_explanation(
    grade: str, name: str, reason: str
) -> str:
    if grade == GRADE_GREEN:
        return f"{name}在参考区间内，单看这一项没有超出区间的信号；仍建议结合完整报告请医生判断。"
    if grade == GRADE_YELLOW:
        return f"{name}超出了参考区间，但还没有到红档范围；建议把这一项记下来，复查时带给医生看，并留意身体变化。"
    if grade == GRADE_RED:
        return f"{name}明显超出了参考区间，建议尽快就诊，并把这份报告带给医生看。"
    return f"{name}目前无法分级（{reason}），只作记录，不下判断；请咨询医生确认这一项的意义。"


def _resolve_item(
    observation: Mapping[str, Any], items: Mapping[str, Any]
) -> tuple[str | None, Mapping[str, Any] | None]:
    """Map an observation to a ranges item.

    Primary key is an explicit ``item_key`` on the observation; fallback
    is an exact ``display_name`` match against the ranges item ``name``,
    used only when no explicit key is present. An explicit key that is
    not in the table resolves to no item (fail-closed to 无法判断) --
    the display name must never override an explicit mismatch.
    """
    key = observation.get("item_key")
    if isinstance(key, str) and key:
        if key not in items:
            return None, None
        item = items[key]
        return key, item if isinstance(item, Mapping) else None
    display = observation.get("display_name")
    if isinstance(display, str) and display:
        for item_key, item in items.items():
            if isinstance(item, Mapping) and item.get("name") == display:
                return item_key, item
    return None, None


def load_ranges(path: Any = None) -> dict[str, Any]:
    """Load ``content/reference_ranges.json``; missing/unusable -> empty.

    A missing file is not an error here: it only means every item grades
    无法判断 until the content worker lands the file. Callers that need a
    hard failure should check ``version`` themselves.
    """
    target = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parent / "content" / "reference_ranges.json"
    )
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": None, "items": {}}
    if not isinstance(raw, Mapping):
        return {"version": None, "items": {}}
    items = raw.get("items")
    if not isinstance(items, Mapping):
        return {"version": raw.get("version"), "items": {}}
    return {"version": raw.get("version"), "items": dict(items)}


def build_interpretation(
    entry_id: Any = "",
    observations: Any = None,
    ranges: Mapping[str, Any] | None = None,
    explanations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a ``report-interpretation/v1`` sheet for one entry.

    ``observations`` are observation dicts (as stored: ``observation_id``,
    ``current_revision_id``, ``verification_status``, ``display_name``,
    ``value_text``, ``unit``, ``context``). Stored observations carry the
    value in ``value_text``; corpus-style observations carry a numeric
    ``value`` instead -- both are accepted, ``value_text`` wins when both
    are present. ``ranges`` is the loaded ranges document
    (``load_ranges()`` output); pass ``None`` to load it from disk.
    ``explanations`` optionally supplies a model-drafted explanation per
    ``item_key``; each draft is linted and replaced by "" on any hit.

    The first argument may also be the observations list itself
    (``build_interpretation(observations)``), the seam the grading corpus
    uses; ``entry_id`` then defaults to "".
    """
    if observations is None and isinstance(entry_id, (list, tuple)):
        observations, entry_id = entry_id, ""
    if ranges is None:
        ranges = load_ranges()
    items = ranges.get("items") if isinstance(ranges, Mapping) else None
    if not isinstance(items, Mapping):
        items = {}
    graded: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for observation in observations or []:
        if not isinstance(observation, Mapping):
            continue
        ref = (
            observation.get("observation_id")
            or observation.get("current_revision_id")
            or ""
        )
        if observation.get("verification_status") not in CONFIRMED_STATUSES:
            excluded.append(
                {
                    "observation_ref": ref,
                    "reason": "未确认观测不进入解读",
                }
            )
            continue
        item_key, item = _resolve_item(observation, items)
        name = observation.get("display_name") or ""
        if item is None:
            grade, reason = GRADE_UNKNOWN, "无对应参考区间"
            canonical: Decimal | None = None
            display_name = name or "未识别项目"
        else:
            raw_value = observation.get("value_text")
            if raw_value is None:
                raw_value = observation.get("value")
            grade, reason, canonical = grade_observation_value(
                raw_value, observation.get("unit"), item
            )
            display_name = item.get("name") or name or item_key or "未识别项目"
        value_text = observation.get("value_text")
        if value_text is None:
            value_text = observation.get("value")
        value_rendered = "" if value_text is None else str(value_text)
        unit_rendered = observation.get("unit") or ""
        result_text = f"{value_rendered}{unit_rendered}"
        draft = (explanations or {}).get(item_key) if item_key else None
        if draft is not None:
            explanation, lint_failed, _reasons = lint_explanation(draft)
        else:
            explanation = _template_explanation(grade, display_name, reason)
            lint_failed = False
        graded.append(
            {
                "observation_ref": ref,
                "item_key": item_key,
                "name": display_name,
                "result_text": result_text,
                "grade": grade,
                "explanation": explanation,
                "action": _action_for(
                    grade, canonical, item if item is not None else {}
                ),
                "lint_failed": lint_failed,
                "reason": reason,
            }
        )
    counts = {
        GRADE_GREEN: 0,
        GRADE_YELLOW: 0,
        GRADE_RED: 0,
        GRADE_UNKNOWN: 0,
    }
    for entry in graded:
        counts[entry["grade"]] += 1
    if counts[GRADE_RED]:
        overall = "其中有红档项目，建议尽快就诊，并把这份报告带给医生看。"
    elif counts[GRADE_YELLOW]:
        overall = "其中有黄档项目，建议复查时请医生结合其他情况看。"
    elif graded and counts[GRADE_UNKNOWN] == 0:
        overall = "已确认项目均在参考区间内，仍建议请医生结合完整报告判断。"
    elif graded:
        overall = "部分项目无法分级，仍建议请医生结合完整报告判断。"
    else:
        overall = "没有可解读的已确认项目。"
    summary = {
        "total_confirmed": len(graded),
        "green": counts[GRADE_GREEN],
        "yellow": counts[GRADE_YELLOW],
        "red": counts[GRADE_RED],
        "unknown": counts[GRADE_UNKNOWN],
        "excluded": len(excluded),
        "text": overall,
    }
    return {
        "contract": CONTRACT_VERSION,
        "entry_id": entry_id,
        "items": graded,
        "excluded": excluded,
        "summary": summary,
        "disclaimer": DISCLAIMER,
        "ranges_version": ranges.get("version") if isinstance(ranges, Mapping) else None,
    }


def interpretation_summary(interpretation: Mapping[str, Any]) -> str:
    """Plain-text summary of an interpretation sheet, for attach use.

    Contains only confirmed observations (name + verbatim value + grade)
    and the distribution counts. Excluded observations, explanations and
    any new judgement are deliberately left out.
    """
    lines = []
    for item in interpretation.get("items", []):
        if not isinstance(item, Mapping):
            continue
        lines.append(f"{item.get('name', '')}：{item.get('result_text', '')}（{item.get('grade', '')}）")
    summary = interpretation.get("summary", {})
    if isinstance(summary, Mapping):
        lines.append(
            "已确认项目共 {total} 项：绿 {green} 项、黄 {yellow} 项、红 {red} 项、无法判断 {unknown} 项。".format(
                total=summary.get("total_confirmed", len(lines)),
                green=summary.get("green", 0),
                yellow=summary.get("yellow", 0),
                red=summary.get("red", 0),
                unknown=summary.get("unknown", 0),
            )
        )
    return "\n".join(lines)
