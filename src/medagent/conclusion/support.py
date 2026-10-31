from __future__ import annotations
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from importlib import resources
from typing import Any, Dict, List, Optional
from ..baseline_projection import build_allergy_hint
from ..direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from ..domain import Assertion, CaseState, Phase, RiskLevel
from ..followups import build_follow_up_plan
from ..handoff import LABELS as FACT_LABELS
from ..reasoning import RuleBasedProposer, validate_workspace
from ..text_match import contains_unnegated, find_unnegated_certain
from ..worry import extract_worry_object, is_worry_sentence

from .constants import PRIOR_RANK, _AGAINST_LABEL_OVERRIDES, _ENCODING_QUOTES, _GENERIC_WORD_FRAGMENTS, _OUTLOOK_QUALIFIER_TOKENS, _QUALIFIED_MARKERS

def _is_pure_number(quote: str) -> bool:
    text = quote.strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        return False
    return True


def _valid_support_quote(state: CaseState, ref) -> bool:
    """Batch B citation gate for one evidence_for reference. A quote is
    usable only when: the fact is PRESENT; the quote is not a single
    character (chief_complaint excepted — the pathway-entry vocabulary
    is single-character by design); it is not a pure number or an
    encoded value; it is a substring of the fact's evidence/value or a
    user transcript turn; and that occurrence is affirmed and certain.

    P4: the affirmation test is direction_guards.is_affirmed_in_text,
    which is the shared find_unnegated_certain test *plus* the three
    shapes it misses — perception-verb negation ("也不觉得呼吸困难"
    denying 困难, P-03/T-06), sentence-tail uncertainty over a
    "、"-joined recapitulation ("……都不清楚" closing the list, N-10),
    and local-descriptor substrings ("脸颊发红发热" as systemic fever,
    D-13). A quote whose only occurrences fail that test is inverted
    evidence and is dropped here, before any direction can use it.
    """
    fact = state.facts.get(ref.fact_key)
    if fact is None or fact.assertion != Assertion.PRESENT:
        return False
    quote = ref.quote or ""
    if not quote:
        return False
    if len(quote) < 2 and ref.fact_key != "chief_complaint":
        return False
    if quote in _ENCODING_QUOTES or _is_pure_number(quote):
        return False
    sources: List[str] = []
    if fact.evidence:
        sources.append(fact.evidence)
    if fact.value is not None:
        sources.append(str(fact.value))
    sources.extend(
        item.get("content", "")
        for item in state.transcript
        if item.get("role") == "user" and item.get("content")
    )
    for source in sources:
        if quote in source and is_affirmed_in_text(source, quote):
            return True
    return False


def _certainty(hypothesis, group: str, valid_count: Optional[int] = None) -> str:
    if group == "rule_out":
        return "cannot_exclude"
    count = (
        len(hypothesis.evidence_for) if valid_count is None else valid_count
    )
    if (
        hypothesis.status == "supported"
        and hypothesis.prior_band == "high"
        and count >= 2
    ):
        return "more_likely"
    return "possible"


def _against_note(hypothesis, state: CaseState) -> Optional[str]:
    """「你否认了X，这一点不支持它」for a direction whose first piece of
    counter-evidence is a fact the patient explicitly denied (assertion
    ABSENT). Anything else — a present fact arguing against the direction
    (a low temperature, a worsening trajectory), a superseded fact, an
    unlabelled fact key — yields no note: the sheet only narrates
    denials, it does not paraphrase measurements."""
    if not hypothesis.evidence_against:
        return None
    ref = hypothesis.evidence_against[0]
    fact = state.facts.get(ref.fact_key)
    if fact is None or fact.fact_id != ref.fact_id:
        return None
    if fact.assertion != Assertion.ABSENT:
        return None
    label = _AGAINST_LABEL_OVERRIDES.get(ref.fact_key) or FACT_LABELS.get(
        ref.fact_key
    )
    if not label:
        return None
    return f"你否认了{label}，这一点不支持它"


