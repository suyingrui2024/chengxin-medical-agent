"""The wording of a consultation: the labels, the phrases, and how they are read.

A patient answers in their own words, so the engine has to recognise several
ways of saying the same thing -- refusing to disclose, skipping a step,
reporting a change that is steady rather than worsening.  Those phrase lists
live here, together with the predicates that read them.

The labels are the other half: the field names the sheet shows the patient in
their own language, so a clinician and the patient are looking at the same
field under the same name.

Nothing here interprets a fact or decides a phase.  This module only knows
what patients say and what the fields are called.
"""
from __future__ import annotations

from typing import List, Optional


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
    "male": "男",
    "female": "女",
    "other": "其他",
    "child": "孩子",
    "other_adult": "其他家人",
    "worsening": "加重",
    "improving": "减轻",
    "stable": "变化不大",
    "intermittent": "阵发性",
}

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

def _normalize_free_text(text: str) -> str:
    return (text or "").strip().strip(" 。！!，,、；;？?～~").replace(" ", "")

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
    "FACT_LABELS",
    "VALUE_LABELS",
    "is_refusal_text",
    "is_self_check_skip_text",
    "is_steady_not_improving_evidence",
    "is_summary_confirmation_text",
    "merge_preserving_denials",
]
