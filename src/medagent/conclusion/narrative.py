from __future__ import annotations
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from importlib import resources
from typing import Any, Dict, List, Optional
from ..baseline_projection import build_allergy_hint
from ..direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from ..domain import Assertion, CaseState, Phase, RiskLevel
from ..followups import build_follow_up_plan
from ..handoff import LABELS as FACT_LABELS
from ..reasoning import RuleBasedProposer, validate_workspace
from ..text_match import contains_unnegated, find_unnegated_certain
from ..worry import extract_worry_object, is_worry_sentence

from .constants import BANNED_PHRASES, CERTAINTY_TEMPLATES, _VISIT_GOAL_KEYWORDS, _WORRY_FALLBACK_OBJECT

def _narrative_templates() -> Dict[str, Any]:
    """Curated narrative templates (content/conclusion_narrative.json).
    Like the self-care groups, the asset counts as absent unless it
    carries review metadata; absent/corrupt -> {} and the sheet simply
    carries narrative=None (fail-open)."""
    data = _load_json_optional("conclusion_narrative.json")
    return data if data.get("reviewed_by") else {}


def _narrative_reasoning(
    templates: Dict[str, Any],
    directions: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
) -> Optional[str]:
    """The “why” paragraph: certainty sentences for the selected
    directions plus the still-missing information. Only curated text —
    patient labels and gap labels, never patient quotes. None when
    there is nothing to explain (directions abstained/empty and no
    gaps), per the narrative contract."""
    sentences: List[str] = []
    if directions:
        rendered = [
            CERTAINTY_TEMPLATES[d["certainty"]].format(label=d["patient_label"])
            for d in directions
            if d.get("patient_label") and d.get("certainty") in CERTAINTY_TEMPLATES
        ]
        if rendered:
            intro = templates.get("directions_intro") or ""
            separator = templates.get("separator") or "；"
            sentences.append(intro + separator.join(rendered))
    if gaps:
        # Batch B three-state rendering: asked-unknown and declined gaps
        # are rendered in separate clauses with their own wording.
        # "还没有确认" must never appear here again — it conflated
        # asked-and-unclear with never-asked. Gaps without a state
        # (pre-batch-B callers) read as asked_unknown.
        separator = templates.get("gaps_separator") or "、"
        unknown_labels = [
            str(gap.get("label") or gap.get("fact_key"))
            for gap in gaps
            if gap.get("state") != "declined"
        ]
        declined_labels = [
            str(gap.get("label") or gap.get("fact_key"))
            for gap in gaps
            if gap.get("state") == "declined"
        ]
        parts: List[str] = []
        if unknown_labels:
            parts.append(
                (
                    templates.get("gaps_unknown_template")
                    or "你提到{labels}还不清楚，这也会影响判断"
                ).replace("{labels}", separator.join(unknown_labels))
            )
        if declined_labels:
            parts.append(
                (
                    templates.get("gaps_declined_template")
                    or "{labels}你表示暂不提供，这也会影响判断"
                ).replace("{labels}", separator.join(declined_labels))
            )
        gap_text = "；".join(parts)
        if gap_text:
            if sentences:
                sentences.append(
                    (templates.get("gaps_connector") or "") + gap_text
                )
            else:
                sentences.append(
                    (templates.get("gaps_only_intro") or "") + gap_text
                )
    if not sentences:
        return None
    return "。".join(sentences) + "。"


def _build_narrative(
    templates: Dict[str, Any],
    *,
    tier: str,
    level: str,
    group_key: str,
    directions: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
    state: Optional[CaseState] = None,
) -> Optional[Dict[str, Any]]:
    """Three-part narrative (benchmark design §3.1): framing （定性）,
    reasoning （原理）, severity （大白话程度）. Framing and severity are
    always present on a sheet when the asset is available; reasoning
    may be None. Deterministic template assembly only.

    P4 (S-11): the severity line passes through the consistency
    fallback when a state is supplied — a recorded low score never
    keeps the urgent template's "程度偏高" claim; it falls back to
    restating the recorded score. The template itself is untouched,
    and no disposition is recomputed."""
    if not templates:
        return None
    framing_map = templates.get("framing") or {}
    if tier == "urgent" and framing_map.get("urgent"):
        framing_template = framing_map["urgent"]
    else:
        framing_template = framing_map.get(level)
    severity_template = (templates.get("severity") or {}).get(level)
    if not framing_template or not severity_template:
        return None
    group_label = (templates.get("group_labels") or {}).get(group_key, "身体")
    severity_text = severity_template.replace("{group}", group_label)
    if state is not None:
        severity_text = severity_consistent_text(severity_text, state)
    return {
        "framing": framing_template.replace("{group}", group_label),
        "reasoning": _narrative_reasoning(
            templates.get("reasoning") or {}, directions, gaps
        ),
        "severity": severity_text,
    }


