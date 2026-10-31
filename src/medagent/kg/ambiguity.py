"""When is it safe to ask the patient what they meant?

Asking costs a turn. Asking at the wrong moment costs more than a turn:
「心口窝疼得厉害，一阵一阵冒汗」 already triggers the high-risk chest
pain flag, and a clarification question there would put a question mark
in front of an emergency. The same words without the sweating trigger
nothing, and asking is exactly right.

So the ordering is not a nicety, it is the rule:

    red flag fires  ->  escalate, never ask
    no red flag     ->  a genuine ambiguity may be worth one question

A second constraint: only ask about a *symptom-like* span. Normalisation
fails on most sentences — names, times, feelings — and interrogating the
patient about every unrecognised word would make the product unusable.

This module decides; it does not ask. Wiring a question into the flow is
a clinical UX change that has to be reviewed, so for now the decision is
recorded as an event and nothing acts on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Characters that make a span read as a complaint rather than a noun.
SYMPTOM_MARKERS = (
    "疼", "痛", "胀", "酸", "麻", "晕", "闷", "痒", "烧", "堵", "抽", "跳",
    # A vague complaint is the *best* case for asking: no red flag, no
    # usable term, and the patient can usually point at it.
    "不舒服", "难受", "不适",
)

#: Body words whose meaning is genuinely ambiguous in colloquial use.
#: 「心口窝」 is the canonical case: epigastrium or precordium, and the
#: difference decides whether this is a stomach complaint or a heart one.
AMBIGUOUS_LOCATIONS = {
    "心口窝": ("胸口", "上腹"),
    "心口": ("胸口", "上腹"),
    "肚子": ("上腹", "下腹"),
    "小肚子": ("下腹",),
    "胃口": ("上腹",),
}

#: A span longer than this is a sentence, not a term.
MAX_SPAN = 12


@dataclass(frozen=True)
class Clarification:
    phrase: str
    options: tuple[str, ...]
    reason: str


def _spans(text: str) -> list[str]:
    """Runs of Chinese characters that could name a symptom."""
    return [
        run
        for run in re.findall(r"[\u4e00-\u9fff]{2,%d}" % MAX_SPAN, text)
        if any(marker in run for marker in SYMPTOM_MARKERS)
    ]


def should_clarify(text: str, triggered_flags: list[str]) -> Clarification | None:
    """A clarification worth asking, or ``None``.

    ``triggered_flags`` is what the triage layer already found. Anything
    non-empty means the turn is an escalation and no question may delay
    it.
    """
    if triggered_flags:
        return None
    if not text:
        return None
    for phrase, options in AMBIGUOUS_LOCATIONS.items():
        if phrase in text:
            # Only worth asking if the complaint actually contains it; a
            # bare mention of a body part is not a complaint.
            if not _spans(text):
                return None
            return Clarification(
                phrase=phrase,
                options=options,
                reason="部位口语歧义，且未触发红旗",
            )
    return None


def candidate_options(store, clarification: Clarification) -> list[dict]:
    """Resolve the option words to concepts we actually hold."""
    found = []
    for word in clarification.options:
        for concept in store.concepts():
            if concept["canonical"] == word or word in concept["canonical"]:
                found.append(
                    {"word": word, "concept": concept["id"], "canonical": concept["canonical"]}
                )
                break
    return found
