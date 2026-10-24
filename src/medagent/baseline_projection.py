"""Baseline profile projection for consultation context (P2c, design §4.3).

Pure functions only — no I/O, no state, no engine wiring. The wiring
worker consumes these at the context-assembly points named in
``~/workspace/scratch/p2c-wiring-note.md``.

Discipline (mirrors the prior_case projection discipline):

* Whitelist only. Anything not listed in :func:`build_baseline_projection`
  is dropped — in particular height/weight and every other full-profile
  field never leaves this module.
* Length caps: at most 10 items per list, at most 80 characters per
  string. Over-long strings are truncated and the truncation is flagged
  (top-level ``truncated`` flag, plus a per-item ``truncated`` flag on
  dict items), never silently cut.
* The projection is context only: it must NEVER be written into
  ``CaseState.facts``. Archive values are reference material, not
  this-turn evidence, so they must not pollute the validation chain.
* Measurement summaries are descriptive statistics only (min/max/mean/
  latest + time). No judgement wording is produced here or anywhere
  downstream of this projection.

Session switch convention (defined here, wired elsewhere — see the
wiring note):

* Session field ``baseline_reference_enabled`` defaults to ``True``
  (decision D5: auto-injection on by default, visible, closable).
* When the patient turns it off for a session, the wiring layer records
  an event of type ``baseline.reference_disabled`` (payload: session
  id only — never the projection content).
* When the switch is off, the wiring layer must not call
  :func:`build_baseline_projection` at all for that session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

MAX_LIST_ITEMS = 10
MAX_STRING_CHARS = 80
MEASUREMENT_WINDOW_DAYS = 7

#: Measurement verification statuses that count as "confirmed" by the
#: patient (matches the observation verification_status vocabulary in
#: patient_records.py).
CONFIRMED_STATUSES = frozenset({"patient_confirmed", "patient_corrected"})

#: Session field carrying the D5 on/off switch (wiring layer only).
BASELINE_REFERENCE_ENABLED_FIELD = "baseline_reference_enabled"

#: Event type recorded when the patient closes the reference (wiring
#: layer only; payload carries no projection content).
BASELINE_REFERENCE_DISABLED_EVENT = "baseline.reference_disabled"

#: Words that mark an avoid-text item as an allergy-related medication
#: prompt. Both an allergy word AND a medication word must be present
#: for :func:`build_allergy_hint` to fire, so a generic "avoid cold
#: medicine" line or a bare "allergy history" line alone does not
#: trigger the hint.
_ALLERGY_WORDS = ("过敏", "allergy", "allergies", "allergic")
_MEDICATION_WORDS = ("药", "用药", "服药", "药物", "药品", "medicine", "medication")


def baseline_reference_enabled(session_fields: Optional[Dict[str, Any]]) -> bool:
    """Return whether baseline reference is on for a session.

    Default ``True`` (D5). Only an explicit ``False`` disables it; a
    missing field, ``None``, or any other value keeps it on. This is a
    convention helper for the wiring layer, not wiring itself.
    """
    if not session_fields:
        return True
    return session_fields.get(BASELINE_REFERENCE_ENABLED_FIELD, True) is not False


def _truncate(text: str) -> tuple[str, bool]:
    """Truncate to MAX_STRING_CHARS, flagging the cut (never silent)."""
    if len(text) <= MAX_STRING_CHARS:
        return text, False
    return text[:MAX_STRING_CHARS] + "…", True


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(values: Any) -> tuple[List[str], bool]:
    """Normalize a str-or-list field to a capped list of strings."""
    truncated = False
    if values is None:
        return [], False
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return [], False
    out: List[str] = []
    for item in values[:MAX_LIST_ITEMS]:
        text = _clean_str(item)
        if text is None:
            continue
        text, cut = _truncate(text)
        truncated = truncated or cut
        out.append(text)
    return out, truncated


def _named_list(values: Any, extra_key: str) -> tuple[List[Dict[str, Any]], bool]:
    """Normalize allergies/medications: keep only name + one extra key.

    ``extra_key`` is ``kind`` for allergies, ``purpose`` for
    medications. A bare string item becomes ``{"name": <str>}``.
    """
    truncated = False
    if values is None:
        return [], False
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return [], False
    out: List[Dict[str, Any]] = []
    for item in values[:MAX_LIST_ITEMS]:
        entry: Dict[str, Any] = {}
        item_truncated = False
        if isinstance(item, str):
            name = _clean_str(item)
            if name is None:
                continue
            name, item_truncated = _truncate(name)
            entry["name"] = name
        elif isinstance(item, dict):
            name = _clean_str(item.get("name"))
            if name is None:
                continue
            name, cut = _truncate(name)
            item_truncated = item_truncated or cut
            entry["name"] = name
            extra = _clean_str(item.get(extra_key))
            if extra is not None:
                extra, cut = _truncate(extra)
                item_truncated = item_truncated or cut
                entry[extra_key] = extra
        else:
            continue
        if item_truncated:
            entry["truncated"] = True
        truncated = truncated or item_truncated
        out.append(entry)
    return out, truncated


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _summarize_measurements(
    measurements: Any, now: datetime
) -> Dict[str, Dict[str, Any]]:
    """Group confirmed, in-window measurements by kind into stats.

    Only ``patient_confirmed``/``patient_corrected`` rows whose
    ``measured_at`` falls within the last 7 days are accepted. Each kind
    yields min/max/mean/latest (+ time, unit, count) — descriptive
    statistics only, no judgement wording.
    """
    if not isinstance(measurements, (list, tuple)):
        return {}
    cutoff = now - timedelta(days=MEASUREMENT_WINDOW_DAYS)
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for row in measurements:
        if not isinstance(row, dict):
            continue
        if row.get("verification_status") not in CONFIRMED_STATUSES:
            continue
        kind = _clean_str(row.get("kind"))
        if kind is None:
            continue
        measured_at = _parse_time(row.get("measured_at"))
        if measured_at is None or measured_at < cutoff or measured_at > now:
            continue
        try:
            value = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        by_kind.setdefault(kind, []).append(
            {"value": value, "at": measured_at, "unit": _clean_str(row.get("unit"))}
        )
    summary: Dict[str, Dict[str, Any]] = {}
    for kind, rows in by_kind.items():
        values = [r["value"] for r in rows]
        latest = max(rows, key=lambda r: r["at"])
        entry: Dict[str, Any] = {
            "count": len(rows),
            "min": min(values),
            "max": max(values),
            "mean": round(sum(values) / len(values), 4),
            "latest": latest["value"],
            "latest_at": latest["at"].isoformat(),
        }
        if latest["unit"] is not None:
            entry["unit"] = latest["unit"]
        summary[kind] = entry
    return summary


def build_baseline_projection(
    profile: Optional[Dict[str, Any]],
    recent_measurements: Optional[List[Dict[str, Any]]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the whitelisted baseline projection for consultation context.

    ``profile`` is the P2a baseline profile dict (whatever its storage
    shape, only whitelisted keys are read). ``recent_measurements`` is
    a list of measurement dicts with ``kind``/``value``/``unit``/
    ``measured_at``/``verification_status``. Returns ``{}`` for an empty
    archive instead of raising.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    projection: Dict[str, Any] = {}
    truncated_any = False

    if isinstance(profile, dict):
        name = _clean_str(profile.get("display_name"))
        if name is not None:
            name, cut = _truncate(name)
            truncated_any = truncated_any or cut
            projection["display_name"] = name

        birth_year = profile.get("birth_year")
        if isinstance(birth_year, bool):
            pass
        elif isinstance(birth_year, int):
            projection["birth_year"] = birth_year
        elif isinstance(birth_year, str) and birth_year.strip().isdigit():
            projection["birth_year"] = int(birth_year.strip())

        birth_month = _clean_str(profile.get("birth_year_month"))
        if birth_month is not None:
            birth_month, cut = _truncate(birth_month)
            truncated_any = truncated_any or cut
            projection["birth_year_month"] = birth_month

        sex = _clean_str(profile.get("sex"))
        if sex is not None:
            sex, cut = _truncate(sex)
            truncated_any = truncated_any or cut
            projection["sex"] = sex

        chronic, cut = _string_list(profile.get("chronic_conditions"))
        truncated_any = truncated_any or cut
        if chronic:
            projection["chronic_conditions"] = chronic

        allergies, cut = _named_list(profile.get("allergies"), "kind")
        truncated_any = truncated_any or cut
        if allergies:
            projection["allergies"] = allergies

        medications, cut = _named_list(profile.get("medications"), "purpose")
        truncated_any = truncated_any or cut
        if medications:
            projection["medications"] = medications

        surgical, cut = _string_list(profile.get("surgical_history"))
        truncated_any = truncated_any or cut
        if surgical:
            projection["surgical_history"] = surgical

    measurements = _summarize_measurements(recent_measurements or [], now)
    if measurements:
        projection["recent_measurements"] = measurements

    if not projection:
        return {}
    projection["truncated"] = truncated_any
    return projection


def build_allergy_hint(
    allergies: Optional[List[Dict[str, Any]]],
    self_care_avoid_text: Optional[Any],
) -> Optional[str]:
    """Template allergy hint for the conclusion sheet's self-care area.

    Returns ``"你档案里记有 X 过敏，涉及用药请先核对"`` only when the
    avoid text is an allergy-related medication prompt (it must mention
    both allergy and medication). Otherwise ``None``. Never names a
    specific drug and never makes a medication judgement — X is only
    the allergy name recorded in the profile.

    DORMANT under self_care 1.2: no real avoid line mentions allergy,
    so this fires only on mock/future content today; see the
    real-content count test in test_p2_integration.py.
    """
    if not allergies or not isinstance(allergies, (list, tuple)):
        return None
    if isinstance(self_care_avoid_text, (list, tuple)):
        text = "；".join(str(t) for t in self_care_avoid_text)
    elif self_care_avoid_text is None:
        return None
    else:
        text = str(self_care_avoid_text)
    lowered = text.lower()
    if not any(word in lowered for word in _ALLERGY_WORDS):
        return None
    if not any(word in lowered for word in _MEDICATION_WORDS):
        return None
    for allergy in allergies:
        if not isinstance(allergy, dict):
            continue
        name = _clean_str(allergy.get("name"))
        if name is not None:
            name, _ = _truncate(name)
            return f"你档案里记有{name}过敏，涉及用药请先核对"
    return None
