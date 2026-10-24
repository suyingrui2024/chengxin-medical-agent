"""Local medication reminder plans (design §6.3, P4).

Pure functions only: given the patient's long-term medications from the
archive baseline (P2a) plus medications mentioned in this consultation,
build a *local reminder* plan. The system only reminds the patient
("该吃 X 了") at times the patient has set. It never suggests a dose,
never judges interactions, and never invents a schedule: a medication
with no patient-set time is returned with an empty time list and a
"pending_setup" status, never a fabricated time.

No dosage units, no frequency advice, and no interaction language appear
anywhere in the output. Medication names are echoed verbatim from the
patient's own records; the module does not parse doses out of names and
does not add any.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

# Bare-string consultation answers that mean "took nothing": they must not
# become a reminder for a medication literally called "没有".
_DENIAL_TEXTS = {"没有", "无", "没吃", "没吃药", "未服用", "不吃", "none", "no"}

# Prefixes stripped from a bare-string consultation answer ("吃了布洛芬"
# -> "布洛芬"). Only a single name is ever recovered this way; callers
# with several medications must pass a structured list.
_PREFIXES = ("吃了", "服用了", "服用", "在吃", "吃")


def _clean_name(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _names_from_baseline(entries: Iterable[Any]) -> List[Dict[str, str]]:
    """Normalize baseline medication entries to [{name, purpose}]."""
    out: List[Dict[str, str]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = _clean_name(entry.get("name"))
            purpose = _clean_name(entry.get("purpose"))
        else:
            name = _clean_name(entry)
            purpose = ""
        if name:
            out.append({"name": name, "purpose": purpose})
    return out


def _names_from_consultation(entries: Any) -> List[str]:
    """Normalize consultation medications to a list of names.

    Accepts a list/tuple of names or {name, ...} dicts, or a single bare
    string (best-effort single name; denial texts yield nothing).
    Free-text multi-drug sentences are NOT split: callers must pass a
    structured list for those.
    """
    if isinstance(entries, str):
        text = entries.strip()
        if not text or text in _DENIAL_TEXTS:
            return []
        for prefix in _PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix):
                text = text[len(prefix):].strip()
                break
        return [text] if text and text not in _DENIAL_TEXTS else []
    out: List[str] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = _clean_name(entry.get("name"))
        else:
            name = _clean_name(entry)
        if name and name not in _DENIAL_TEXTS:
            out.append(name)
    return out


def _valid_times(times: Any) -> List[str]:
    """Keep only HH:MM clock times, deduplicated, in given order."""
    out: List[str] = []
    if not times:
        return out
    if isinstance(times, str):
        times = [times]
    for item in times:
        if isinstance(item, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", item.strip()):
            cleaned = item.strip()
            if cleaned not in out:
                out.append(cleaned)
    return out


def build_medication_reminders(
    baseline_medications: Iterable[Any],
    consultation_medications: Any = None,
    scheduled_times: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build the local reminder plan.

    Args:
        baseline_medications: P2a medications list ({name, purpose} dicts
            or plain names).
        consultation_medications: medications mentioned this consultation
            (list of names/dicts, or a bare string).
        scheduled_times: optional patient-set times, {name: ["08:00", ...]}.
            Absent for a medication means "pending_setup", never a guess.

    Returns a list of {medication_name, purpose, source, scheduled_times,
    status, note}, deduplicated by normalized name (baseline first, then
    consultation additions). ``note`` only ever says the system will
    remind the patient it is time for that medication.
    """
    scheduled_times = scheduled_times or {}
    merged: List[Dict[str, Any]] = []
    seen = set()

    def _key(name: str) -> str:
        return name.strip().casefold()

    for entry in _names_from_baseline(baseline_medications):
        if _key(entry["name"]) in seen:
            continue
        seen.add(_key(entry["name"]))
        merged.append({**entry, "source": "baseline"})
    for name in _names_from_consultation(consultation_medications):
        if _key(name) in seen:
            continue
        seen.add(_key(name))
        merged.append({"name": name, "purpose": "", "source": "consultation"})

    plan: List[Dict[str, Any]] = []
    for item in merged:
        times = _valid_times(scheduled_times.get(item["name"]))
        if times:
            status = "scheduled"
            note = f"到设定时间提醒你：该吃{item['name']}了"
        else:
            status = "pending_setup"
            note = f"提醒时间待设定，设定后会按时提醒你该吃{item['name']}了"
        plan.append(
            {
                "medication_name": item["name"],
                "purpose": item["purpose"],
                "source": item["source"],
                "scheduled_times": times,
                "status": status,
                "note": note,
            }
        )
    return plan
