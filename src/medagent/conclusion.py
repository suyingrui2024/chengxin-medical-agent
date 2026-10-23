"""Consultation conclusion sheet composer.

Implements docs/问诊结论单设计文档.md (v1.1): at close time, derive the
patient-facing conclusion sheet — disposition, differential directions,
information gaps, self-care, safety net, outlook — deterministically from
the concluded case. No model calls; every clinical sentence traces to the
triage outcome, a validated reasoning workspace, or reviewed content.

Invariants (design §4): verbatim-quote evidence only (I1), rule-out
directions are never truncated away (I2), composition failure must never
block closing (I3, enforced by the caller), regeneration on re-close
increments the sheet revision (I4).
"""
from __future__ import annotations

import json
import os
from importlib import resources
from typing import Any, Dict, List, Optional

from .domain import Assertion, CaseState, Phase, RiskLevel
from .followups import build_follow_up_plan
from .reasoning import RuleBasedProposer, validate_workspace

# Same list as consultation._summary_payload's return_now_if; a drift-guard
# test asserts the two never diverge (the summary remains the source the
# composer prefers when a summary payload is at hand).
RETURN_NOW_IF = [
    "意识异常或抽搐",
    "严重呼吸困难或嘴唇发紫",
    "持续压榨样胸痛",
    "大量或无法控制的出血",
]

# Design §6.3: hitting any of these in a rendered clinical sentence fails
# composition (the caller then falls back to the plain summary view).
BANNED_PHRASES = (
    "确诊",
    "可以排除",
    "没事",
    "不用担心",
    "保证",
    "肯定是",
    "一定是",
    "必须吃药",
)

CERTAINTY_TEMPLATES = {
    "more_likely": "更像是 {label}",
    "possible": "可能是 {label}",
    "cannot_exclude": "目前还不能排除 {label}，这也是建议就诊的原因",
}

ABSTAIN_TEXT = "目前的信息还不足以给出可能的方向——这也是建议你留意变化、必要时线下评估的原因之一"

# Hypothesis id -> self-care pathway group (mirrors differentials.json
# pathway membership; the evaluation suite exercises every id).
PATHWAY_GROUPS = {
    "uri": "respiratory",
    "lower_respiratory": "respiratory",
    "asthma_exacerbation": "respiratory",
    "influenza_like": "respiratory",
    "acs_possible": "chest",
    "non_cardiac_chest": "chest",
    "appendicitis": "abdominal",
    "gastroenteritis": "abdominal",
    "gi_bleed": "abdominal",
    "functional_dyspepsia": "abdominal",
    "meningitis_possible": "headache",
    "thunderclap_headache": "headache",
    "migraine_tension": "headache",
    "bppv": "dizziness",
    "posterior_stroke": "dizziness",
    "orthostatic": "dizziness",
    "mechanical_back": "back",
    "radiculopathy": "back",
    "cauda_serious": "back",
    "cystitis": "urinary",
    "pyelonephritis": "urinary",
    "urinary_stone": "urinary",
    "urticaria": "skin",
    "dermatitis": "skin",
    "skin_infection": "skin",
}

PRIOR_RANK = {"high": 3, "medium": 2, "low": 1}

GAP_IMPACTS = {
    "temperature_c": "体温读数会影响对感染程度的判断",
    "severity": "难受程度会影响对紧急程度的判断",
    "trajectory": "变化趋势会影响对病情走向的判断",
    "resp_breathing": "呼吸情况会影响对严重程度的判断",
    "gi_details": "腹痛的部位和性质会影响对腹部急症的判断",
    "head_details": "头痛的发作方式和伴随表现会影响对颅内急症的判断",
    "dizzy_details": "头晕的类型和伴随表现会影响对脑部供血问题的判断",
    "back_details": "腰痛是否影响腿部和大小便会影响对腰椎严重问题的判断",
    "urinary_details": "排尿表现和尿色会影响对尿路感染部位的判断",
    "skin_details": "皮疹的形态和诱因会影响对过敏与感染的判断",
}
DEFAULT_GAP_IMPACT = "这项信息会影响判断"

# Fact keys whose unknown state makes a gap "significant" for the
# disposition mapping (design §5.2), beyond discriminating-question hits.
MEASUREMENT_FACTS = {"temperature_c"}

_cache: Dict[str, Any] = {}


def sheet_mode() -> str:
    """off | shadow | on — env override, default on (design §8.3/§9A)."""
    mode = os.environ.get("MEDAGENT_CONCLUSION_SHEET", "on").strip().lower()
    return mode if mode in ("off", "shadow", "on") else "on"