def _select_directions(accepted, state: CaseState) -> tuple[List[Dict[str, Any]], bool]:
    labels = _patient_labels()
    active = accepted.active()
    rule_outs = [
        h
        for h in active
        if h.category in ("serious_condition", "red_flag_condition")
    ]
    # "unlikely" is the proposer's question-economics downgrade (entered on
    # the chief complaint alone, workup complete, nothing more supporting).
    # For a common condition with verbatim evidence that is still a fair
    # "possible" direction at close; for a serious condition it reads as
    # worked-up-and-unlikely, so serious "unlikely" stays out of D2.
    likelies = [h for h in active if h.category == "common_condition"]
    likelies += [
        h
        for h in accepted.hypotheses.values()
        if h.category == "common_condition"
        and h.status == "unlikely"
        and h.evidence_for
    ]
    # Supported rule-outs outrank merely-active ones, and red-flag linkage
    # breaks ties — a strongly evidenced serious hypothesis must never be
    # crowded out by weakly evidenced ones (febrile-headache case, 2026-10-23).
    rule_outs.sort(
        key=lambda h: (
            0 if h.status == "supported" else 1,
            0 if h.red_flags else 1,
            -PRIOR_RANK.get(h.prior_band, 0),
            -len(h.evidence_for),
        )
    )
    likelies.sort(
        key=lambda h: (-PRIOR_RANK.get(h.prior_band, 0), -len(h.evidence_for))
    )
    # I2: rule-out directions are never dropped by truncation. Common
    # directions fill the remaining slots up to three total.
    chosen = list(rule_outs)
    chosen += likelies[: max(0, 3 - len(chosen))]
    # Abstention (design §5.4, recalibrated in the phase-A report): with a
    # complete workup the DSL's evidence lists are thin by construction, so
    # the operative rule is "nothing selectable" rather than an evidence
    # count the content cannot reach.
    abstained = not chosen
    if abstained:
        return [], True
    directions: List[Dict[str, Any]] = []
    for hypothesis in chosen:
        group = "rule_out" if hypothesis in rule_outs else "likely"
        # Batch B citation gate: only affirmed, certain, verbatim
        # quotes count. A direction left with no valid support is
        # dropped here (and the sheet abstains if that empties the
        # list); certainty is recomputed from the valid count, so a
        # "supported" hypothesis kept alive by one denied quote and one
        # real quote reads "possible", not "more_likely". The
        # against_note is untouched by this gate — it only ever
        # narrates ABSENT facts, so an affirmation check on it would
        # be backwards.
        valid_quote_refs = [
            (ref, ref.quote)
            for ref in hypothesis.evidence_for
            if _valid_support_quote(state, ref)
        ]
        valid_quotes = [quote for _ref, quote in valid_quote_refs]
        if not valid_quotes:
            continue
        # P4 direction anchoring (④-6): a direction that survives
        # the quote gate can still be unanchored — trajectory/coding
        # wording as its only support (J-07), fever/coding-only
        # cross-system rule-outs (U-11/U-14), or every quote denied
        # on record by an ABSENT fact. Such a direction is dropped
        # here, never kept to preserve a direction count; dropping
        # also removes its label from narrative reasoning and the
        # department reason, which are both rendered from this list.
        if not direction_kept(hypothesis, group, valid_quote_refs, state):
            continue
        against_note = _against_note(hypothesis, state)
        directions.append(
            {
                "hypothesis_id": hypothesis.id,
                "patient_label": labels.get(hypothesis.id),
                "group": group,
                "certainty": _certainty(hypothesis, group, len(valid_quotes)),
                "support_quotes": valid_quotes[:2],
                "against_note": against_note,
            }
        )
    # A direction without a patient-facing label may not be shown to the
    # patient (design §5.3.5); it still counted toward selection above,
    # but the entry itself is dropped here rather than leaking a null
    # label to the renderer. If that empties the list, the sheet
    # abstains instead of presenting an empty directions block.
    directions = [d for d in directions if d["patient_label"]]
    if not directions:
        return [], True
    return directions, False


