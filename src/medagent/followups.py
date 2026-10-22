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

from datetime import datetime, timedelta, timezone
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
        hours = 24
        kind = "urgent_checkin"
        reason = "风险升级关闭，建议 24 小时内确认是否已获线下评估：" + "、".join(
            state.risk.triggers
        )
    elif state.closed_reason == "confirmed_by_user":
        kind = "routine_checkin"
        has_unknowns = any(
            fact.assertion.value == "unknown" for fact in state.facts.values()
        )
        hours = 48 if has_unknowns else 72
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
