from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .domain import Assertion, CaseState, RiskAssessment, RiskLevel, utc_now
from .adaptive_questions import DIRECT_EMERGENCY_TAGS


@dataclass(frozen=True)
class PhraseRule:
    id: str
    phrases: Tuple[str, ...]
    label: str


EMERGENCY_RULES = (
    PhraseRule("unresponsive", ("叫不醒", "失去意识", "意识不清", "昏迷"), "意识明显异常"),
    PhraseRule("seizure", ("正在抽搐", "持续抽搐", "反复抽搐"), "抽搐"),
    PhraseRule("cyanosis", ("嘴唇发紫", "口唇发紫"), "口唇发紫"),
    PhraseRule("severe_breathing", ("喘不上气", "不能呼吸", "严重呼吸困难"), "严重呼吸困难"),
    PhraseRule("chest_pain", ("持续胸痛", "压榨样胸痛", "胸痛大汗"), "高风险胸痛描述"),
    PhraseRule("major_bleeding", ("大出血", "血止不住", "大量呕血"), "大量或无法控制的出血"),
)

NEGATIONS = ("没有", "无", "否认", "不是", "未出现", "不伴")

RISK_TAG_LABELS = {
    "unresponsive": "意识明显异常",
    "seizure": "抽搐",
    "cyanosis": "口唇或皮肤发紫",
    "severe_breathing": "严重呼吸困难",
    "chest_discomfort": "患者选择胸部不适",
    "major_bleeding": "大量或无法控制的出血",
}

OROFACIAL_TERMS = ("牙", "牙齿", "牙龈", "下颌", "颌部", "口腔", "面部", "脸")


class TriageService:
    """Deterministic safety gate evaluated before and after every planning step."""

    @staticmethod
    def _not_negated(text: str, start: int) -> bool:
        prefix = re.split(r"[，。！？；,;!?\n]|但是|但|不过|现在", text[:start])[-1][-6:]
        return not any(token in prefix for token in NEGATIONS)

    def scan_text(self, text: str) -> List[str]:
        triggers: List[str] = []
        for rule in EMERGENCY_RULES:
            for phrase in rule.phrases:
                if any(
                    self._not_negated(text, match.start())
                    for match in re.finditer(re.escape(phrase), text)
                ):
                    triggers.append(rule.label)
                    break
        return triggers

    def assess(
        self,
        state: CaseState,
        latest_text: str = "",
        risk_tags: Iterable[str] = (),
    ) -> RiskAssessment:
        triggers = self.scan_text(latest_text)
        selected_tags = set(risk_tags)
        triggers.extend(
            RISK_TAG_LABELS[tag]
            for tag in selected_tags
            if tag in DIRECT_EMERGENCY_TAGS
        )
        # Editing a non-screening field cannot retract an issued emergency route.
        if state.closed_reason == "emergency_route":
            triggers.extend(state.risk.triggers)
        screen = state.facts.get("emergency_screen")
        if screen and screen.assertion == Assertion.PRESENT and screen.value is True:
            triggers.append("患者确认急症筛查项")
        if triggers:
            return RiskAssessment(RiskLevel.EMERGENCY, sorted(set(triggers)), utc_now())

        urgent: List[str] = []
        complaint = str(state.get("chief_complaint", ""))
        orofacial_complaint = any(term in complaint for term in OROFACIAL_TERMS)
        if orofacial_complaint and "exertional" in selected_tags:
            urgent.append("口腔、牙齿或下颌不适与活动相关")
        if orofacial_complaint and "cold_sweat" in selected_tags:
            urgent.append("口腔、牙齿或下颌不适伴冷汗")
        if "jaw_or_upper_body" in selected_tags and selected_tags.intersection(
            {"exertional", "cold_sweat"}
        ):
            urgent.append("下颌或上半身不适伴活动相关或冷汗表现")
        severity = state.get("severity")
        if isinstance(severity, int) and severity >= 8:
            urgent.append("症状影响评分较高")
        if state.get("trajectory") == "worsening":
            urgent.append("症状持续加重")
        temperature = state.get("temperature_c")
        if isinstance(temperature, (int, float)) and temperature >= 40:
            urgent.append("测得体温达到 40℃")
        age = state.get("age_years")
        if (
            isinstance(age, (int, float))
            and age < 0.25
            and isinstance(temperature, (int, float))
            and temperature >= 38
        ):
            urgent.append("3 月龄以下婴儿体温达到 38℃")
        if urgent:
            return RiskAssessment(RiskLevel.URGENT, urgent, utc_now())

        if state.assertion("emergency_screen") == Assertion.ABSENT:
            return RiskAssessment(RiskLevel.ROUTINE, [], utc_now())
        return RiskAssessment(RiskLevel.UNDETERMINED, [], utc_now())