def _strip_qualifiers(item: str) -> str:
    core = str(item or "")
    for marker in ("新出现或加重", "新出现", "突然出现", "明显加重", "迅速加重", "或加重", "加重"):
        core = core.replace(marker, "")
    return core.strip(" ，、。；")


def _denial_remainders(text: str) -> List[str]:
    """Text after each negation marker, up to the sentence end.

    Shared by the fact scan and the transcript scan in
    _denied_texts so both use exactly one marker rule: an
    enumeration under one marker ("没有A、B") keeps both items
    (splitting on 、 would orphan B); a marker counts only at a
    clause boundary (start or after ，/、/；/。/space, optionally
    led by 也/还/并/且), so "说不清" (unknown, no marker) never
    qualifies, and a bare "无" in 无法/无论 is excluded.
    Qualifying on a loose match stays fail-safe here — it only
    narrows an alert to a new occurrence, never removes it.
    """
    remainders: List[str] = []
    for match in re.finditer(
        r"(?:^|[，、；。\s])(?:也|还|并|且)?(没有|否认|无)([^。；]+)", text
    ):
        marker, remainder = match.group(1), match.group(2).strip()
        if marker == "无" and remainder[:1] in ("法", "论"):
            continue
        if len(remainder) >= 2:
            remainders.append(remainder)
    return remainders


def _denied_texts(state: CaseState) -> List[str]:
    texts: List[str] = []
    for fact in state.facts.values():
        if fact.assertion != Assertion.ABSENT:
            continue
        if fact.value is not None:
            texts.append(str(fact.value))
        if fact.evidence:
            texts.append(str(fact.evidence))
    # N-12/T-04 live shape: in real runs the denial is usually NOT
    # its own ABSENT fact. The grouped emergency_screen ABSENT fact
    # carries no value or evidence, and the denial survives only
    # inside a PRESENT fact's evidence ("没有肢体无力、说话不清").
    for fact in state.facts.values():
        if fact.assertion == Assertion.ABSENT:
            continue
        for raw in (fact.value, fact.evidence):
            if raw is None:
                continue
            texts.extend(_denial_remainders(str(raw)))
    # N-12 round-4 shape: the denial survives in NEITHER fact form.
    # The recording layer books only the affirmative part of an
    # answer ("没有肢体无力说话不清" never reaches any fact's
    # value/evidence), so the denial lives only in the raw patient
    # answer text. Both engines write user turns to
    # state.transcript (consultation.py, agent.py), so scan those
    # turns with the same marker rule. Assistant turns are never
    # scanned: the system's own wording must not narrow its own
    # safety net. The denied set still only feeds
    # _adjust_assertion_wording's "新出现" narrowing (its sole
    # caller), so this cannot touch ordering, disposition, or
    # direction evidence.
    for turn in state.transcript or []:
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        content = turn.get("content")
        if content:
            texts.extend(_denial_remainders(str(content)))
    return texts