def _contains_banned(text: str) -> bool:
    return bool(text) and any(phrase in text for phrase in BANNED_PHRASES)


def _worry_tail(chief: str, severity: Any, gap_labels: List[str]) -> str:
    tail = f"已确认的是{chief}；" if chief else ""
    if severity is not None:
        tail += f"影响程度为 {severity} 分；"
    if gap_labels:
        tail += f"{'、'.join(gap_labels)}还不清楚；"
    return tail


def _build_worry(
    state: CaseState, gaps: List[Dict[str, Any]], goal_value: Optional[str]
) -> tuple[Optional[Dict[str, str]], bool]:
    """Batch B worry slot. Source priority: state.facts["patient_worry"]
    (PRESENT, value is the object) > a worry sentence recorded as the
    patient goal (worry.is_worry_sentence / extract_worry_object). The
    second source consumes the goal: it returns ``goal_consumed=True``
    so the caller sets goal_line to None — a worry must never overwrite
    a patient goal (K-03), and when the goal slot itself holds the
    worry, the goal slot is emptied rather than duplicated.

    Returns (worry_dict_or_None, goal_consumed). The response template
    uses only the chief complaint, the severity score when collected,
    and the gap labels — never a direction or department."""
    worry_fact = state.facts.get("patient_worry")
    obj: Optional[str] = None
    goal_consumed = False
    if (
        worry_fact is not None
        and worry_fact.assertion == Assertion.PRESENT
        and str(worry_fact.value or "").strip()
    ):
        obj = str(worry_fact.value).strip()
    elif goal_value and is_worry_sentence(goal_value):
        obj = extract_worry_object(goal_value)
        if obj:
            goal_consumed = True
    if not obj:
        return None, False
    chief_fact = state.facts.get("chief_complaint")
    chief = (
        str(chief_fact.value).strip()
        if chief_fact is not None
        and chief_fact.assertion == Assertion.PRESENT
        and chief_fact.value is not None
        else ""
    )
    severity_fact = state.facts.get("severity")
    severity = (
        severity_fact.value
        if severity_fact is not None
        and severity_fact.assertion == Assertion.PRESENT
        else None
    )
    gap_labels = [
        str(gap.get("label") or gap.get("fact_key")) for gap in (gaps or [])
    ]
    close = "建议按结论单的处置去做，出现下方警示信号请及时就医。"
    response = (
        f"你问过是不是‘{obj}’：目前还不能仅凭这次问诊确定这一点。"
        f"{_worry_tail(chief, severity, gap_labels)}{close}"
    )
    if _contains_banned(obj) or _contains_banned(response):
        # Degrade: the object becomes the generic fallback and the
        # response drops the quoted object, keeping the other sentences.
        # A banned phrase inside the chief complaint itself (patient
        # wording) is replaced the same way so the sheet lint below
        # still passes; gap labels are curated and severity is a score.
        safe_chief = (
            "你描述的主要问题" if _contains_banned(chief) else chief
        )
        response = (
            f"{_WORRY_FALLBACK_OBJECT}，目前还不能仅凭这次问诊确定这一点。"
            f"{_worry_tail(safe_chief, severity, gap_labels)}{close}"
        )
        obj = _WORRY_FALLBACK_OBJECT
    return {"object": obj, "response": response}, goal_consumed


def _build_goal_response(goal_line: Optional[str]) -> Optional[str]:
    """Batch B goal slot. Only for a non-empty goal_line that is not a
    visit-oriented goal: goals mentioning 检查 / 去医院 / 就诊 /
    看医生 return None — batch A's _speak_close_line branch owns those
    and this slot must not duplicate it. Over 60 chars, the quoted goal
    is truncated. A banned phrase degrades the response to an
    object-free form (the goal itself is still quoted nowhere)."""
    if not goal_line:
        return None
    # P2 slot guard (O-15 / U-17): a worry sentence sitting in the
    # goal slot must never render a second, near-identical answer
    # next to the worry slot's own response. is_worry_sentence is
    # reused from worry.py; no local judgement is invented here.
    # (The goal slot itself is still emptied by _build_worry only
    # when no separate patient_worry fact exists; when both facts
    # exist this guard is what keeps the close to one rendering.)
    if is_worry_sentence(goal_line):
        return None
    if any(keyword in goal_line for keyword in _VISIT_GOAL_KEYWORDS):
        return None
    display = goal_line if len(goal_line) <= 60 else goal_line[:60] + "…"
    response = (
        f"你问到‘{display}’：这次问诊的信息还不足以直接回答这一点，"
        "需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。"
    )
    if _contains_banned(goal_line) or _contains_banned(response):
        response = (
            "你问到的这一点，这次问诊的信息还不足以直接回答这一点，"
            "需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。"
        )
    return response

from .sheet import _load_json_optional