def directions_visible() -> bool:
    """Phase A ships directions in shadow; phase B flips this after the
    evaluation gate and clinician review (design §9)."""
    return os.environ.get("MEDAGENT_CONCLUSION_DIRECTIONS", "off").strip().lower() == "on"


def _load_json(name: str) -> Dict[str, Any]:
    if name not in _cache:
        raw = (
            resources.files("medagent.content")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
        _cache[name] = json.loads(raw)
    return _cache[name]


def _patient_labels() -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for pathway in _load_json("differentials.json").get("pathways", []):
        for hypothesis in pathway.get("hypotheses", []):
            if hypothesis.get("patient_label"):
                labels[hypothesis["id"]] = hypothesis["patient_label"]
    return labels


def _self_care_group(group_key: str, tier: str) -> Optional[Dict[str, Any]]:
    groups = _load_json("self_care.json").get("groups", [])
    for wanted in (group_key, "generic"):
        for group in groups:
            if (
                group.get("match") == wanted
                and group.get("tier") == tier
                and group.get("reviewed_by")
            ):
                return group
    return None


def _certainty(hypothesis, group: str) -> str:
    if group == "rule_out":
        return "cannot_exclude"
    if (
        hypothesis.status == "supported"
        and hypothesis.prior_band == "high"
        and len(hypothesis.evidence_for) >= 2
    ):
        return "more_likely"
    return "possible"


def _select_directions(accepted) -> tuple[List[Dict[str, Any]], bool]:
    labels = _patient_labels()
    active = accepted.active()
    rule_outs = [
        h
        for h in active
        if h.category in ("serious_condition", "red_flag_condition")
    ]
    # "unlikely" is the proposer's question-economics downgrade (entered on
    # the chief complaint alone, workup complete, nothing more supporting).
    # For a common condition with verbatim evidence that is still a fair
    # "possible" direction at close; for a serious condition it reads as
    # worked-up-and-unlikely, so serious "unlikely" stays out of D2.
    likelies = [h for h in active if h.category == "common_condition"]
    likelies += [
        h
        for h in accepted.hypotheses.values()
        if h.category == "common_condition"
        and h.status == "unlikely"
        and h.evidence_for
    ]
    # Supported rule-outs outrank merely-active ones, and red-flag linkage
    # breaks ties — a strongly evidenced serious hypothesis must never be
    # crowded out by weakly evidenced ones (febrile-headache case, 2026-10-23).
    rule_outs.sort(
        key=lambda h: (
            0 if h.status == "supported" else 1,
            0 if h.red_flags else 1,
            -PRIOR_RANK.get(h.prior_band, 0),
            -len(h.evidence_for),
        )
    )
    likelies.sort(
        key=lambda h: (-PRIOR_RANK.get(h.prior_band, 0), -len(h.evidence_for))
    )
    # I2: rule-out directions are never dropped by truncation. Common
    # directions fill the remaining slots up to three total.
    chosen = list(rule_outs)
    chosen += likelies[: max(0, 3 - len(chosen))]
    # Abstention (design §5.4, recalibrated in the phase-A report): with a
    # complete workup the DSL's evidence lists are thin by construction, so
    # the operative rule is "nothing selectable" rather than an evidence
    # count the content cannot reach.
    abstained = not chosen
    if abstained:
        return [], True
    directions: List[Dict[str, Any]] = []
    for hypothesis in chosen:
        group = "rule_out" if hypothesis in rule_outs else "likely"
        against_note = None
        if hypothesis.evidence_against:
            ref = hypothesis.evidence_against[0]
            if ref.fact_key == "resp_breathing":
                against_note = "你否认了呼吸困难等表现，这一点不支持它"
        directions.append(
            {
                "hypothesis_id": hypothesis.id,
                "patient_label": labels.get(hypothesis.id),
                "group": group,
                "certainty": _certainty(hypothesis, group),
                "support_quotes": [ref.quote for ref in hypothesis.evidence_for[:2]],
                "against_note": against_note,
            }
        )
    # A direction without a patient-facing label may not be shown to the
    # patient (design §5.3.5); it still counted toward selection above.
    return directions, False


def _gaps(state: CaseState, summary, accepted) -> List[Dict[str, Any]]:
    details = (summary or {}).get("unknown_details") or []
    discriminating_keys = set()
    for hypothesis in accepted.active():
        discriminating_keys.update(hypothesis.discriminating_questions)
    # discriminating_questions hold question ids; the summary's unknown
    # details carry fact keys — match on fact key membership via question
    # ids is not possible here, so measurement facts and any unknown that
    # shares a key with a discriminating question id both count.
    scored = []
    for detail in details:
        key = detail.get("key")
        if not key:
            continue
        score = 0
        if key in MEASUREMENT_FACTS:
            score += 2
        if key in discriminating_keys:
            score += 2
        scored.append((score, key, detail.get("label") or key))
    scored.sort(key=lambda item: -item[0])
    return [
        {
            "fact_key": key,
            "label": label,
            "impact": GAP_IMPACTS.get(key, DEFAULT_GAP_IMPACT),
            "significant": score >= 2,
        }
        for score, key, label in scored[:3]
    ]


def _disposition(state, tier, directions, due_hours, show_directions):
    if tier == "urgent":
        return {
            "level": "urgent_today",
            "window_hours": due_hours or 24,
            "text": "建议今天内到线下医疗机构当面评估，不要在家观察等待。",
            "department_hint": None,
        }
    rule_outs = [d for d in directions if d["group"] == "rule_out"]
    if rule_outs:
        window = due_hours or 48
        top = rule_outs[0]
        if show_directions and top.get("patient_label"):
            text = (
                f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊，"
                f"排除 {top['patient_label']}。"
            )
        else:
            text = f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
        return {
            "level": "routine_visit",
            "window_hours": window,
            "text": text,
            "department_hint": None,
        }
    return {
        "level": "self_care",
        "window_hours": due_hours,
        "text": (
            "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。"
        ),
        "department_hint": None,
    }


def compose_conclusion(
    state: CaseState, summary: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Build the conclusion sheet for a just-closed session, or None when
    no sheet should exist (not closed, emergency route, still open)."""
    if state.phase != Phase.CLOSED:
        return None
    if state.closed_reason not in ("confirmed_by_user", "urgent_route"):
        return None
    if state.risk.level == RiskLevel.EMERGENCY:
        return None
    tier = (
        "urgent"
        if (
            state.closed_reason == "urgent_route"
            or state.risk.level == RiskLevel.URGENT
        )
        else "routine"
    )
    plan = state.follow_up or build_follow_up_plan(state) or {}
    due_hours = plan.get("due_in_hours")

    workspace = RuleBasedProposer().propose(state)
    accepted, _rejected = validate_workspace(state, workspace)
    show_directions = directions_visible()
    directions, abstained = _select_directions(accepted)
    disposition = _disposition(state, tier, directions, due_hours, show_directions)
    gaps = _gaps(state, summary, accepted)
    if tier == "routine" and disposition["level"] == "self_care":
        if any(gap["significant"] for gap in gaps):
            window = due_hours or 72
            disposition = {
                "level": "routine_visit",
                "window_hours": window,
                "text": (
                    "可以在家先观察；把下方缺的信息补上，"
                    f"如果 {window} 小时后还没有好转，请到线下医疗机构就诊。"
                ),
                "department_hint": None,
            }
    group_key = "generic"
    status_rank = {"supported": 0, "active": 1, "unlikely": 2, "refuted": 3}
    for hypothesis in sorted(
        accepted.hypotheses.values(),
        key=lambda h: status_rank.get(h.status, 4),
    ):
        if hypothesis.id in PATHWAY_GROUPS:
            group_key = PATHWAY_GROUPS[hypothesis.id]
            break
    care = _self_care_group(group_key, tier) or {}
    subject = state.facts.get("consult_for")
    previous = state.conclusion or {}
    sheet = {
        "version": 1,
        "revision": int(previous.get("revision", 0)) + 1,
        "tier": tier,
        "subject_kind": subject.value if subject is not None else "self",
        "disposition": disposition,
        "goal_line": None,
        "directions": directions,
        "directions_abstained": abstained,
        "directions_visible": show_directions,
        "abstain_text": ABSTAIN_TEXT if abstained else None,
        "gaps": gaps,
        "self_care": {
            "do": list(care.get("do", [])),
            "avoid": list(care.get("avoid", [])),
            "measure": list(care.get("measure", [])),
        },
        "return_now_if": list(
            (summary or {}).get("return_now_if") or RETURN_NOW_IF
        ),
        "outlook": care.get("course"),
        "follow_up": (
            {"kind": plan.get("kind"), "due_in_hours": due_hours}
            if plan
            else None
        ),
        "generated_by": "conclusion-composer/v1",
        "content_versions": {
            "differentials": _load_json("differentials.json").get("version"),
            "self_care": _load_json("self_care.json").get("version"),
        },
    }
    rendered = json.dumps(sheet, ensure_ascii=False)
    if any(phrase in rendered for phrase in BANNED_PHRASES):
        raise ValueError("conclusion sheet failed the banned-phrase lint")
    return sheet