def _adjust_assertion_wording(sheet: Dict[str, Any], state: CaseState) -> Dict[str, Any]:
    """T-04: rewrite outlook / return_now_if by assertion state.

    * Outlook: a symptom already PRESENT must not be listed under
      “新出现” — it is removed from that enumeration (other tokens
      stay); if the enumeration empties, the clause becomes an
      “原有症状有没有加重” check.
    * return_now_if: an item for a symptom the patient explicitly
      denied is kept only with a “新出现” qualifier appended
      (suffix style, matching the existing “肢体无力新出现” items).
      Denial matching is verbatim first, then a shared 2-character
      fragment with a denied text — qualifying on a loose match is
      fail-safe here (it narrows the alert to a new occurrence
      without removing it); items already qualified are untouched.
    """
    if not isinstance(sheet, dict):
        return sheet
    present_texts = ""
    for fact in state.facts.values():
        if fact.assertion != Assertion.PRESENT:
            continue
        if fact.value is not None:
            present_texts += str(fact.value)
        if fact.evidence:
            present_texts += str(fact.evidence)
    outlook = sheet.get("outlook")
    if isinstance(outlook, str) and "新出现" in outlook:
        def _rewrite(match: "re.Match[str]") -> str:
            prefix = match.group(1) or ""
            enumeration = match.group(2) or ""
            tokens = [
                token.strip()
                for token in re.split(r"[、，或]", enumeration)
                if token.strip()
            ]
            kept = [token for token in tokens if token not in present_texts]
            if kept:
                return f"{prefix}新出现{'、'.join(kept)}"
            # Empty enumeration: the "原有症状" fallback is only
            # sound when at least one removed token was a symptom.
            # When every removed token is a qualifier (oral "或加重"),
            # nothing was shown to pre-exist — leave the course
            # verbatim instead of asserting "原有症状".
            removed = [token for token in tokens if token in present_texts]
            if not any(
                token not in _OUTLOOK_QUALIFIER_TOKENS for token in removed
            ):
                return match.group(0)
            if prefix:
                return "原有症状有没有加重"
            return "原有症状加重"

        sheet["outlook"] = re.sub(
            r"(有没有)?新出现([^，。；]+)", _rewrite, outlook, count=1
        )
    denied = _denied_texts(state)
    if denied and isinstance(sheet.get("return_now_if"), list):
        adjusted: List[str] = []
        for item in sheet["return_now_if"]:
            text = str(item)
            if any(marker in text for marker in _QUALIFIED_MARKERS):
                adjusted.append(text)
                continue
            core = _strip_qualifiers(text)
            denied_match = False
            if core and any(core in denied_text for denied_text in denied):
                denied_match = True
            elif len(core) >= 2:
                for start in range(len(core) - 1):
                    fragment = core[start : start + 2]
                    if fragment in _GENERIC_WORD_FRAGMENTS:
                        continue
                    if any(fragment in denied_text for denied_text in denied):
                        denied_match = True
                        break
            adjusted.append(f"{text}新出现" if denied_match else text)
        sheet["return_now_if"] = adjusted
    return sheet


def _onset_is_chronic_gradual(state: CaseState) -> bool:
    """E-04/U-09: a gradual onset at week/month/year scale.

    Narrow on purpose: a gradual marker （逐渐/慢慢/渐渐） plus a
    week-or-longer duration unit （周/月/年/长期）, or an explicit
    “慢性”. A sudden onset, or a gradual onset measured only in days,
    does not trigger this — those keep their existing outlook.
    """
    fact = state.facts.get("onset")
    if fact is None or fact.assertion != Assertion.PRESENT:
        return False
    text = ""
    if fact.value is not None:
        text += str(fact.value)
    if fact.evidence:
        text += str(fact.evidence)
    if "慢性" in text:
        return True
    gradual = any(marker in text for marker in ("逐渐", "慢慢", "渐渐"))
    long_unit = any(unit in text for unit in ("周", "月", "年", "长期"))
    return gradual and long_unit


def _is_infant_case(state: CaseState) -> bool:
    """P-17: subject is a child who cannot be presumed to report pain.

    Primary gate: consult_for == child with a recorded age <= 3
    years (age_years is stored in years; < 2 displays as months).
    Secondary gate, for cases where the age was not captured: the
    subject is a child and a recorded fact says pain cannot be
    described （说不清 + 疼/痛 in the same fact text, e.g. the P-17
    “宝宝还小，说不清肚子疼” evidence). Older children keep the
    existing adult wording — this never rewrites a self-reporting
    child's sheet."""
    subject = state.facts.get("consult_for")
    if (
        subject is None
        or subject.assertion != Assertion.PRESENT
        or str(subject.value) != "child"
    ):
        return False
    age = state.facts.get("age_years")
    if (
        age is not None
        and age.assertion == Assertion.PRESENT
        and isinstance(age.value, (int, float))
        and not isinstance(age.value, bool)
        and age.value <= 3
    ):
        return True
    for fact in state.facts.values():
        text = ""
        if fact.value is not None:
            text += str(fact.value)
        if fact.evidence:
            text += str(fact.evidence)
        if "说不清" in text and ("疼" in text or "痛" in text):
            return True
    return False


