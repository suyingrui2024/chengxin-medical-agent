"""Rewriting a question the patient has already partly answered.

The model may propose "有没有发烧、咳嗽？", but if the patient already said
they have no fever, asking about it again wastes the turn.  These functions
remove the options that have been denied, and leave the rest of the question
alone -- including the purpose clause, because rewriting that would change
what the question is for rather than what it asks.

Nothing here invents or rewords a clinical term.  A token is dropped only
when a recorded fact explicitly denies it.
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping

from ...reports.bridging import SELF_BRIDGED_OPENERS


_PURPOSE_CLAUSE_MARKERS = ("为了", "想先确认", "想确认", "先确认", "好判断")

_DENIED_ITEM_SYNONYMS = {
    "发热": ("发烧",),
    "发烧": ("发热",),
}

def _recorded_denies_token(texts: List[str], token: str) -> bool:
    """True when some recorded fact text explicitly denies ``token``."""
    variants = (token,) + _DENIED_ITEM_SYNONYMS.get(token, ())
    for variant in variants:
        for prefix in ("没有", "无", "不", "没"):
            needle = prefix + variant
            if any(needle in text for text in texts):
                return True
    return False

def _strip_denied_items(text: str, facts: Mapping[str, Any]) -> str:
    """Drop already-denied items from a composite question (Y-10/P-02).

    Only "有没有/有无 …？" clauses are filtered, and only tokens with
    an explicit recorded denial ("没有发热" denies "发热"). A clause
    that loses every token is dropped; the genuinely new remainder
    stays askable. Purpose clauses ("为了确认有没有…") are never
    touched. Recorded fact texts (values + evidence) are the denial
    corpus; nothing here invents or rewords a clinical term.
    """
    recorded: List[str] = []
    for fact in facts.values():
        value = getattr(fact, "value", None)
        if isinstance(value, str):
            recorded.append(value)
        evidence = getattr(fact, "evidence", None)
        if isinstance(evidence, str):
            recorded.append(evidence)
    if not recorded:
        return text
    # A model question carries its bridging prefix in the same clause
    # as the question ("你说…，想先确认一下：有没有…？"). The prefix's
    # purpose half-sentence must not be mistaken for a question clause,
    # so it is split off at the colon that follows the purpose marker
    # and only the question body is filtered.
    prefix, body = "", text
    for opener in SELF_BRIDGED_OPENERS:
        if not text.startswith(opener):
            continue
        for marker in _PURPOSE_CLAUSE_MARKERS:
            marker_at = text.find(marker)
            if marker_at < 0:
                continue
            colon_at = text.find("：", marker_at)
            if colon_at >= 0:
                prefix, body = text[: colon_at + 1], text[colon_at + 1 :]
            break
        break
    filtered = _filter_question_clauses(body, recorded)
    result = prefix + filtered
    return result if result.strip() else text

def _filter_question_clauses(text: str, recorded: List[str]) -> str:
    """Drop the options the patient has already denied from a question.

    "有没有发烧、咳嗽" becomes "有没有咳嗽" once the patient has denied a
    fever.  Only clauses that actually ask "is there ..." are touched, and
    clauses carrying a purpose marker are left alone -- rewriting those would
    change what the question is for, not what it asks.

    If every option in a clause is denied, the clause is dropped; if that
    would empty the whole text, the original is returned instead, because an
    empty question is worse than a repeated one.
    """
    clauses = re.split(r"(？|\?|；|;)", text)
    out: List[str] = []
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        delimiter = clauses[index + 1] if index + 1 < len(clauses) else ""
        index += 2
        match = re.search(r"(有没有|有无)", clause)
        if match is None or any(
            marker in clause for marker in _PURPOSE_CLAUSE_MARKERS
        ):
            out.append(clause + delimiter)
            continue
        head, tail = clause[: match.end()], clause[match.end() :]
        tokens = [part for part in re.split(r"[、，,或]", tail) if part.strip()]
        kept = [
            token
            for token in tokens
            if not _recorded_denies_token(recorded, token.strip())
        ]
        if not kept:
            continue
        if len(kept) == len(tokens):
            out.append(clause + delimiter)
        else:
            out.append(head + "、".join(token.strip() for token in kept) + delimiter)
    result = "".join(out)
    return result if result.strip() else text


__all__ = [
    "_strip_denied_items",
]
