"""Negation-aware substring matching for patient free text.

The reasoning layer matches clinical terms inside what the patient
actually typed. A naive ``term in text`` check reads "没有呕吐" as
evidence *for* vomiting — in real consultations negated mentions are
roughly half of all symptom mentions, so this was the single largest
source of false evidence in the differential workspace.

``contains_unnegated`` returns True only when at least one occurrence
of the term is not immediately preceded (within a short window) by a
negation cue. Negated occurrences are simply ignored — they never
count as support, and (deliberately) never auto-count as refutation
either: the absence of vomiting does not refute appendicitis, it just
fails to support gastroenteritis.
"""
from __future__ import annotations

NEGATION_CUES = ("没有", "没", "无", "未", "否认", "未见", "不伴")
WINDOW = 6
_CLAUSE_BREAKS = "，。；、！？,.;!?：:"


def _occurrence_negated(text: str, index: int) -> bool:
    window = text[max(0, index - WINDOW) : index]
    # A negation only scopes within its own clause: "感冒没好，又发热了"
    # has a real fever — the 没 belongs to the previous clause.
    cut = max((window.rfind(mark) for mark in _CLAUSE_BREAKS), default=-1)
    if cut >= 0:
        window = window[cut + 1 :]
    return any(cue in window for cue in NEGATION_CUES)


def find_unnegated(text: str, term: str) -> int:
    """Index of the first non-negated occurrence of ``term``, or -1."""
    if not text or not term:
        return -1
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return -1
        if not _occurrence_negated(text, index):
            return index
        start = index + 1


def contains_unnegated(text: str, term: str) -> bool:
    return find_unnegated(text, term) >= 0
