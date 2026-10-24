"""Longitudinal follow-up loop (design doc §6).

When a consultation concludes, a follow-up plan is scheduled from the
final risk picture (emergency routes get none: the patient was directed
to immediate care). Outcomes recorded later close the loop; "worsened"
and "hospitalized" spawn a linked re-triage session instead of letting
the thread die. Everything is stored on the case and in the event log,
so longitudinal summaries and the review flywheel can consume it.

This module is deliberately transport-free: *when* to contact the
patient (push, message, scheduler) is a deployment concern; here we own
the plan, the outcome vocabulary, and the re-triage linkage.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any, Dict, Optional

from .domain import CaseState, RiskLevel

OUTCOMES = (
    "recovered",      # 已痊愈
    "improving",      # 好转中
    "unchanged",      # 无变化
    "worsened",       # 加重 -> re-triage
    "sought_care",    # 已就医
    "hospitalized",   # 已住院 -> re-triage linkage kept for the record
)

RETRIAGE_OUTCOMES = ("worsened", "hospitalized")

# Design §6.2 (P4): routine follow-up windows by pathway group, read from
# content/followup_windows.json (engineering-v1). The table below is the
# fail-open fallback when the file is missing or unreadable, so plan
# building never crashes on a content problem; it mirrors the file.
_DEFAULT_ROUTINE_HOURS = {
    "respiratory": 72,
    "chest": 48,
    "abdominal": 48,
    "headache": 72,
    "dizziness": 72,
    "back": 120,
    "urinary": 72,
    "skin": 168,
    "generic": 72,
}
_DEFAULT_UNKNOWN_HOURS_CAP = 48

# HARD RULE (design §6.2): urgent follow-up is always 24h. It is
# deliberately NOT table-driven: no content edit may lengthen it.
URGENT_HOURS = 24

# Detail fact -> pathway group. A recorded detail fact (any assertion:
# being asked and answered "不知道" still counts as a probe, same
# semantics as the pathway-exploration rule) identifies the pathway.
_DETAIL_FACT_TO_GROUP = {
    "resp_breathing": "respiratory",
    "resp_cough_details": "respiratory",
    "resp_intake": "respiratory",
    "gi_details": "abdominal",
    "head_details": "headache",
    "dizzy_details": "dizziness",
    "back_details": "back",
    "urinary_details": "urinary",
    "skin_details": "skin",
}

# differentials.json pathway id -> follow-up table group. Only "low_back"
# differs from the table key ("back").
_PATHWAY_ID_TO_GROUP = {"low_back": "back"}


def _load_routine_windows() -> Dict[str, int]:
    """Read the routine window table; fall back to defaults on any error."""
    try:
        raw = (
            resources.files("medagent.content")
            .joinpath("followup_windows.json")
            .read_text(encoding="utf-8")
        )
        data = json.loads(raw)
        hours = {
            str(group): int(value)
            for group, value in (data.get("routine_hours") or {}).items()
        }
        hours = {
            group: value
            for group, value in hours.items()
            if group in _DEFAULT_ROUTINE_HOURS and value > 0
        }
        if hours:
            return {**_DEFAULT_ROUTINE_HOURS, **hours}
    except Exception:
        pass
    return dict(_DEFAULT_ROUTINE_HOURS)


def _unknown_hours_cap() -> int:
    try:
        raw = (
            resources.files("medagent.content")
            .joinpath("followup_windows.json")
            .read_text(encoding="utf-8")
        )
        cap = int(json.loads(raw).get("unknown_hours_cap", 0))
        return cap if cap > 0 else _DEFAULT_UNKNOWN_HOURS_CAP
    except Exception:
        return _DEFAULT_UNKNOWN_HOURS_CAP


def routine_hours_for(group: str) -> int:
    """Routine window for a pathway group; unknown groups fall to generic."""
    return _load_routine_windows().get(group, _DEFAULT_ROUTINE_HOURS["generic"])


def _derive_pathway_group(state: CaseState) -> str:
    """Best-effort pathway group for the concluded case.

    1. First recorded detail fact wins (recording order = exploration
       order; the primary pathway is probed first).
    2. Otherwise, chief-complaint trigger terms from differentials.json
       (this is the only signal for pathways with no dedicated detail
       field, e.g. chest). Substring matching only: the chief complaint
       is the patient's presenting complaint, and a negated term here
       can only shift a routine check-in window, never a disposition.
    3. Otherwise generic. Never raises.
    """
    for fact in state.facts.values():
        group = _DETAIL_FACT_TO_GROUP.get(fact.key)
        if group:
            return group
    try:
        chief_fact = state.facts.get("chief_complaint")
        chief = str(chief_fact.value) if chief_fact and chief_fact.value else ""
        if chief:
            raw = (
                resources.files("medagent.content")
                .joinpath("differentials.json")
                .read_text(encoding="utf-8")
            )
            for pathway in json.loads(raw).get("pathways", []):
                terms = pathway.get("chief_complaint_contains_any") or []
                if any(term and term in chief for term in terms):
                    return _PATHWAY_ID_TO_GROUP.get(
                        pathway.get("id"), pathway.get("id")
                    )
    except Exception:
        pass
    return "generic"


def _due_at(scheduled_at: str, hours: int) -> str:
    start = datetime.fromisoformat(scheduled_at)
    return (start + timedelta(hours=hours)).astimezone(timezone.utc).isoformat()


def build_follow_up_plan(state: CaseState) -> Optional[Dict[str, Any]]:
    """Pure plan derivation from the concluded case. None = no follow-up."""
    if state.risk.level == RiskLevel.EMERGENCY:
        return None
    if state.closed_reason == "emergency_route":
        return None
    scheduled_at = state.updated_at
    if state.closed_reason == "urgent_route" or state.risk.level == RiskLevel.URGENT:
        # HARD RULE: urgent is always 24h (see URGENT_HOURS above).
        hours = URGENT_HOURS
        kind = "urgent_checkin"
        reason = "风险升级关闭，建议 24 小时内确认是否已获线下评估"
        if state.risk.triggers:
            reason += "：" + "、".join(state.risk.triggers)
    elif state.closed_reason == "confirmed_by_user":
        kind = "routine_checkin"
        # Design §6.2: routine window comes from the per-group table.
        # The old unknown-based 48/72 split is superseded, except for one
        # preserved safety invariant: while any fact is still unknown,
        # the window never stretches past the unknown cap (48h), so an
        # uncertain case is never pushed to a 5–7 day window.
        hours = routine_hours_for(_derive_pathway_group(state))
        has_unknowns = any(
            fact.assertion.value == "unknown" for fact in state.facts.values()
        )
        if has_unknowns:
            hours = min(hours, _unknown_hours_cap())
        reason = "摘要已确认，例行回访确认恢复情况"
    else:
        return None
    return {
        "kind": kind,
        "status": "scheduled",
        "scheduled_at": scheduled_at,
        "due_at": _due_at(scheduled_at, hours),
        "due_in_hours": hours,
        "reason": reason,
        "outcome": None,
        "outcome_note": "",
        "outcome_recorded_at": None,
        "retriage_session_id": None,
    }


def apply_outcome(plan: Dict[str, Any], outcome: str, note: str = "") -> Dict[str, Any]:
    """Validate and stamp an outcome onto a plan (mutates and returns it)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"未知随访结局：{outcome}")
    if plan.get("status") == "completed":
        raise ValueError("该随访已记录结局，不能重复记录")
    plan["status"] = "completed"
    plan["outcome"] = outcome
    plan["outcome_note"] = note
    plan["outcome_recorded_at"] = datetime.now(timezone.utc).isoformat()
    return plan
