from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from .domain import Assertion, ObservationQuality, Source
from .assertion_evidence import UNKNOWN_PHRASES, has_uncertainty

if TYPE_CHECKING:
    from .knowledge import Question


ABSENT_PHRASES = ("没有", "无", "否认", "都没有", "未用", "没用")
YES_PHRASES = ("有", "是", "对", "出现了", "有一项")


class ObservationInputError(ValueError):
    """A patient-correctable input error with a controlled display message."""


@dataclass(frozen=True)
class Interpretation:
    value: Any
    assertion: Assertion
    source: Source = Source.PATIENT
    quality: ObservationQuality = ObservationQuality.NOT_ASSESSED


class Interpreter:
    """Question-scoped parser. It never turns missing or unclear text into a negative."""

    def interpret(
        self, question: Question, text: str, choice_value: Optional[str] = None
    ) -> Interpretation:
        raw = text.strip()
        selected = choice_value.strip() if choice_value else None
        if selected == "unknown" or any(phrase in raw for phrase in UNKNOWN_PHRASES):
            return Interpretation(value=None, assertion=Assertion.UNKNOWN)
        if question.input in {"text_or_absent", "yes_no_unknown"} and has_uncertainty(
            raw
        ):
            return Interpretation(value=None, assertion=Assertion.UNKNOWN)

        if question.input == "choice":
            allowed = {item.value for item in question.options}
            labels = {item.label: item.value for item in question.options}
            if selected in allowed:
                if selected == "unknown":
                    return Interpretation(None, Assertion.UNKNOWN)
                return Interpretation(selected, Assertion.PRESENT)
            if raw in labels:
                mapped = labels[raw]
                if mapped == "unknown":
                    return Interpretation(None, Assertion.UNKNOWN)
                return Interpretation(mapped, Assertion.PRESENT)
            return Interpretation(raw, Assertion.PRESENT)

        if question.input == "yes_no_unknown":
            if selected == "yes":
                return Interpretation(True, Assertion.PRESENT)
            if selected == "no":
                return Interpretation(False, Assertion.ABSENT)
            if any(raw.startswith(phrase) for phrase in ABSENT_PHRASES):
                return Interpretation(False, Assertion.ABSENT)
            if any(raw.startswith(phrase) for phrase in YES_PHRASES):
                return Interpretation(True, Assertion.PRESENT)
            return Interpretation(None, Assertion.UNKNOWN)

        if question.input == "age":
            months = re.search(r"(\d{1,3})\s*个?月", raw)
            years = re.search(r"(\d{1,3})(?:\s*岁|\s*周岁)", raw)
            bare = re.fullmatch(r"\s*(\d{1,3})\s*", raw)
            if months:
                return Interpretation(
                    round(int(months.group(1)) / 12, 3), Assertion.PRESENT
                )
            if years:
                return Interpretation(int(years.group(1)), Assertion.PRESENT)
            if bare:
                return Interpretation(int(bare.group(1)), Assertion.PRESENT)
            return Interpretation(raw, Assertion.PRESENT)

        if question.input == "severity":
            score = re.search(r"(?<!\d)(10|[0-9])\s*分?", raw)
            if score:
                return Interpretation(int(score.group(1)), Assertion.PRESENT)
            return Interpretation(raw, Assertion.PRESENT)

        if question.input == "text_or_absent":
            absent_pattern = (
                r"^(?:没有|无|否认|未用|没用)(?:相关|这些|上述|任何)?(?:症状|情况|不适|药物|处理|过敏|病史|用药)?[。！!]?$"
            )
            if raw in ABSENT_PHRASES or re.fullmatch(absent_pattern, raw):
                return Interpretation(None, Assertion.ABSENT)
            return Interpretation(raw, Assertion.PRESENT)

        return Interpretation(raw, Assertion.PRESENT)

    def temperature(self, text: str) -> Optional[float]:
        # Require a single complete number; never truncate 380 or 38.25, or
        # choose a reading from conflicting values. No Fahrenheit conversion.
        numbers = re.findall(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", text)
        suffixes = re.findall(r"\d\s*([a-zA-Z%％]+)", text)
        if (
            len(numbers) != 1
            or any(unit.lower() not in {"c", "celsius"} for unit in suffixes)
            or re.search(r"华氏|℉|°\s*[fk]", text, re.I)
            or re.search(r"\d\s*(?:岁|个?月|天|年|小时|分(?:钟)?|秒)", text)
            or re.search(
                r"岁|月龄|个月|血糖|血压|心率|体重|脉搏|公斤|mmol|mmhg|\b(?:kg|mg|bpm)\b", text, re.I
            )
            or re.search(r"-\s*\d", text)
        ):
            return None
        value = float(numbers[0])
        return value if 34 <= value < 44 else None
