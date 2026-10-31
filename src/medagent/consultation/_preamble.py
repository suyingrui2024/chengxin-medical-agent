"""Shared imports, constants and module-level helpers for the package."""
from __future__ import annotations

import os
from dataclasses import dataclass
from copy import deepcopy
from threading import Lock, RLock, Condition
from typing import Any, Dict, List, Optional
from uuid import uuid4

from ..domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    ObservationQuality,
    Phase,
    QuickReply,
    RiskLevel,
    RiskAssessment,
    Source,
)
from ..events import Event, EventStore
from ..publication import ReplyPublicationGate
from ..state_transitions import commit_phase
from ..interpreter import Interpretation, Interpreter, ObservationInputError
from ..knowledge import (
    ADULT_AGE_PROMPT,
    ContentRepository,
    Question,
    question_fact_gates_pass,
)
from ..adaptive_questions import DIRECT_EMERGENCY_TAGS
from ..red_flags import load_definitions
from ..self_checks import SelfCheckService
from ..triage import TriageService
from ..turn_context import TurnContext
from ..planner import RulePlanner
from ..question_ordering import (
    earliest_phase_candidates,
    filter_candidates,
    ordering_for_ranker,
)
from ..session_store import SessionStore
from ..phase_graph import planning_rank
from ..followups import RETRIAGE_OUTCOMES, apply_outcome, build_follow_up_plan
from ..reasoner import QuestionRanker
from .. import __version__, closing
from ..bridging import build_bridging_sentence
from ..handoff import LABELS as HANDOFF_LABELS
from ..worry import extract_worry_object
from ..presentation import calculation_presentation
from ..task_intents import task_intent
from ..patient_calculations import request_draft, confirmed_parameters
from ..observations import ObservationMixin
from ..task_conversation import TaskConversationMixin
from ..calculation_followup import CalculationFollowupMixin
from ..consultation_subject import (
    subject_profile,
    proposed_profile,
    compatible,
    explicit_switch,
    conflicting_reference,
)


FACT_LABELS = {
    "consult_for": "咨询对象",
    "age_years": "年龄",
    "chief_complaint": "主要问题",
    "emergency_screen": "急症筛查",
    "onset": "开始与起病方式",
    "trajectory": "变化趋势",
    "severity": "影响程度",
    "associated_symptoms": "伴随不适",
    "resp_breathing": "呼吸情况",
    "resp_cough_details": "咳嗽细节",
    "resp_intake": "饮水进食与排尿",
    "temperature_c": "体温",
    "symptom_location": "不适部位（主观定位）",
    "medications_taken": "已做处理/用药",
    "allergies": "过敏信息",
    "relevant_history": "相关病史",
    "patient_goal": "期望与担心",
}

VALUE_LABELS = {
    "self": "本人",
    "child": "孩子",
    "other_adult": "其他家人",
    "worsening": "加重",
    "improving": "减轻",
    "stable": "变化不大",
    "intermittent": "阵发性",
}

# -- Batch A controller gates (W3) -------------------------------------
# Urgent close on a single clue (trajectory=worsening alone, or one
# severity score alone) must wait for a minimum fact set: severity
# collected, associated symptoms probed, patient goal recorded. Only
# the two single-clue urgent triggers are gated; temperature/infant/
# orofacial urgent triggers keep their immediacy, and the deterministic
# emergency route is never gated by this.
URGENT_SINGLE_CLUE_TRIGGERS = frozenset({"症状持续加重", "症状影响评分较高"})
URGENT_MINIMUM_FACTS = ("severity", "associated_symptoms", "patient_goal")

# A suspended self-check task that keeps being suspended without ever
# completing is capped: after this many *consecutive* suspensions of the
# same task occurrence it is cleared and the session returns to the main
# flow instead of looping forever. "Consecutive" is per task occurrence
# (task_id + interaction_id): a successful resume, a completion, a new
# task offer, or a clear resets the run, so different occurrences each
# suspended once never accumulate toward the cap.
SELF_CHECK_SUSPEND_CAP = 3

# Short patient-facing notice attached to the next reply when the cap
# clears a suspended step, so the step is never silently dropped.
SUSPEND_CAP_NOTICE = "这一步先跳过，回到主流程继续。"

