"""Deterministic patient-worry detection (disease batch B, WORRY).

A patient naming a worry mid-consultation ("是不是食物中毒？") must
reach the conclusion sheet and the spoken close, without depending on
the model recording it, and without ever naming a shadow direction or
a department hint. This module is the single contract for worry
recognition: the consultation layer captures with it, the conclusion
composer splits worry-formed goal values with it.

Design notes (from round-1 evidence):

- Only the FIRST worry in a session is captured: the named worry that
  the close must answer. Later worries are not overwritten into the
  slot (first-wins keeps the close accountable to one object).
- Pure severity worries ("是不是很严重") are NOT captured here: the
  disposition and severity narrative already answer them. Plan/drug
  questions ("是不是要去医院") are not captured either: they belong
  to the goal slot, not the worry slot.
- The object is the patient's own wording, capped at 30 chars. The
  composer sanitizes it against the banned-phrase lint before it
  reaches any patient-visible surface.
"""

from __future__ import annotations

from typing import Optional

# Longest-first: "是不是得了" must win over "是不是".
_WORRY_MARKERS = ("是不是得了", "会不会得了", "是不是", "会不会是", "是不是会")

# Objects that are pure severity: the disposition already answers them.
_SEVERITY_ONLY = ("很严重", "严重", "要紧", "危险", "很危险")

# Objects that are plan/drug questions, not disease worries: they belong
# to the goal slot (and the batch-A exam-goal branch answers visit ones).
_PLAN_FRAGMENTS = ("去医院", "看医生", "就诊", "挂号", "吃药", "打针", "输液")

_TERMINATORS = "？?。！!；;\n"
_LIST_CUTS = ("，", "、", "；", "而且", "但是", "不过")
_TRAILING_PARTICLES = ("了", "的", "吗", "呢", "啊", "吧")
_LEADING_STRIPS = ("得了", "是", "有")
_MAX_OBJECT_LEN = 30


def _extract_span(text: str, start: int) -> str:
    """Text after a worry marker up to the sentence terminator."""
    end = len(text)
    for index in range(start, len(text)):
        if text[index] in _TERMINATORS:
            end = index
            break
    span = text[start:end]
    # Cut at the first list/contrast break, unless what follows starts
    # a 还是/或者 alternative ("是不是肺炎，还是普通感冒" is one worry
    # with two alternatives, not two worries).
    cut = len(span)
    for marker in _LIST_CUTS:
        index = span.find(marker)
        if 0 <= index < cut:
            rest = span[index + len(marker) :]
            if rest.startswith(("还是", "或者", "或是")):
                continue
            cut = index
    span = span[:cut]
    for prefix in _LEADING_STRIPS:
        if span.startswith(prefix) and len(span) > len(prefix):
            span = span[len(prefix) :]
            break
    span = span.strip()
    while span and span[-1] in _TRAILING_PARTICLES:
        span = span[:-1].strip()
    return span


def extract_worry_object(text: str) -> Optional[str]:
    """The worried-about object in a patient sentence, or None.

    Returns None for non-worry sentences, pure severity worries, and
    plan/drug questions. The earliest marker in the text wins.
    """
    if not text:
        return None
    earliest: Optional[int] = None
    marker_len = 0
    for marker in _WORRY_MARKERS:
        index = text.find(marker)
        if index >= 0 and (earliest is None or index < earliest):
            earliest = index
            marker_len = len(marker)
    if earliest is None:
        return None
    obj = _extract_span(text, earliest + marker_len)
    if not obj or len(obj) > _MAX_OBJECT_LEN:
        return None
    if obj in _SEVERITY_ONLY:
        return None
    if any(fragment in obj for fragment in _PLAN_FRAGMENTS):
        return None
    return obj


def is_worry_sentence(text: str) -> bool:
    """Whether a sentence carries a capturable disease worry."""
    return extract_worry_object(text) is not None
