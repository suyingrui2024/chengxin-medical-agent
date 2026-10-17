from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .domain import Assertion, CaseState, RiskAssessment, RiskLevel, utc_now


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

    def assess(self, state: CaseState, latest_text: str = "") -> RiskAssessment:
        triggers = self.scan_text(latest_text)
        # Editing a non-screening field cannot retract an issued emergency route.
        if state.closed_reason == "emergency_route":
            triggers.extend(state.risk.triggers)
        screen = state.facts.get("emergency_screen")
        if screen and screen.assertion == Assertion.PRESENT and screen.value is True:
            triggers.append("患者确认急症筛查项")
        if triggers:
            return RiskAssessment(RiskLevel.EMERGENCY, sorted(set(triggers)), utc_now())

        urgent: List[str] = []
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