# Patient wording that refuses to disclose a field ("不想说"). A refusal
# is a recordable state: it lands as an UNKNOWN fact carrying the
# refusal quote, so the summary gate is satisfied without inventing a
# value and without rewriting the refusal as "不知道".
REFUSAL_PHRASES = (
    "不想说",
    "不太想说",
    "不方便说",
    "不愿说",
    "不愿意说",
    "拒绝说",
    "拒绝回答",
    "不想告诉",
    "不方便告诉",
    "不能说",
    "不说这个",
    "不想透露",
)

# Steady, not-improving wording ("一直没怎么好转") must not be booked as
# worsening. An explicit worsening phrase in the same evidence wins over
# the steady reading, except when it is itself negated ("没有加重").
_STEADY_NOT_IMPROVING_PHRASES = (
    "一直没怎么好转",
    "没怎么好转",
    "一直没有好转",
    "没有好转",
    "没好转",
    "不见好转",
    "不见好",
    "没有变好",
    "没见好",
    "变化不大",
    "没什么变化",
    "一直这样",
    "老样子",
    "还是老样子",
    "没有加重",
    "没加重",
)
_WORSENING_PHRASES = (
    "加重",
    "更严重",
    "越来越",
    "恶化",
    "更疼",
    "更痛",
    "更难受",
    "变严重",
)
_NEGATED_WORSENING = ("没有加重", "没加重", "没有恶化", "没恶化")

_SKIP_TEXTS = ("跳过", "暂时跳过", "跳过这一步", "先跳过", "跳过吧", "这步跳过")


def _normalize_free_text(text: str) -> str:
    return (text or "").strip().strip(" 。！!，,、；;？?～~").replace(" ", "")


# -- Batch C BURDEN (O-12): fact writeback merges, never overwrites -----
# A later short writeback for the same key used to drop denial and
# unknown segments recorded earlier ("没有受过外伤", "漏尿没注意过"),
# leaving the handoff with an incomplete fact. Merging keeps exactly
# those denial/unknown segments: ordinary present segments may still be
# superseded, but a recorded denial or unknown is only displaced by an
# explicit correction (merge=False) or by a new value that affirms the
# very term that was denied. This is a text heuristic, not a clinical
# rule: it preserves what the patient already said, it never invents
# a value.
_DENIAL_SEGMENT_MARKERS = (
    "没有",
    "没",
    "无",
    "否认",
    "不注意",
    "没注意",
    "不清楚",
    "不知道",
    "没留意",
    "未注意",
)


def _denial_segments(text: str) -> List[str]:
    segments: List[str] = []
    current = ""
    for char in text:
        if char in "、，；。":
            if current.strip():
                segments.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        segments.append(current.strip())
    return [
        segment
        for segment in segments
        if len(segment) >= 2
        and any(marker in segment for marker in _DENIAL_SEGMENT_MARKERS)
    ]


def _denied_core(segment: str) -> str:
    """The term a denial segment denies, for the supersede check."""
    for marker in ("没有", "没", "无", "否认"):
        index = segment.find(marker)
        if index >= 0:
            return segment[index + len(marker) :]
    return ""


def _preserved_segments(previous_text: Optional[str], new_value: str) -> List[str]:
    """Denial/unknown segments of ``previous_text`` the new value drops."""
    if not previous_text or not isinstance(new_value, str):
        return []
    appended: List[str] = []
    for segment in _denial_segments(previous_text):
        if segment in new_value:
            continue
        core = _denied_core(segment)
        if core and core in new_value and ("没有" + core) not in new_value:
            # The new value affirms the denied term ("发烧了" after
            # "没有发烧"): the denial is superseded, not preserved.
            continue
        appended.append(segment)
    return appended


def merge_preserving_denials(previous_text: Optional[str], new_value: str) -> str:
    """Merge a new string writeback over a previous one (O-12).

    Denial/unknown segments from ``previous_text`` that are neither
    already present in ``new_value`` nor affirmed-away by it are
    appended, so a short second writeback cannot silently drop them.
    Non-string values and empty previous texts pass through untouched.
    """
    appended = _preserved_segments(previous_text, new_value)
    if not appended:
        return new_value
    return new_value.rstrip("。！；") + "；" + "、".join(appended)


