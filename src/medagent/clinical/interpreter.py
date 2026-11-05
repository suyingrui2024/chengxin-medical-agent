"""Reading a patient's answer as a structured interpretation.

Every answer to a question becomes an ``Interpretation``: a value, an
assertion, a source and a quality.  The rules that matter are the ones about
what must NOT be inferred -- an uncertain phrasing yields UNKNOWN rather than
a guess, and an explicit "I do not know" is an answer, not a missing one.

Start at ``Interpreter.interpret``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

from ..core.domain import Assertion, ObservationQuality, Source
from ..guards.assertion_evidence import UNKNOWN_PHRASES, has_uncertainty

if TYPE_CHECKING:
    from .knowledge import Question


ABSENT_PHRASES = ("没有", "无", "否认", "都没有", "未用", "没用")

# 肯定回答只认「整句就是在说 yes」，不认单字前缀。
# 旧实现用 startswith(("有","是","对"))，"对了，帮我儿子也看一下" 会因为开头
# 的"对"被读成"有危险信号"，直接触发 120。这类口头语（对了/对不起/对于）
# 在中文里极常见，而判断题问的是"有没有"，一句话开头撞上某个字并不等于肯定。
YES_PATTERNS = (
    # 有 / 是 / 对 / 是的 / 对的 / 是啊 —— 整句只有这些
    re.compile(r"^(?:有|是|对)(?:的|啊|呀)?$"),
    # 同上但后面接标点，例如"有，昨天开始的"
    re.compile(r"^(?:有|是|对)(?:的|啊|呀)?[\s，,。；;！!、]"),
    # 有这项 / 有一项 / 有这种情况 / 出现了
    re.compile(r"^(?:有|是|对)(?:这|那|上述|该)?(?:一)?(?:项|个|种|些)"),
    re.compile(r"^出现了"),
    re.compile(r"^(?:确实|的确)(?:有|是)"),
    # 有点疼 / 有些难受 / 有一点 —— 症状描述里的肯定
    re.compile(r"^(?:有|是)(?:点|些|一点)"),
)


def _is_affirmative(raw: str) -> bool:
    """True only when the answer itself is a yes.

    Kept deliberately narrow: a false "有" on the red-flag screen escalates a
    patient to an emergency call, while a false "unknown" only asks again.
    """
    return any(pattern.match(raw) for pattern in YES_PATTERNS)


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
        """Read the patient's answer as a structured interpretation.

        "I do not know" is an answer, not a missing one: it comes back as
        UNKNOWN with no value, and a field answered that way is a gap the sheet
        can show rather than a blank.

        A chosen option is read first, then its label, then the free text.  When
        the question takes free text, uncertainty in the wording also yields
        UNKNOWN rather than a guess.
        """
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
            if _is_affirmative(raw):
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
