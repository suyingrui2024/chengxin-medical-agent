"""P4 direction guards: quote/assertion consistency for conclusion directions.

Pure functions only (no engine, no I/O beyond the differentials content
asset, no conclusion import — conclusion.py calls into here, never the
reverse). They exist because the shared ``text_match`` affirmation test
misses three shapes seen in the second disease-test round, and because
a direction must never rest on coding values alone:

1. Perception-verb negation (P-03 / T-06, the ``喘 -> 困难`` chain):
   ``也不觉得呼吸困难`` reads as affirmed under the shared window rule
   (``不`` is not adjacent to the term — ``觉得`` intervenes), yet the
   patient is denying the symptom. Any ``不/没 + 觉得/感到/感觉`` scope
   over the term in its own clause is a denial.
2. Sentence-final uncertainty over an enumerated recapitulation
   (N-10): ``头晕是天旋地转还是头重脚轻、每次持续多久、……、走路稳不稳
   都不清楚`` — the uncertainty marker sits far past the 6-character
   after-window, but the whole ``、``-joined list is recapitulated
   options closed by ``都不清楚``. When the tail of a term's sentence
   (no ``，``/``；`` break between term and marker) ends in an
   uncertainty marker, every ``、``-listed term in that tail is
   uncertain, not affirmed.
3. Local-descriptor substrings (D-13): ``脸颊发红发热`` is local
   warmth, not systemic fever. A fever term whose occurrence sits
   inside a local descriptor (``发红`` / ``红肿`` / ``局部`` / ``患处``
   / a skin site word immediately before it) is not systemic evidence.

Direction-level rules (applied in ``_select_directions``):

* trajectory / severity / temperature coding alone never anchors a
  direction on its own (J-07: a trajectory sentence was the sole
  support for a chest-pain direction the patient never reported).
  A direction whose evidence refs all point at coding facts is
  dropped — unless the patient actually reported the direction's
  core symptom: non_cardiac_chest is the one hypothesis with no DSL
  symptom terms at all, and its probe case (chest chief "胸口闷" +
  stable trajectory) is a pinned existing behaviour, so for it the
  anchor is an affirmed chest-site mention (胸痛 / 胸口 / 胸闷 /
  心口) in the patient's own words. 心慌 alone is palpitation, not
  chest pain, and does not anchor it — that is exactly the J-07
  shape.
* a ``rule_out`` (``cannot_exclude``) direction supported only by
  fever terms and/or coding wording is dropped when its pathway is
  not one of the chief complaint's pathways (cross-system fever-only,
  U-11 / U-14 shape). Same-pathway fever-only rule-outs are kept:
  fever is that pathway's own evidence, and the red-flag-entry design
  keeps serious hypotheses to be excluded on their own chief.
* a direction whose every valid quote is denied by a recorded ABSENT
  fact (the ABSENT fact's own evidence names the quote term, or its
  fever synonym) is dropped, for any group — an ABSENT fact is the
  patient's denial in recorded form, so it cannot simultaneously
  support a ``cannot_exclude`` (or any) direction on that symptom.

Nothing here changes a disposition level, a threshold, or a visibility
default: guards only drop directions / quotes, and the narrative
severity fallback only restates a recorded score (see
:func:`severity_consistent_text`).
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any, Dict, List, Optional, Set

from .text_match import (
    NEGATION_CUES,
    UNCERTAINTY_MARKERS,
    contains_unnegated,
)

# Clause breaks shared with text_match: "、" is deliberately NOT one,
# so a denial / uncertainty scope still runs over a "、"-joined list.
_CLAUSE_BREAKS = "，。；！？,.;!?：:"
_SENTENCE_BREAKS = "。！？\n"
_WINDOW = 6

# Fever terms are synonyms for the denial / local checks: a denial of
# "发烧" denies a "发热" quote and vice versa.
FEVER_TERMS = ("发热", "发烧")
FEVER_SYNONYMS: Dict[str, tuple] = {
    "发热": ("发烧",),
    "发烧": ("发热",),
}

# Facts whose values are codes / scores / readings, never symptoms:
# a trajectory sentence, a severity score, a temperature reading.
CODING_FACT_KEYS = frozenset({"trajectory", "severity", "temperature_c"})

# Perception verbs that carry a negation over the symptom that follows
# them ("不觉得呼吸困难" denies 呼吸困难; "没感到胸闷" denies 胸闷).
_PERCEPTION_NEGATIONS = (
    "不觉得",
    "没觉得",
    "没有觉得",
    "不感到",
    "没感到",
    "没有感到",
    "不感觉",
    "没感觉",
    "感觉不到",
    "觉不到",
    "不觉着",
)

# Uncertainty markers that can close a whole recapitulated sentence
# ("……都不清楚" at the sentence tail).
_TAIL_UNCERTAINTY_MARKERS = (
    "都不清楚",
    "都不知道",
    "都不确定",
    "不清楚",
    "不知道",
    "说不清",
    "不确定",
    "没注意",
    "没留意",
    "不记得",
)

# Words immediately before a fever term that mark it as a local skin
# description rather than systemic fever.
_LOCAL_FEVER_MARKERS = ("发红", "红肿", "局部", "患处", "脸颊", "面部", "皮肤")


def _clause_start(text: str, index: int) -> int:
    start = index
    while start > 0 and text[start - 1] not in _CLAUSE_BREAKS:
        start -= 1
    return start


def _occurrence_negated(text: str, index: int) -> bool:
    """Mirror of text_match._occurrence_negated (private there, and
    this module must judge one occurrence at a time)."""
    window = text[max(0, index - _WINDOW) : index]
    cut = max((window.rfind(mark) for mark in _CLAUSE_BREAKS), default=-1)
    if cut >= 0:
        window = window[cut + 1 :]
    if any(cue in window for cue in NEGATION_CUES):
        return True
    if window.endswith(("不是", "不敢", "不")) or window.endswith("没"):
        return True
    segment = text[_clause_start(text, index) : index]
    if "、" in segment:
        head = segment.split("、", 1)[0]
        if any(cue in head for cue in NEGATION_CUES):
            return True
    return False


def _occurrence_uncertain(text: str, index: int, term_len: int) -> bool:
    """Mirror of text_match._occurrence_uncertain, plus the
    sentence-tail rule (N-10) and the perception-negation rule
    (P-03 / T-06), which the shared implementation does not cover."""
    start = _clause_start(text, index)
    before = text[start:index]
    if any(marker in before for marker in UNCERTAINTY_MARKERS):
        return True
    if any(neg in before for neg in _PERCEPTION_NEGATIONS):
        return True
    after = text[index + term_len : index + term_len + _WINDOW]
    if any(marker in after for marker in UNCERTAINTY_MARKERS):
        return True
    # Sentence-tail uncertainty: the term sits in a "、"-joined
    # recapitulation whose sentence ends in an uncertainty marker,
    # with no clause break between the term and the marker.
    sent_start = index
    while sent_start > 0 and text[sent_start - 1] not in _SENTENCE_BREAKS:
        sent_start -= 1
    sent_end = index + term_len
    while sent_end < len(text) and text[sent_end] not in _SENTENCE_BREAKS:
        sent_end += 1
    tail = text[index + term_len : sent_end]
    if tail and "，" not in tail and "；" not in tail and "：" not in tail:
        stripped = tail.strip()
        if ("、" in stripped or len(stripped) <= 8) and any(
            stripped.endswith(marker) for marker in _TAIL_UNCERTAINTY_MARKERS
        ):
            return True
    return False


def _occurrence_is_local_fever(text: str, index: int, quote: str) -> bool:
    if quote not in FEVER_TERMS:
        return False
    left = text[max(0, index - 6) : index]
    return any(marker in left for marker in _LOCAL_FEVER_MARKERS)


def is_affirmed_in_text(text: str, quote: str) -> bool:
    """True when ``quote`` has at least one occurrence in ``text``
    that is affirmed and certain under the shared rules *plus* the
    P4 rules above (perception negation, sentence-tail uncertainty,
    local-fever substrings)."""
    if not text or not quote:
        return False
    start = 0
    while True:
        index = text.find(quote, start)
        if index < 0:
            return False
        if (
            not _occurrence_negated(text, index)
            and not _occurrence_uncertain(text, index, len(quote))
            and not _occurrence_is_local_fever(text, index, quote)
        ):
            return True
        start = index + 1


def _fact_texts(state: Any, key: str) -> List[str]:
    fact = (state.facts or {}).get(key)
    if fact is None:
        return []
    texts = []
    if fact.evidence:
        texts.append(str(fact.evidence))
    if fact.value is not None:
        texts.append(str(fact.value))
    return texts


def quote_denied_by_absent(state: Any, quote: str) -> bool:
    """True when a recorded ABSENT fact's own wording names ``quote``
    (or, for fever terms, its synonym): the denial is on record, so
    the quote cannot support a direction on that symptom."""
    terms = [quote, *FEVER_SYNONYMS.get(quote, ())]
    for fact in (state.facts or {}).values():
        assertion = getattr(fact, "assertion", None)
        if assertion is None:
            continue
        # Assertion is a str Enum; .value is the stable comparison
        # (str() of a mixed-in Enum is "Assertion.ABSENT", not the
        # value), and plain-string assertions compare directly.
        if getattr(assertion, "value", assertion) != "absent":
            continue
        texts = []
        if fact.evidence:
            texts.append(str(fact.evidence))
        if fact.value is not None:
            texts.append(str(fact.value))
        if any(term in text for text in texts for term in terms):
            return True
    return False


def _load_differentials() -> Dict[str, Any]:
    try:
        raw = resources.files("medagent.content").joinpath(
            "differentials.json"
        ).read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def hypothesis_pathway(hypothesis_id: str) -> Optional[str]:
    for pathway in _load_differentials().get("pathways", []):
        for hypothesis in pathway.get("hypotheses", []):
            if hypothesis.get("id") == hypothesis_id:
                return pathway.get("id")
    return None


def chief_pathway_groups(chief_text: str) -> Set[str]:
    """Pathway ids whose chief-complaint terms match the chief
    complaint (negation-aware, same semantics as the proposer)."""
    chief = (chief_text or "").strip()
    groups: Set[str] = set()
    if not chief:
        return groups
    for pathway in _load_differentials().get("pathways", []):
        terms = pathway.get("chief_complaint_contains_any", [])
        if any(contains_unnegated(chief, term) for term in terms):
            groups.add(pathway.get("id"))
    return groups


# Core-symptom anchors for hypotheses whose support is coding-only
# (today only non_cardiac_chest: its support DSL has no contains_any
# terms, so its symptom must come from the patient's own words).
# Derived from the patient label's symptom (胸痛) plus chest-site
# synonyms. 心慌 is deliberately absent: palpitation triggers the
# chest pathway's entry, but it is not the chest-pain symptom this
# direction names.
_TRAJECTORY_ONLY_ANCHORS: Dict[str, tuple] = {
    "non_cardiac_chest": ("胸痛", "胸口", "胸闷", "心口"),
}


def _patient_texts(state: Any) -> List[str]:
    texts: List[str] = []
    for fact in (state.facts or {}).values():
        if fact.evidence:
            texts.append(str(fact.evidence))
        if fact.value is not None:
            texts.append(str(fact.value))
    for item in getattr(state, "transcript", []) or []:
        if isinstance(item, dict) and item.get("role") == "user":
            if item.get("content"):
                texts.append(str(item["content"]))
    return texts


def direction_kept(
    hypothesis: Any,
    group: str,
    valid_quote_refs: List[Any],
    state: Any,
) -> bool:
    """Direction-level keep/drop after the quote gate.

    ``valid_quote_refs`` is a list of ``(ref, quote)`` pairs that
    passed the quote gate. ``hypothesis`` is the proposer's
    Hypothesis (``.evidence_for``, ``.id``)."""
    if not valid_quote_refs:
        return False
    refs = list(getattr(hypothesis, "evidence_for", []) or [])
    # J-07: trajectory / coding facts alone never anchor a direction,
    # unless the patient affirmed the direction's core symptom (the
    # pinned chest-noncardiac probe: chest chief + stable trajectory
    # keeps its direction; an anxiety chief with only 心慌 does not).
    if refs and all(
        getattr(ref, "fact_key", None) in CODING_FACT_KEYS for ref in refs
    ):
        anchors = _TRAJECTORY_ONLY_ANCHORS.get(
            getattr(hypothesis, "id", ""), ()
        )
        texts = _patient_texts(state)
        if not any(
            is_affirmed_in_text(text, anchor)
            for text in texts
            for anchor in anchors
        ):
            return False
    quotes = [quote for _ref, quote in valid_quote_refs]
    # Every valid quote denied on record by an ABSENT fact: the
    # direction conflicts with recorded denials and must not stand,
    # as cannot_exclude or otherwise.
    if all(quote_denied_by_absent(state, quote) for quote in quotes):
        return False
    if group == "rule_out":
        anchored = [
            (ref, quote)
            for ref, quote in valid_quote_refs
            if getattr(ref, "fact_key", None) not in CODING_FACT_KEYS
            and quote not in FEVER_TERMS
        ]
        if not anchored:
            # Fever / coding only: allowed only on the chief's own
            # pathway (same-system rule-out); cross-system it is the
            # U-11 / U-14 shape and is dropped.
            pathway = hypothesis_pathway(getattr(hypothesis, "id", ""))
            chief_text = ""
            chief_fact = (state.facts or {}).get("chief_complaint")
            if chief_fact is not None and chief_fact.value is not None:
                chief_text = str(chief_fact.value)
            if pathway not in chief_pathway_groups(chief_text):
                return False
    return True


def parse_severity_score(state: Any) -> Optional[float]:
    """Recorded severity score, if any, as a float. Only the fact
    value counts (never evidence prose): the fallback restates the
    recorded score, it does not re-derive one."""
    fact = (state.facts or {}).get("severity")
    if fact is None or fact.value is None:
        return None
    value = fact.value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None


def severity_consistent_text(severity_text: str, state: Any) -> str:
    """S-11 consistency fallback (narrative template itself untouched).

    The urgent severity template asserts ``程度偏高``. When a
    severity score *is* recorded and sits below the scale midpoint
    (``< 5`` on the 0–10 score the sheet itself reports), that
    assertion contradicts the fact: fall back to restating the
    recorded score in place of the ``程度偏高`` claim, keeping the
    rest of the sentence (including its same-day assessment wording)
    intact. Scores at/above the midpoint, and unrecorded severity,
    leave the template untouched — this is a wording-consistency
    check, not a disposition or triage threshold, and the disposition
    level is never recomputed here."""
    if not severity_text or "程度偏高" not in severity_text:
        return severity_text
    score = parse_severity_score(state)
    if score is None or score >= 5:
        return severity_text
    if score == int(score):
        score_text = str(int(score))
    else:
        score_text = str(score)
    return severity_text.replace(
        "程度偏高", f"难受程度为 {score_text} 分", 1
    )