def _is_undetermined_unknown_screen(state: CaseState) -> bool:
    """G-12 gate: risk UNDETERMINED *and* the emergency screen itself
    answered unknown. A missing screen is not an unknown screen."""
    if state.risk is None or state.risk.level != RiskLevel.UNDETERMINED:
        return False
    screen = state.facts.get("emergency_screen")
    return screen is not None and screen.assertion == Assertion.UNKNOWN


def _hypothesis_support_terms(hypothesis_id: str) -> set:
    """Every contains_any term of one hypothesis's support conditions
    in differentials.json (its DSL vocabulary, across conditions)."""
    terms = set()
    for pathway in _load_json("differentials.json").get("pathways", []):
        for hypothesis in pathway.get("hypotheses", []):
            if hypothesis.get("id") == hypothesis_id:
                for condition in hypothesis.get("support", []):
                    terms.update(condition.get("contains_any") or [])
    return terms


def _sheet_evidence_count(accepted, state: CaseState, direction) -> int:
    """Distinct valid evidence tokens backing one direction, for
    sheet_group only. This is batch-B-rework option (b)-②: count
    distinct tokens, not proposer refs.

    The proposer records one ref per fired condition, so a
    single-condition hypothesis whose detail fact genuinely names
    several of its symptoms (cystitis: one urinary_details condition
    matching 尿频 / 尿急 / 尿痛) arrived at sheet_group with one
    quote and could never reach the old ">= 2 support_quotes" bar —
    a structural defect in batch B's own rule, not a corpus gap.
    The rejected alternative (b)-①, one ref per matched term, was
    not taken: it would change evidence_for lengths, flipping
    proposer status (active -> supported) and with it the certainty
    recomputation, the abstention logic, and the planner's workspace
    signature — a reasoning change smuggled in as a display fix.

    The count here is the union of the direction's valid quotes with
    the hypothesis's DSL terms attested — affirmed and certain under
    direction_guards.is_affirmed_in_text (the shared test plus the
    P4 perception / sentence-tail / local-descriptor rules) — in the
    value/evidence of the facts its evidence refs actually point at. A single token can
    never drive sheet_group, so the K-12 shape (one respiratory quote
    on a urinary chief complaint) still falls back to generic; two
    distinct tokens drive their group whether they arrived as two
    refs or as two terms inside one referenced fact. Certainty is
    untouched: it still recomputes from the valid-quote count in
    _select_directions, never from this count.
    """
    valid = set(direction.get("support_quotes") or [])
    hypothesis = None
    if accepted is not None:
        hypothesis = accepted.hypotheses.get(direction.get("hypothesis_id"))
    if hypothesis is None:
        return len(valid)
    terms = _hypothesis_support_terms(direction.get("hypothesis_id"))
    if not terms:
        return len(valid)
    attested = set()
    for ref in hypothesis.evidence_for:
        fact = state.facts.get(ref.fact_key)
        if fact is None or fact.assertion != Assertion.PRESENT:
            continue
        texts = []
        if isinstance(fact.value, str):
            texts.append(fact.value)
        if fact.evidence:
            texts.append(fact.evidence)
        for text in texts:
            for term in terms:
                if is_affirmed_in_text(text, term):
                    attested.add(term)
    return len(valid | attested)

from .sheet import _load_json, _patient_labels