def is_summary_confirmation_text(text: str) -> bool:
    """Free-text confirmation equivalent to tapping confirm_summary.

    Deliberately narrow: only an unambiguous confirmation counts, so a
    correction ("不对，年龄写错了") never closes the session. The
    D-03 wording ("我看了，都对，没什么要改的") is covered.
    """
    normalized = _normalize_free_text(text)
    if normalized in {
        "都对",
        "没什么要改的",
        "没什么要改",
        "没有要改的",
        "确认",
        "确认无误",
        "没问题",
        "可以",
        "就这样",
        "同意",
        "我看了都对",
        "我看了没问题",
    }:
        return True
    return normalized.startswith("我看了") and (
        "都对" in normalized
        or "没什么要改" in normalized
        or "没有要改" in normalized
        or "确认无误" in normalized
    )


def is_refusal_text(text: str) -> bool:
    return bool(text) and any(phrase in text for phrase in REFUSAL_PHRASES)


def is_steady_not_improving_evidence(text: str) -> bool:
    """True when the evidence reads as steady-not-improving, not worse."""
    if not text:
        return False
    if not any(phrase in text for phrase in _STEADY_NOT_IMPROVING_PHRASES):
        return False
    unnegated = text
    for phrase in _NEGATED_WORSENING:
        unnegated = unnegated.replace(phrase, "")
    return not any(phrase in unnegated for phrase in _WORSENING_PHRASES)


def is_self_check_skip_text(text: str) -> bool:
    return _normalize_free_text(text) in _SKIP_TEXTS


__all__ = [
    "os",
    "dataclass",
    "deepcopy",
    "Lock",
    "RLock",
    "Condition",
    "Any",
    "Dict",
    "List",
    "Optional",
    "uuid4",
    "AgentReply",
    "Assertion",
    "CaseState",
    "Fact",
    "ObservationQuality",
    "Phase",
    "QuickReply",
    "RiskLevel",
    "RiskAssessment",
    "Source",
    "Event",
    "EventStore",
    "ReplyPublicationGate",
    "commit_phase",
    "Interpretation",
    "Interpreter",
    "ObservationInputError",
    "ADULT_AGE_PROMPT",
    "ContentRepository",
    "Question",
    "question_fact_gates_pass",
    "DIRECT_EMERGENCY_TAGS",
    "load_definitions",
    "SelfCheckService",
    "TriageService",
    "TurnContext",
    "RulePlanner",
    "earliest_phase_candidates",
    "filter_candidates",
    "ordering_for_ranker",
    "SessionStore",
    "planning_rank",
    "RETRIAGE_OUTCOMES",
    "apply_outcome",
    "build_follow_up_plan",
    "QuestionRanker",
    "__version__",
    "closing",
    "build_bridging_sentence",
    "HANDOFF_LABELS",
    "extract_worry_object",
    "calculation_presentation",
    "task_intent",
    "request_draft",
    "confirmed_parameters",
    "ObservationMixin",
    "TaskConversationMixin",
    "CalculationFollowupMixin",
    "subject_profile",
    "proposed_profile",
    "compatible",
    "explicit_switch",
    "conflicting_reference",
    "FACT_LABELS",
    "VALUE_LABELS",
    "URGENT_SINGLE_CLUE_TRIGGERS",
    "URGENT_MINIMUM_FACTS",
    "SELF_CHECK_SUSPEND_CAP",
    "SUSPEND_CAP_NOTICE",
    "REFUSAL_PHRASES",
    "_STEADY_NOT_IMPROVING_PHRASES",
    "_WORSENING_PHRASES",
    "_NEGATED_WORSENING",
    "_SKIP_TEXTS",
    "_normalize_free_text",
    "_DENIAL_SEGMENT_MARKERS",
    "_denial_segments",
    "_denied_core",
    "_preserved_segments",
    "merge_preserving_denials",
    "is_summary_confirmation_text",
    "is_refusal_text",
    "is_steady_not_improving_evidence",
    "is_self_check_skip_text",
]
