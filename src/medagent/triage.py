from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Dict, Iterable, List, Tuple

from .domain import Assertion, CaseState, RiskAssessment, RiskLevel, utc_now
from .adaptive_questions import DIRECT_EMERGENCY_TAGS
from .red_flags import (
    FlagHit,
    RedFlagScanner,
    SemanticProvider,
    load_definitions,
    phrase_not_negated,
)


@dataclass(frozen=True)
class PhraseRule:
    id: str
    phrases: Tuple[str, ...]
    label: str


# Derived from content/red_flags.json so existing imports keep working; the
# JSON definitions are the single source of truth.
EMERGENCY_RULES = tuple(
    PhraseRule(d.id, d.phrases, d.label) for d in load_definitions()
)

# Kept importable for compatibility; the canonical copy lives in red_flags.
from .red_flags import NEGATIONS  # noqa: E402,F401

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
    """Layered deterministic-first safety gate.

    ``scan_text`` is a pure function of the input text, so results are
    memoized per instance: within a turn the same text is scanned by
    several callers (consultation + adaptive engine) but computed once.
    Layers only ever add triggers; see ``red_flags.RedFlagScanner``.
    """

    _CACHE_LIMIT = 512

    def __init__(
        self,
        scanner: RedFlagScanner | None = None,
        semantic_provider: SemanticProvider | None = None,
    ) -> None:
        if scanner is None:
            scanner = RedFlagScanner(provider=semantic_provider)
        elif semantic_provider is not None:
            scanner.provider = semantic_provider
        self._scanner = scanner
        self._scan_cache: Dict[str, Tuple[str, ...]] = {}
        self._scan_cache_lock = Lock()

    @staticmethod
    def _not_negated(text: str, start: int) -> bool:
        return phrase_not_negated(text, start)

    def scan_detailed(self, text: str) -> List[FlagHit]:
        return self._scanner.scan(text)

    def scan_text(self, text: str) -> List[str]:
        # The cache is shared across server threads; guard it with a
        # small lock (worst case without one was a benign rescan, but
        # the "memoized" contract should not rely on the GIL).
        with self._scan_cache_lock:
            cached = self._scan_cache.get(text)
        if cached is not None:
            return list(cached)
        labels: List[str] = []
        for hit in self._scanner.scan(text):
            if hit.label not in labels:
                labels.append(hit.label)
        with self._scan_cache_lock:
            if len(self._scan_cache) >= self._CACHE_LIMIT:
                self._scan_cache.clear()
            self._scan_cache[text] = tuple(labels)
        return list(labels)

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
