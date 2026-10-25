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
fails to support gastroenteritis. A "、"-separated denial list is one
negation scope ("没有气短、喘" denies both items), and a negation
immediately before the term ("不是", "不敢", bare "不") also denies
it. ``find_unnegated_certain`` additionally excludes uncertain
mentions ("不清楚", "没注意过", ...); ``contains_unnegated`` does
not, by design.
"""
from __future__ import annotations

NEGATION_CUES = ("没有", "无", "未", "否认", "未见", "不伴")
WINDOW = 6
# Clause breaks that cut a negation's scope. "、" is deliberately NOT
# here: in a denial list ("没有气短、喘") the negation scopes over the
# whole list, and treating 、 as a break let the later items ("喘")
# read as affirmed evidence.
_CLAUSE_BREAKS = "，。；！？,.;!?：:"
# Negations that only count when they sit immediately before the term
# (same rule as the bare 没 below): "不是天旋地转" denies the term,
# "不敢咳嗽" is avoidance, not a cough — but "不是很疼" is still a
# real pain (很 intervenes) and "不断咳嗽" is still a cough (断
# intervenes: the window ends with 断, not 不).
_ADJACENT_NEGATIONS = ("不是", "不敢", "不")
# Markers of an uncertain mention: the patient is saying they do not
# know, not that the symptom is present. These are NOT negation cues
# for contains_unnegated (its semantics are unchanged); they are only
# consulted by find_unnegated_certain.
UNCERTAINTY_MARKERS = ("不清楚", "不知道", "没注意", "没留意", "不确定", "说不清", "不记得")


def _occurrence_negated(text: str, index: int) -> bool:
    window = text[max(0, index - WINDOW) : index]
    # A negation only scopes within its own clause: "感冒没好，又发热了"
    # has a real fever — the 没 belongs to the previous clause.
    cut = max((window.rfind(mark) for mark in _CLAUSE_BREAKS), default=-1)
    if cut >= 0:
        window = window[cut + 1 :]
    if any(cue in window for cue in NEGATION_CUES):
        return True
    # A bare 没 is a negation only when it directly precedes the term
    # ("没发热"). Otherwise it usually negates a verb further back —
    # "没睡好胸口疼" has a real chest pain, and reading the 没 as
    # negating 胸口 would silently kill the whole chest pathway.
    # The same adjacency rule covers 不是 / 不敢 / bare 不:
    # "不是天旋地转" denies the dizziness, "不敢咳嗽" is avoidance of
    # coughing, "不发热" denies fever — while "不是很疼" (很
    # intervenes) and "不断咳嗽" (断 intervenes) stay affirmed.
    if window.endswith(_ADJACENT_NEGATIONS) or window.endswith("没"):
        return True
    # A 、-joined denial list is one negation scope: "没有A、B、C"
    # denies the whole list, even when the head cue sits further than
    # WINDOW characters before a later item ("也没有走路不稳、说话
    # 不清" denies 说话不清, 7 characters past 没有). WINDOW itself
    # is unchanged; this only extends the lookback to the head of
    # the term's own 、 list within its clause.
    start = index
    while start > 0 and text[start - 1] not in _CLAUSE_BREAKS:
        start -= 1
    segment = text[start:index]
    if "、" in segment:
        head = segment.split("、", 1)[0]
        if any(cue in head for cue in NEGATION_CUES):
            return True
    return False


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


def _occurrence_uncertain(text: str, index: int, term_len: int) -> bool:
    # Before the term: any uncertainty marker in the same clause
    # ("不清楚有没有发热", "没注意过发热"). The clause start is the
    # last clause break before the term; 、 does not break the clause
    # here either, matching the negation-scope rule above.
    start = index
    while start > 0 and text[start - 1] not in _CLAUSE_BREAKS:
        start -= 1
    if any(marker in text[start:index] for marker in UNCERTAINTY_MARKERS):
        return True
    # After the term: any uncertainty marker within WINDOW characters
    # ("走路不稳不清楚", "发热没注意过").
    after = text[index + term_len : index + term_len + WINDOW]
    return any(marker in after for marker in UNCERTAINTY_MARKERS)


def find_unnegated_certain(text: str, term: str) -> int:
    """Index of the first occurrence of ``term`` that is neither
    negated (per :func:`find_unnegated`) nor uncertain — i.e. the
    patient actually affirmed it, rather than denying it or saying
    they do not know. Returns -1 when no such occurrence exists.

    ``contains_unnegated`` deliberately does NOT apply this test:
    uncertainty is a weaker signal than negation, and changing its
    return values would silently re-scope every existing caller.
    """
    if not text or not term:
        return -1
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return -1
        if not _occurrence_negated(text, index) and not _occurrence_uncertain(
            text, index, len(term)
        ):
            return index
        start = index + 1
