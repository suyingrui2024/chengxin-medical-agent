"""Consultation conclusion sheet composer.

Implements docs/问诊结论单设计文档.md (v1.1): at close time, derive the
patient-facing conclusion sheet — disposition, differential directions,
information gaps, self-care, safety net, outlook — deterministically from
the concluded case. No model calls; every clinical sentence traces to the
triage outcome, a validated reasoning workspace, or reviewed content.

Invariants (design §4): verbatim-quote evidence only (I1), rule-out
directions are never truncated away (I2), composition failure must never
block closing (I3, enforced by the caller), regeneration on re-close
increments the sheet revision (I4).
"""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from importlib import resources
from typing import Any, Dict, List, Optional

from .baseline_projection import build_allergy_hint
from .direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from .domain import Assertion, CaseState, Phase, RiskLevel
from .followups import build_follow_up_plan
from .handoff import LABELS as FACT_LABELS
from .reasoning import RuleBasedProposer, validate_workspace
from .text_match import contains_unnegated, find_unnegated_certain
from .worry import extract_worry_object, is_worry_sentence

# Batch B citation validation uses text_match's shared
# ``find_unnegated_certain`` via direction_guards.is_affirmed_in_text
# (shared test plus the P4 perception / sentence-tail / local rules).
# An earlier
# revision carried a local fallback (``_quote_affirmed_certain_local``)
# for a time when those names were not in text_match yet; it was
# deleted in the batch-B rework because its semantics diverged from
# the shared implementation in three ways: it treated "、" as a clause
# break, so later items of a denial list ("没有气短、喘" -> "喘")
# read as affirmed; it missed the adjacent negations ("不是" /
# "不敢" / bare "不"); and it rejected a quote for any uncertainty
# marker anywhere in its clause, where the shared implementation
# checks markers before the term in the same clause and within a
# short window after it. Importing ``is_refusal_text`` from
# consultation is deliberately NOT done here: consultation imports
# bridging, which imports this module's BANNED_PHRASES, so a top-level
# consultation import would be circular; the local refusal check in
# ``_gaps`` covers the same evidence phrases the task prescribes.

# Same list as consultation._summary_payload's return_now_if; a drift-guard
# test asserts the two never diverge. It is the composer's fallback: when
# the case has an active pathway, the pathway-specific list from
# self_care.json (return_now_if.by_pathway) wins; then the summary
# payload's list; then this constant.
RETURN_NOW_IF = [
    "意识异常或抽搐",
    "严重呼吸困难或嘴唇发紫",
    "持续压榨样胸痛",
    "大量或无法控制的出血",
]

# Design §6.3: hitting any of these in a rendered clinical sentence fails
# composition (the caller then falls back to the plain summary view).
BANNED_PHRASES = (
    "确诊",
    "可以排除",
    "没事",
    "不用担心",
    "保证",
    "肯定是",
    "一定是",
    "必须吃药",
)

CERTAINTY_TEMPLATES = {
    "more_likely": "更像是 {label}",
    "possible": "可能是 {label}",
    "cannot_exclude": "目前还不能排除 {label}，这也是建议就诊的原因",
}

ABSTAIN_TEXT = "目前的信息还不足以给出可能的方向——这也是建议你留意变化、必要时线下评估的原因之一"

# Hypothesis id -> self-care pathway group (mirrors differentials.json
# pathway membership; the evaluation suite exercises every id).
PATHWAY_GROUPS = {
    "uri": "respiratory",
    "lower_respiratory": "respiratory",
    "asthma_exacerbation": "respiratory",
    "influenza_like": "respiratory",
    "acs_possible": "chest",
    "non_cardiac_chest": "chest",
    "appendicitis": "abdominal",
    "gastroenteritis": "abdominal",
    "gi_bleed": "abdominal",
    "functional_dyspepsia": "abdominal",
    "meningitis_possible": "headache",
    "thunderclap_headache": "headache",
    "migraine_tension": "headache",
    "bppv": "dizziness",
    "posterior_stroke": "dizziness",
    "orthostatic": "dizziness",
    "mechanical_back": "back",
    "radiculopathy": "back",
    "cauda_serious": "back",
    "cystitis": "urinary",
    "pyelonephritis": "urinary",
    "urinary_stone": "urinary",
    "urticaria": "skin",
    "dermatitis": "skin",
    "skin_infection": "skin",
}

PRIOR_RANK = {"high": 3, "medium": 2, "low": 1}

GAP_IMPACTS = {
    "temperature_c": "体温读数会影响对感染程度的判断",
    "severity": "难受程度会影响对紧急程度的判断",
    "trajectory": "变化趋势会影响对病情走向的判断",
    "resp_breathing": "呼吸情况会影响对严重程度的判断",
    "gi_details": "腹痛的部位和性质会影响对腹部急症的判断",
    "head_details": "头痛的发作方式和伴随表现会影响对颅内急症的判断",
    "dizzy_details": "头晕的类型和伴随表现会影响对脑部供血问题的判断",
    "back_details": "腰痛是否影响腿部和大小便会影响对腰椎严重问题的判断",
    "urinary_details": "排尿表现和尿色会影响对尿路感染部位的判断",
    "skin_details": "皮疹的形态和诱因会影响对过敏与感染的判断",
}
DEFAULT_GAP_IMPACT = "这项信息会影响判断"

# Fact keys whose unknown state makes a gap "significant" for the
# disposition mapping (design §5.2), beyond discriminating-question hits.
MEASUREMENT_FACTS = {"temperature_c"}

_cache: Dict[str, Any] = {}


def sheet_mode() -> str:
    """off | shadow | on — env override, default on (design §8.3/§9A)."""
    mode = os.environ.get("MEDAGENT_CONCLUSION_SHEET", "on").strip().lower()
    return mode if mode in ("off", "shadow", "on") else "on"


def directions_visible() -> bool:
    """Phase A ships directions in shadow; phase B flips this after the
    evaluation gate and clinician review (design §9).

    Rendering single source (modular-refactor design §4.4, frozen):
    the composer is the ONLY place that computes visibility. The sheet
    always carries the full ``directions`` list plus this bool; every
    renderer (conclusion card, handoff endpoint, print module) consumes
    the bool and never recomputes it, never falls back to "a direction
    list is present, therefore visible", and treats a missing flag as
    invisible (fail-closed). ``narrative.reasoning`` shares this gate
    because each reasoning line quotes a direction label; ``worry`` /
    ``goal_response`` are the patient's own words and are NOT gated.
    """
    return os.environ.get("MEDAGENT_CONCLUSION_DIRECTIONS", "off").strip().lower() == "on"


def department_visible() -> bool:
    """Department hints ship in shadow too (benchmark design §3.4/D2):
    the composer always fills the hint from reviewed content, and this
    flag tells the renderer whether it may be shown before the C12
    clinical sign-off.

    Rendering single source (modular-refactor design §4.4, frozen):
    same contract as :func:`directions_visible` — the composer is the
    ONLY place that computes this bool, ``disposition.department_hint``
    is always computed, and renderers show it only when this flag is
    true (missing flag = invisible, fail-closed).
    """
    return os.environ.get("MEDAGENT_CONCLUSION_DEPARTMENT", "off").strip().lower() == "on"


def _load_json(name: str) -> Dict[str, Any]:
    if name not in _cache:
        raw = (
            resources.files("medagent.content")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
        _cache[name] = json.loads(raw)
    return _cache[name]


def _load_json_optional(name: str) -> Dict[str, Any]:
    """_load_json for assets that may legitimately be absent (narrative
    templates, department map): a missing or corrupt file degrades the
    feature to off instead of failing composition (fail-open, matching
    the composer's I3 style)."""
    try:
        data = _load_json(name)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _patient_labels() -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for pathway in _load_json("differentials.json").get("pathways", []):
        for hypothesis in pathway.get("hypotheses", []):
            if hypothesis.get("patient_label"):
                labels[hypothesis["id"]] = hypothesis["patient_label"]
    return labels


def _self_care_group(group_key: str, tier: str) -> Optional[Dict[str, Any]]:
    groups = _load_json("self_care.json").get("groups", [])
    for wanted in (group_key, "generic"):
        for group in groups:
            if (
                group.get("match") == wanted
                and group.get("tier") == tier
                and group.get("reviewed_by")
            ):
                return group
    return None


# Detail fact key -> pathway group, for the safety-net fallback below.
DETAIL_FACT_GROUPS = {
    "resp_cough_details": "respiratory",
    "resp_breathing": "respiratory",
    "gi_details": "abdominal",
    "head_details": "headache",
    "dizzy_details": "dizziness",
    "back_details": "back",
    "urinary_details": "urinary",
    "skin_details": "skin",
}


def _explored_pathway_group(state: CaseState) -> Optional[str]:
    """Pathway group of the first detail fact actually collected.

    When the sheet abstains on directions there is no hypothesis to key
    the safety net on, but the consultation still explored a pathway —
    its warning signs are the relevant ones (live-eval run3 case3: a
    fatigue + abdominal case with empty directions was spoken the
    generic neuro warnings). Fact insertion order follows collection
    order, so the primary pathway's detail field normally comes first;
    later screening probes of other pathways do not override it.
    """
    for key in state.facts:
        group = DETAIL_FACT_GROUPS.get(key)
        if group is not None:
            return group
    return None


def _pathway_return_now_if(group_key: str) -> List[str]:
    """Pathway-specific safety-net list from self_care.json.

    Keyed by the same pathway group keys as PATHWAY_GROUPS / the
    self-care groups. Like the groups themselves, the data counts as
    absent unless it carries review metadata (content note convention).
    """
    data = _load_json("self_care.json").get("return_now_if") or {}
    if not data.get("reviewed_by"):
        return []
    items = (data.get("by_pathway") or {}).get(group_key) or []
    return [str(item) for item in items if str(item).strip()]


def chief_matches_pathway(chief_complaint: str) -> bool:
    """Whether a chief complaint triggers any differential pathway.

    Same matching semantics as the reasoning proposer (negation-aware
    substring match against differentials.json pathway terms). Used to
    decide whether a direction-less sheet is genuinely off-pathway:
    a chief that triggers no pathway at all (sudden flashes/floaters,
    sudden single-ear deafness, knee pain, gum bleeding once the bare
    「红肿」 skin term was narrowed) must not borrow the nearest
    probed pathway's language. A chief that does trigger a pathway
    keeps the existing explored-pathway safety net (pinned by the
    run3-case3 test), even when directions abstain.
    """
    chief = (chief_complaint or "").strip()
    if not chief:
        return False
    for pathway in _load_json("differentials.json").get("pathways", []):
        terms = pathway.get("chief_complaint_contains_any", [])
        if any(contains_unnegated(chief, term) for term in terms):
            return True
    return False


def apply_neutral_fallback(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Neutral off-pathway rendering of a composed conclusion sheet.

    Controller-callable (batch A, W2): pass any ``compose_conclusion``
    sheet; returns a copy whose narrative, self-care, outlook and
    safety net no longer borrow a nearest-pathway template — the
    「泌尿不适」/「皮肤不适」/ skin-takeover shapes seen in round 1
    (endocrine polyuria/polydipsia, gum bleeding, eyelid lump).
    Neutral content: narrative ``neutral`` block from
    conclusion_narrative.json, the reviewed ``generic`` self-care
    group from self_care.json (no duplicate neutral group is created,
    so the two cannot drift), and the generic four-item safety net.

    Deliberately untouched: disposition level/window/text, directions
    and their visibility flags, department hint and its visibility,
    goal_line, and ``emergency_explanation`` (a separate function, not
    a sheet field). Urgent-tier framing/severity are triage wording,
    not pathway borrowing, so only self_care/routine_visit narrative
    lines are replaced. Idempotent: applying it to an already-neutral
    sheet returns an equal sheet (modulo the marker key). A sheet
    without a narrative keeps ``narrative=None`` (fail-open, same as
    the composer when the narrative asset is absent). The result still
    has to pass the composer's banned-phrase lint when it goes through
    ``compose_conclusion``; direct callers rendering the sheet should
    run the same check.
    """
    if not isinstance(sheet, dict):
        return sheet
    neutral = deepcopy(sheet)
    tier = "urgent" if neutral.get("tier") == "urgent" else "routine"
    templates = _narrative_templates().get("neutral") or {}
    narrative = neutral.get("narrative")
    level = (neutral.get("disposition") or {}).get("level")
    if isinstance(narrative, dict) and templates and level in ("self_care", "routine_visit"):
        framing = (templates.get("framing") or {}).get(level)
        if framing:
            narrative["framing"] = framing
        severity = (templates.get("severity") or {}).get(level)
        if severity:
            narrative["severity"] = severity
    care = _self_care_group("generic", tier) or {}
    if care:
        previous_care = neutral.get("self_care") or {}
        neutral["self_care"] = {
            "do": list(care.get("do", [])),
            "avoid": list(care.get("avoid", [])),
            "measure": list(care.get("measure", [])),
            **(
                {"allergy_hint": previous_care["allergy_hint"]}
                if previous_care.get("allergy_hint")
                else {}
            ),
        }
        if care.get("course"):
            neutral["outlook"] = care["course"]
    neutral["return_now_if"] = list(RETURN_NOW_IF)
    neutral["neutral_fallback"] = True
    return neutral


# Batch B: a support quote is only usable when it is affirmed and
# certain patient wording — not a denial, not an unknown, not a
# truncated character, not a score, not an encoded value. The
# affirmed-and-certain test is direction_guards.is_affirmed_in_text
# (the shared text_match.find_unnegated_certain test plus the P4
# perception / sentence-tail / local-descriptor rules); there is
# deliberately no second local copy of its negation/uncertainty
# rules in this module.
# Encoded fact values that must never pose as patient quotes. This
# mirrors consultation.VALUE_LABELS keys (and reasoning._ENCODING_VALUES);
# consultation itself cannot be imported here (circular via bridging,
# see the import note above).
_ENCODING_QUOTES = frozenset(
    {
        "worsening",
        "improving",
        "stable",
        "unknown",
        "present",
        "absent",
        "self",
        "child",
        "other_adult",
    }
)


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


# Patient-facing wording for a denied fact cited as counter-evidence
# (benchmark design §3.5). Labels come from the handoff projection's
# table so the two patient surfaces never drift; resp_breathing keeps
# its bespoke symptom wording rather than the generic field label.
_AGAINST_LABEL_OVERRIDES = {"resp_breathing": "呼吸困难等表现"}


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


# Batch B gaps: a gap is either declined (the patient refused the
# field) or asked_unknown (asked, answered "unclear"). The summary's
# declined_details already carry consultation's full refusal test; the
# local check below is the prescribed fallback for evidence that
# reaches the composer without that list — it must stay local because
# importing consultation here would be circular (see the import note).
_REFUSAL_EVIDENCE_MARKERS = ("不想说", "不方便说", "拒绝提供")


def _is_refusal_evidence(text: str) -> bool:
    return bool(text) and any(marker in text for marker in _REFUSAL_EVIDENCE_MARKERS)


def _gap_label(detail: Dict[str, Any], key: str) -> str:
    """Label fallback chain: the summary detail's label, then the
    handoff label table, then the raw key. A detail label that IS the
    raw key (unlocalized, e.g. "sex") is treated as absent so the
    table's localized label wins — raw keys must never reach the
    patient-visible layer."""
    label = detail.get("label")
    if label and label != key:
        return str(label)
    return FACT_LABELS.get(key, key)


def _gaps(state: CaseState, summary, accepted) -> List[Dict[str, Any]]:
    details = (summary or {}).get("unknown_details") or []
    declined_keys = {
        detail.get("key")
        for detail in (summary or {}).get("declined_details") or []
        if detail.get("key")
    }
    discriminating_keys = set()
    for hypothesis in accepted.active():
        discriminating_keys.update(hypothesis.discriminating_questions)
    # discriminating_questions hold question ids; the summary's unknown
    # details carry fact keys — match on fact key membership via question
    # ids is not possible here, so measurement facts and any unknown that
    # shares a key with a discriminating question id both count.
    scored = []
    for detail in details:
        key = detail.get("key")
        if not key:
            continue
        score = 0
        if key in MEASUREMENT_FACTS:
            score += 2
        if key in discriminating_keys:
            score += 2
        fact = state.facts.get(key)
        declined = key in declined_keys or _is_refusal_evidence(
            fact.evidence if fact is not None else ""
        )
        scored.append(
            (
                score,
                key,
                _gap_label(detail, key),
                "declined" if declined else "asked_unknown",
            )
        )
    scored.sort(key=lambda item: -item[0])
    return [
        {
            "fact_key": key,
            "label": label,
            "impact": GAP_IMPACTS.get(key, DEFAULT_GAP_IMPACT),
            "significant": score >= 2,
            "state": gap_state,
        }
        for score, key, label, gap_state in scored[:3]
    ]


def format_window_text(disposition, window_hours=None) -> str:
    """Single-source window/disposition sentence (batch C, G-02/U-02).

    The conclusion sheet and the spoken close line must never carry
    two competing meanings for the same window: the sheet's
    disposition text is generated through this helper, and the close
    layer (which owns ``spoken_close_line``) should call this helper
    with the sheet's disposition dict instead of hard-coding its own
    window sentence. One disposition instance therefore yields one
    window sentence in both surfaces.

    ``disposition`` may be a level string (``"routine_visit"``) or a
    disposition dict (``level`` / ``window_hours`` / ``text``, plus
    the optional generator keys ``window_kind`` and
    ``rule_out_label``). A dict that already carries ``text`` is
    canonicalised from that text, so a composed sheet round-trips
    to its own sentence:

    * ``window_kind == "after_if_not_improved"`` (or a text containing
      “后还没有好转”) -> “可以在家先观察；把下方缺的信息补上，
      如果 {window} 小时后还没有好转，请到线下医疗机构就诊。”
      This is the significant-gap upgrade variant; it is NOT
      rewritten into a “within” sentence, and no new threshold is
      introduced — the sheet's own variant is preserved verbatim.
    * any other ``routine_visit`` -> “请在 {window} 小时内…”
      (with “排除 {label}” when a rule-out label is present).
    * ``urgent_today`` -> today sentence; ``self_care`` -> the
      sheet's observe sentence. Unknown levels fall back to the
      dict's own text, or "" when there is none.
    """
    if isinstance(disposition, dict):
        level = str(disposition.get("level") or "")
        window = window_hours if window_hours is not None else disposition.get("window_hours")
        text = str(disposition.get("text") or "")
        rule_out_label = disposition.get("rule_out_label")
        window_kind = disposition.get("window_kind")
    elif isinstance(disposition, str):
        level = disposition
        window = window_hours
        text = ""
        rule_out_label = None
        window_kind = None
    else:
        return ""
    if level == "urgent_today":
        return "建议今天内到线下医疗机构当面评估，不要在家观察等待。"
    if level == "self_care":
        return "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。"
    if level == "routine_visit":
        if window_kind == "after_if_not_improved" or "后还没有好转" in text:
            if window is None:
                return "可以在家先观察；把下方缺的信息补上，如果还没有好转，请到线下医疗机构就诊。"
            return (
                "可以在家先观察；把下方缺的信息补上，"
                f"如果 {window} 小时后还没有好转，请到线下医疗机构就诊。"
            )
        if rule_out_label is None and "排除" in text:
            match = re.search(r"排除(.+?)。", text)
            if match and match.group(1).strip():
                rule_out_label = match.group(1).strip()
        if window is None:
            return "可以在家先观察；请尽快到线下医疗机构就诊评估。"
        if rule_out_label:
            return (
                f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊，"
                f"排除 {rule_out_label}。"
            )
        return f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
    if text:
        return text if text.endswith(("。", "！", "？")) else text + "。"
    return ""


def _disposition(state, tier, directions, due_hours, show_directions):
    if tier == "urgent":
        return {
            "level": "urgent_today",
            "window_hours": due_hours or 24,
            "text": format_window_text("urgent_today"),
            "department_hint": None,
        }
    rule_outs = [d for d in directions if d["group"] == "rule_out"]
    if rule_outs:
        window = due_hours or 48
        top = rule_outs[0]
        label = top.get("patient_label") if show_directions else None
        text = format_window_text(
            {
                "level": "routine_visit",
                "window_hours": window,
                "rule_out_label": label,
            },
            window,
        )
        return {
            "level": "routine_visit",
            "window_hours": window,
            "text": text,
            "department_hint": None,
        }
    return {
        "level": "self_care",
        "window_hours": due_hours,
        "text": format_window_text("self_care"),
        "department_hint": None,
    }


# Batch C assertion-state wording (T-04): outlook and safety-net text
# must agree with the recorded assertion state. This is wording logic
# only — it never decides which symptoms belong in the safety net
# (no new clinical mapping is created here).
_QUALIFIED_MARKERS = ("新出现", "突然出现", "明显加重", "迅速加重", "加重")
_GENERIC_WORD_FRAGMENTS = {"症状", "出现", "感觉", "不适", "情况", "问题"}


def _strip_qualifiers(item: str) -> str:
    core = str(item or "")
    for marker in ("新出现或加重", "新出现", "突然出现", "明显加重", "迅速加重", "或加重", "加重"):
        core = core.replace(marker, "")
    return core.strip(" ，、。；")


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
    # Take the text after each negation marker up to the sentence
    # end, so an enumeration under one marker ("没有A、B") keeps
    # both items: splitting on 、 would orphan B. A marker counts
    # only at a clause boundary (start or after ，/、/；/。/space,
    # optionally led by 也/还/并/且), so "说不清" (unknown, no
    # marker) never qualifies, and a bare "无" in 无法/无论 is
    # excluded. Qualifying on a loose match stays fail-safe here
    # — it only narrows an alert to a new occurrence, never
    # removes it.
    for fact in state.facts.values():
        if fact.assertion == Assertion.ABSENT:
            continue
        for raw in (fact.value, fact.evidence):
            if raw is None:
                continue
            text = str(raw)
            for match in re.finditer(
                r"(?:^|[，、；。\s])(?:也|还|并|且)?(没有|否认|无)([^。；]+)", text
            ):
                marker, remainder = match.group(1), match.group(2).strip()
                if marker == "无" and remainder[:1] in ("法", "论"):
                    continue
                if len(remainder) >= 2:
                    texts.append(remainder)
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


_SHORT_TERM_OUTLOOK_MARKERS = (
    "数天内",
    "几天内",
    "1–2 天内",
    "3–5 天",
    "数天",
    # E-09 worsening class (self_care.json course grep, 2026-10-26):
    # respiratory_urgent "数小时内加重" is the E-09 case itself;
    # abdominal_urgent "以小时计" is the other hour-scale template;
    # generic_urgent / headache_urgent / dizziness_urgent
    # "短时间内变化/加重" is the same short-term urgency class.
    # Week-scale templates ("1–2 周内", "一两周内") are NOT added:
    # they are consistent with a chronic course.
    "数小时内",
    "以小时计",
    "短时间内",
)
_CHRONIC_OUTLOOK = (
    "这类不适已持续一段时间，观察期间重点看整体状态是好转还是加重，"
    "出现下方警示信号请及时就医。"
)


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


def _adjust_chronic_outlook(sheet: Dict[str, Any], state: CaseState) -> Dict[str, Any]:
    """E-04/U-09/E-09: a chronic gradual course must not be paired
    with a short-term template — relief class (“数天内缓解”) or
    worsening class (“数小时内加重”, “以小时计”, “短时间内”).
    Only the time claim is replaced — by a duration-acknowledging
    observation sentence with no relief-time prognosis — and only
    when the current outlook actually carries such a claim;
    outlooks without a time claim are left untouched."""
    if not isinstance(sheet, dict):
        return sheet
    outlook = sheet.get("outlook")
    if (
        isinstance(outlook, str)
        and outlook
        and _onset_is_chronic_gradual(state)
        and any(marker in outlook for marker in _SHORT_TERM_OUTLOOK_MARKERS)
    ):
        sheet["outlook"] = _CHRONIC_OUTLOOK
    return sheet


_INFANT_OBSERVABLE_DO = "把哭闹、拒食、精神、尿量和排便性状的变化记下来，就诊时告诉医生"
_INFANT_OBSERVABLE_MEASURE = "记录哭闹、精神、小便和大便性状的变化"
_INFANT_WORSENING = "哭闹明显加重、精神变差或尿量明显减少"


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


def _apply_infant_observable(sheet: Dict[str, Any], state: CaseState) -> Dict[str, Any]:
    """P-17 conclusion side: for an infant case, pain-reporting
    self-care / warning items are replaced by caregiver-observable
    ones (crying, refusal to eat, alertness, urine output, stool
    character). Avoid items are untouched (medication wording is
    not pain reporting).

    P-22 supplement (replacement alone was base-table dependent):
    when the base table has no pain / abdominal item to replace —
    e.g. the generic emergency list on an infant dehydration sheet —
    the infant items are *appended* instead, so the adaptation no
    longer depends on which base table the sheet happened to draw.
    The worsening item is appended only when no existing item
    already names reduced urine output, to avoid near-duplicates."""
    if not isinstance(sheet, dict) or not _is_infant_case(state):
        return sheet
    care = sheet.get("self_care")
    if isinstance(care, dict):
        care["do"] = [
            _INFANT_OBSERVABLE_DO
            if ("疼痛" in item and ("记下来" in item or "记录" in item))
            else item
            for item in care.get("do", [])
        ]
        care["measure"] = [
            _INFANT_OBSERVABLE_MEASURE if "疼痛" in item else item
            for item in care.get("measure", [])
        ]
        if _INFANT_OBSERVABLE_DO not in care.get("do", []):
            care["do"] = list(care.get("do", [])) + [_INFANT_OBSERVABLE_DO]
    if isinstance(sheet.get("return_now_if"), list):
        sheet["return_now_if"] = [
            _INFANT_WORSENING if "腹痛" in item else item
            for item in sheet["return_now_if"]
        ]
        if _INFANT_WORSENING not in sheet["return_now_if"] and not any(
            "尿量" in item for item in sheet["return_now_if"]
            if isinstance(item, str)
        ):
            sheet["return_now_if"] = list(sheet["return_now_if"]) + [
                _INFANT_WORSENING
            ]
    return sheet


def _apply_eye_generic_worsening(sheet: Dict[str, Any], state: CaseState) -> Dict[str, Any]:
    """Y-03 conclusion side, bounded by the batch-C brief: for a
    red/itch eye chief (the Y-03 conjunctivitis shape), the safety
    net is the generic four items plus one generic worsening item
    anchored to the recorded domain （眼睛不适明显加重）. The
    trigger is deliberately narrow — red/itch eye wording only —
    so other eye presentations keep their pinned safety net
    untouched (e.g. the sudden-vision sheet whose return_now_if is
    pinned to exactly the generic four). No eye-specific clinical
    warning items （视力下降 / 剧痛 / 外伤 / 发热 as safety-net
    entries) are invented here — those need clinical sign-off and
    are recorded as a residual; the eye-specific *questions* live
    in questions.json instead."""
    if not isinstance(sheet, dict):
        return sheet
    chief = state.facts.get("chief_complaint")
    chief_text = ""
    if chief is not None:
        if chief.value is not None:
            chief_text += str(chief.value)
        if chief.evidence:
            chief_text += str(chief.evidence)
    if not any(
        term in chief_text
        for term in ("眼红", "眼睛红", "眼痒", "眼睛痒", "结膜")
    ):
        return sheet
    if list(sheet.get("return_now_if") or []) != list(RETURN_NOW_IF):
        return sheet
    sheet["return_now_if"] = list(RETURN_NOW_IF) + ["眼睛不适明显加重"]
    return sheet


def _department_hint(
    tier: str,
    directions: List[Dict[str, Any]],
    group_key: str,
    data: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Department suggestion from content/departments.json (benchmark
    design §3.4). Priority: the urgent entry for urgent-tier sheets,
    then the first selected direction with a per-hypothesis entry, then
    the sheet pathway group's entry, then generic. Reasons may carry a
    {label} placeholder, filled with the matched direction's patient
    label. No usable entry anywhere -> None, the pre-P1 behavior."""
    if not data:
        return None

    def entry_of(raw) -> Optional[Dict[str, str]]:
        if not isinstance(raw, dict) or not raw.get("department"):
            return None
        return {
            "department": str(raw["department"]),
            "reason": str(raw.get("reason") or ""),
        }

    if tier == "urgent":
        hint = entry_of(data.get("urgent"))
        if hint is not None:
            return hint
    by_hypothesis = data.get("by_hypothesis") or {}
    for direction in directions:
        hint = entry_of(by_hypothesis.get(direction.get("hypothesis_id")))
        if hint is not None:
            hint["reason"] = hint["reason"].replace(
                "{label}", direction.get("patient_label") or ""
            )
            return hint
    by_group = data.get("by_group") or {}
    for key in (group_key, "generic"):
        hint = entry_of(by_group.get(key))
        if hint is not None:
            label = directions[0].get("patient_label") if directions else ""
            hint["reason"] = hint["reason"].replace("{label}", label or "")
            return hint
    return None


def _narrative_templates() -> Dict[str, Any]:
    """Curated narrative templates (content/conclusion_narrative.json).
    Like the self-care groups, the asset counts as absent unless it
    carries review metadata; absent/corrupt -> {} and the sheet simply
    carries narrative=None (fail-open)."""
    data = _load_json_optional("conclusion_narrative.json")
    return data if data.get("reviewed_by") else {}


def _narrative_reasoning(
    templates: Dict[str, Any],
    directions: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
) -> Optional[str]:
    """The “why” paragraph: certainty sentences for the selected
    directions plus the still-missing information. Only curated text —
    patient labels and gap labels, never patient quotes. None when
    there is nothing to explain (directions abstained/empty and no
    gaps), per the narrative contract."""
    sentences: List[str] = []
    if directions:
        rendered = [
            CERTAINTY_TEMPLATES[d["certainty"]].format(label=d["patient_label"])
            for d in directions
            if d.get("patient_label") and d.get("certainty") in CERTAINTY_TEMPLATES
        ]
        if rendered:
            intro = templates.get("directions_intro") or ""
            separator = templates.get("separator") or "；"
            sentences.append(intro + separator.join(rendered))
    if gaps:
        # Batch B three-state rendering: asked-unknown and declined gaps
        # are rendered in separate clauses with their own wording.
        # "还没有确认" must never appear here again — it conflated
        # asked-and-unclear with never-asked. Gaps without a state
        # (pre-batch-B callers) read as asked_unknown.
        separator = templates.get("gaps_separator") or "、"
        unknown_labels = [
            str(gap.get("label") or gap.get("fact_key"))
            for gap in gaps
            if gap.get("state") != "declined"
        ]
        declined_labels = [
            str(gap.get("label") or gap.get("fact_key"))
            for gap in gaps
            if gap.get("state") == "declined"
        ]
        parts: List[str] = []
        if unknown_labels:
            parts.append(
                (
                    templates.get("gaps_unknown_template")
                    or "你提到{labels}还不清楚，这也会影响判断"
                ).replace("{labels}", separator.join(unknown_labels))
            )
        if declined_labels:
            parts.append(
                (
                    templates.get("gaps_declined_template")
                    or "{labels}你表示暂不提供，这也会影响判断"
                ).replace("{labels}", separator.join(declined_labels))
            )
        gap_text = "；".join(parts)
        if gap_text:
            if sentences:
                sentences.append(
                    (templates.get("gaps_connector") or "") + gap_text
                )
            else:
                sentences.append(
                    (templates.get("gaps_only_intro") or "") + gap_text
                )
    if not sentences:
        return None
    return "。".join(sentences) + "。"


def _build_narrative(
    templates: Dict[str, Any],
    *,
    tier: str,
    level: str,
    group_key: str,
    directions: List[Dict[str, Any]],
    gaps: List[Dict[str, Any]],
    state: Optional[CaseState] = None,
) -> Optional[Dict[str, Any]]:
    """Three-part narrative (benchmark design §3.1): framing （定性）,
    reasoning （原理）, severity （大白话程度）. Framing and severity are
    always present on a sheet when the asset is available; reasoning
    may be None. Deterministic template assembly only.

    P4 (S-11): the severity line passes through the consistency
    fallback when a state is supplied — a recorded low score never
    keeps the urgent template's "程度偏高" claim; it falls back to
    restating the recorded score. The template itself is untouched,
    and no disposition is recomputed."""
    if not templates:
        return None
    framing_map = templates.get("framing") or {}
    if tier == "urgent" and framing_map.get("urgent"):
        framing_template = framing_map["urgent"]
    else:
        framing_template = framing_map.get(level)
    severity_template = (templates.get("severity") or {}).get(level)
    if not framing_template or not severity_template:
        return None
    group_label = (templates.get("group_labels") or {}).get(group_key, "身体")
    severity_text = severity_template.replace("{group}", group_label)
    if state is not None:
        severity_text = severity_consistent_text(severity_text, state)
    return {
        "framing": framing_template.replace("{group}", group_label),
        "reasoning": _narrative_reasoning(
            templates.get("reasoning") or {}, directions, gaps
        ),
        "severity": severity_text,
    }


# Batch B worry / goal responses. Both slots use only already-collected
# facts and the sheet's own disposition wording: they must never name a
# shadow direction label or a department hint, so the direction /
# department visibility gates (C3 / C12) cannot be bypassed through
# this channel. The worry response never mentions a hypothesis; the
# goal response never mentions one either.
_VISIT_GOAL_KEYWORDS = ("检查", "去医院", "就诊", "看医生")
_WORRY_FALLBACK_OBJECT = "你问过的那个担心"


def _contains_banned(text: str) -> bool:
    return bool(text) and any(phrase in text for phrase in BANNED_PHRASES)


def _worry_tail(chief: str, severity: Any, gap_labels: List[str]) -> str:
    tail = f"已确认的是{chief}；" if chief else ""
    if severity is not None:
        tail += f"影响程度为 {severity} 分；"
    if gap_labels:
        tail += f"{'、'.join(gap_labels)}还不清楚；"
    return tail


def _build_worry(
    state: CaseState, gaps: List[Dict[str, Any]], goal_value: Optional[str]
) -> tuple[Optional[Dict[str, str]], bool]:
    """Batch B worry slot. Source priority: state.facts["patient_worry"]
    (PRESENT, value is the object) > a worry sentence recorded as the
    patient goal (worry.is_worry_sentence / extract_worry_object). The
    second source consumes the goal: it returns ``goal_consumed=True``
    so the caller sets goal_line to None — a worry must never overwrite
    a patient goal (K-03), and when the goal slot itself holds the
    worry, the goal slot is emptied rather than duplicated.

    Returns (worry_dict_or_None, goal_consumed). The response template
    uses only the chief complaint, the severity score when collected,
    and the gap labels — never a direction or department."""
    worry_fact = state.facts.get("patient_worry")
    obj: Optional[str] = None
    goal_consumed = False
    if (
        worry_fact is not None
        and worry_fact.assertion == Assertion.PRESENT
        and str(worry_fact.value or "").strip()
    ):
        obj = str(worry_fact.value).strip()
    elif goal_value and is_worry_sentence(goal_value):
        obj = extract_worry_object(goal_value)
        if obj:
            goal_consumed = True
    if not obj:
        return None, False
    chief_fact = state.facts.get("chief_complaint")
    chief = (
        str(chief_fact.value).strip()
        if chief_fact is not None
        and chief_fact.assertion == Assertion.PRESENT
        and chief_fact.value is not None
        else ""
    )
    severity_fact = state.facts.get("severity")
    severity = (
        severity_fact.value
        if severity_fact is not None
        and severity_fact.assertion == Assertion.PRESENT
        else None
    )
    gap_labels = [
        str(gap.get("label") or gap.get("fact_key")) for gap in (gaps or [])
    ]
    close = "建议按结论单的处置去做，出现下方警示信号请及时就医。"
    response = (
        f"你问过是不是‘{obj}’：目前还不能仅凭这次问诊确定这一点。"
        f"{_worry_tail(chief, severity, gap_labels)}{close}"
    )
    if _contains_banned(obj) or _contains_banned(response):
        # Degrade: the object becomes the generic fallback and the
        # response drops the quoted object, keeping the other sentences.
        # A banned phrase inside the chief complaint itself (patient
        # wording) is replaced the same way so the sheet lint below
        # still passes; gap labels are curated and severity is a score.
        safe_chief = (
            "你描述的主要问题" if _contains_banned(chief) else chief
        )
        response = (
            f"{_WORRY_FALLBACK_OBJECT}，目前还不能仅凭这次问诊确定这一点。"
            f"{_worry_tail(safe_chief, severity, gap_labels)}{close}"
        )
        obj = _WORRY_FALLBACK_OBJECT
    return {"object": obj, "response": response}, goal_consumed


def _build_goal_response(goal_line: Optional[str]) -> Optional[str]:
    """Batch B goal slot. Only for a non-empty goal_line that is not a
    visit-oriented goal: goals mentioning 检查 / 去医院 / 就诊 /
    看医生 return None — batch A's _speak_close_line branch owns those
    and this slot must not duplicate it. Over 60 chars, the quoted goal
    is truncated. A banned phrase degrades the response to an
    object-free form (the goal itself is still quoted nowhere)."""
    if not goal_line:
        return None
    # P2 slot guard (O-15 / U-17): a worry sentence sitting in the
    # goal slot must never render a second, near-identical answer
    # next to the worry slot's own response. is_worry_sentence is
    # reused from worry.py; no local judgement is invented here.
    # (The goal slot itself is still emptied by _build_worry only
    # when no separate patient_worry fact exists; when both facts
    # exist this guard is what keeps the close to one rendering.)
    if is_worry_sentence(goal_line):
        return None
    if any(keyword in goal_line for keyword in _VISIT_GOAL_KEYWORDS):
        return None
    display = goal_line if len(goal_line) <= 60 else goal_line[:60] + "…"
    response = (
        f"你问到‘{display}’：这次问诊的信息还不足以直接回答这一点，"
        "需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。"
    )
    if _contains_banned(goal_line) or _contains_banned(response):
        response = (
            "你问到的这一点，这次问诊的信息还不足以直接回答这一点，"
            "需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。"
        )
    return response


# ---------------------------------------------------------------------------
# P2 goal-part handling (mechanism ②: a multi-part goal must be
# answered part by part, not only on its visit part).
#
# Why the whole-goal slot is not enough: _build_goal_response returns
# None for any goal_line containing a visit keyword, so a goal like
# "要不要去医院，还是先吃止痛药观察" (K-16) lost its non-visit part
# entirely — the batch-A visit sentence answered only the visit part.
# The close layer therefore re-walks the parts: visit parts are
# covered by the batch-A sentence, every other part needs its own
# handling sentence, and a part whose handling cannot be sourced from
# the sheet's own self-care items gets the fixed "insufficient
# information" sentence. No efficacy, dosage, or disposition promise
# is ever composed here: drug / treatment parts always take the
# fixed sentence, never a self-care item's wording.
# ---------------------------------------------------------------------------

# Leading question scaffolding stripped (longest first) before a
# part's content keywords are derived. These are question frames,
# never content: stripping them leaves the thing actually asked
# about (e.g. "要不要吃退烧药" -> "吃退烧药" -> "退烧药").
_GOAL_PART_PREFIXES = (
    "我就想知道",
    "我只想知道",
    "最想知道",
    "想知道",
    "想问",
    "要不要",
    "能不能",
    "会不会",
    "需不需要",
    "是不是",
    "是否",
    "平时怎么",
    "吃什么能",
    "吃什么",
    "先吃",
    "还是",
    "还有",
    "需要",
    "先",
    "要",
)

# Parts naming a drug or a named treatment: handling is always the
# fixed sentence, even if a self-care item happens to share a word.
# (A generic "不要自行服用抗生素" is not an answer to "要不要吃退烧药".)
_GOAL_DRUG_TREATMENT_WORDS = (
    "止痛药",
    "退烧药",
    "眼药水",
    "滴眼液",
    "滴眼药",
    "复位",
    "含片",
    "感冒药",
    "抗生素",
    "用药",
    "吃药",
)

_GOAL_PART_FIXED_HANDLING = (
    "这一点这次问诊的信息还不足以直接回答，需当面评估后决定"
)

# Stopwords removed when deriving chief-complaint chunks for the
# K-17 preference (a self-care item overlapping the chief wins over
# a merely part-matching one); the chunks themselves are patient
# wording, never new clinical content.
_CHIEF_CHUNK_STOPWORDS = (
    "我",
    "自己",
    "孩子",
    "咨询",
    "的",
    "了",
    "很",
    "有",
    "是",
    "最近",
    "一直",
    "感觉",
    "出现",
    "情况",
    "问题",
    "这次",
    "有点",
    "比较",
)


def split_goal_parts(goal_line: str) -> List[str]:
    """Split a goal line into parts on ，、；, stripped, deduped,
    order preserved. Empty parts and trailing terminal punctuation
    (the recorded sentence's own 。) are dropped."""
    if not goal_line:
        return []
    parts: List[str] = []
    for raw in re.split("[，、；]", goal_line):
        part = raw.strip().strip("。！？")
        if part and part not in parts:
            parts.append(part)
    return parts


def _part_keywords(part: str) -> List[str]:
    """Content keywords of one goal part, for the addressed check.

    The full part is always a keyword; stripping question scaffolding
    and one leading verb character adds the shorter content form
    (e.g. "吃退烧药" -> "退烧药"), and a "不X" form adds X
    (e.g. "正不正常" -> "正常"). The addressed check is a substring
    check of these keywords in the visible close text *excluding*
    the goal_response's own quote of the goal — otherwise every part
    would trivially "appear" via its own quotation. Note the
    deliberate non-shortening: "缓解一点" is kept whole, not cut to
    "缓解", so a batch-A condition clause ("症状持续不缓解") does
    not falsely mark a relief-method part as answered (F-13)."""
    core = part.strip()
    # Strip scaffolding repeatedly (longest first each pass): stacked
    # frames are real ("还是先吃…" -> "先吃…" -> content; "是不是
    # 需要做…" -> "需要做…" -> content). Bounded so a degenerate
    # part cannot loop.
    for _ in range(3):
        stripped = False
        for prefix in _GOAL_PART_PREFIXES:
            if core.startswith(prefix) and len(core) > len(prefix):
                core = core[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    keywords: List[str] = []
    if part.strip() and part.strip() not in keywords:
        keywords.append(part.strip())
    if core and core not in keywords:
        keywords.append(core)
    # One leading verb character off (吃/用/做/留 …): "吃退烧药"
    # also matches on "退烧药" alone.
    if len(core) >= 3 and core[0] in "吃用喝做贴滴停开留":
        shorter = core[1:]
        if shorter not in keywords:
            keywords.append(shorter)
    # "不X" -> X ("正不正常" -> "正常").
    index = core.find("不")
    if index >= 0 and len(core) - index - 1 >= 2:
        without_bu = core[index + 1:]
        if without_bu not in keywords:
            keywords.append(without_bu)
    return [keyword for keyword in keywords if len(keyword) >= 2]


# Visit semantics for the *part* filter only (the composer's
# _VISIT_GOAL_KEYWORDS is untouched): 就医 is visit wording too —
# a part like "想整理就医记录" is answered by the disposition line
# ("暂时不需要专门跑一趟医院") and the confirmation flow itself,
# not by a per-part sentence. Without this, record-organisation
# goals that the composer already answers generically would gain a
# redundant per-part sentence and break the pinned close bytes.
_GOAL_VISIT_PART_KEYWORDS = _VISIT_GOAL_KEYWORDS + ("就医",)


def _keyword_addressed(keyword: str, visible: str) -> bool:
    """A keyword occurrence counts as handling only when it is not
    a negated mention. O-17: the batch-A condition clause
    "症状持续不缓解" contains 缓解, but a negated mention is not a
    relief-method response — only an occurrence not immediately
    preceded by 不 counts."""
    if not keyword:
        return False
    start = 0
    while True:
        index = visible.find(keyword, start)
        if index < 0:
            return False
        if index == 0 or visible[index - 1] != "不":
            return True
        start = index + 1


def _visit_part_residual(part: str) -> str:
    """Content hidden inside a visit part (Y-16 / D-14).

    A part like "去医院挑开" or "去医院开药" is filtered as a
    visit part, which used to swallow the non-visit content after
    the visit phrase. The residual exists only for the narrow
    destination-plus-content shape: after stripping question
    scaffolding and urgency fillers, the part must *start* with
    去医院, and the text after it (with any further visit wording
    stripped) is the residual. Pure visit parts ("去医院",
    "去医院检查") leave an empty residual and stay covered, and
    a visit keyword embedded in a noun phrase ("想整理就医记录":
    就医记录 is the record itself, not a second question) never
    yields a residual — that pinned shape stays covered by the
    disposition line alone."""
    core = part.strip()
    for _ in range(3):
        stripped = False
        for prefix in _GOAL_PART_PREFIXES:
            if core.startswith(prefix) and len(core) > len(prefix):
                core = core[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    for filler in ("马上", "立刻", "尽快"):
        if core.startswith(filler):
            core = core[len(filler):]
    if not core.startswith("去医院"):
        return ""
    suffix = core[len("去医院"):]
    for keyword in _GOAL_VISIT_PART_KEYWORDS + ("急诊", "看病"):
        suffix = suffix.replace(keyword, "")
    suffix = suffix.strip(" ，、；。！？")
    for prefix in ("还是", "或者", "顺便"):
        if suffix.startswith(prefix) and len(suffix) > len(prefix):
            suffix = suffix[len(prefix):]
    suffix = suffix.strip(" ，、；。！？")
    return suffix if len(suffix) >= 2 else ""


def unaddressed_goal_parts(goal_line: str, visible_text: str) -> List[str]:
    """Parts of a goal line with no handling in the visible text.

    Pure visit-keyword parts are treated as covered by the batch-A
    visit sentence and never listed; any other part whose keywords
    appear nowhere in visible_text is unaddressed. A visit part
    with residual non-visit content (Y-16 "挑开", D-14 "开药")
    contributes the residual as its own part. Callers must pass
    the close text *without* the goal_response quote (see
    _part_keywords) or the check is vacuous."""
    if not goal_line:
        return []
    visible = visible_text or ""
    unaddressed: List[str] = []
    for part in split_goal_parts(goal_line):
        if any(keyword in part for keyword in _GOAL_VISIT_PART_KEYWORDS):
            residual = _visit_part_residual(part)
            if not residual:
                continue
            keywords = _part_keywords(residual)
            if not keywords:
                continue
            if not any(_keyword_addressed(keyword, visible) for keyword in keywords):
                unaddressed.append(residual)
            continue
        keywords = _part_keywords(part)
        if not keywords:
            continue
        if not any(_keyword_addressed(keyword, visible) for keyword in keywords):
            unaddressed.append(part)
    return unaddressed


def unaddressed_urgent_goal_parts(goal_line: str, visible_text: str) -> List[str]:
    """Urgent-close walk (O-19): the shared walk plus examination
    parts. The urgent head answers the destination question
    ("要不要马上去急诊：要") but never the examination question,
    so a part naming 检查 with no 检查 handling in the visible
    text is walked with the fixed sentence ("需当面评估后决定"
    is the offline-evaluation framing the O-19 opinion allows).
    Pure destination parts stay covered by the direct sentence
    and are never listed — listing them would re-introduce the
    N-11 contradiction in per-part form."""
    parts = unaddressed_goal_parts(goal_line, visible_text)
    visible = visible_text or ""
    if _keyword_addressed("检查", visible):
        return parts
    for part in split_goal_parts(goal_line or ""):
        if "检查" in part and part not in parts:
            parts.append(part)
    return parts


def goal_part_handling(
    part: str, sheet: Dict[str, Any], chief_text: str = ""
) -> str:
    """Handling sentence body for one unaddressed goal part.

    Drug / treatment parts always take the fixed sentence. Otherwise
    the first self-care "do" item sharing a part keyword wins, with
    chief-overlapping items preferred (K-17: a do item matching the
    chief complaint's own wording is the most specific answer the
    sheet already contains). When no sheet item matches, the fixed
    sentence is returned — never an invented self-care line. In
    particular, self_care.json contains no ulcer-specific entry at
    all (verified: 溃疡/口腔/辣/酸 all absent), so ulcer goals fall
    through to the fixed sentence and stay a reported residual."""
    if any(word in part for word in _GOAL_DRUG_TREATMENT_WORDS):
        return _GOAL_PART_FIXED_HANDLING
    keywords = _part_keywords(part)
    care = sheet.get("self_care") if isinstance(sheet, dict) else None
    do_items = (
        [item for item in (care or {}).get("do", []) if isinstance(item, str)]
        if isinstance(care, dict)
        else []
    )
    candidates = [
        item for item in do_items if any(keyword in item for keyword in keywords)
    ]
    if not candidates:
        return _GOAL_PART_FIXED_HANDLING
    chunks = [
        chunk
        for chunk in re.split("[，、。；：\\s]", chief_text or "")
        if len(chunk) >= 2 and chunk not in _CHIEF_CHUNK_STOPWORDS
    ]
    for item in candidates:
        if any(chunk in item for chunk in chunks):
            return item
    return candidates[0]


# ---------------------------------------------------------------------------
# G-12 engineering-side exit (UNDETERMINED must not silently slide
# to routine). The disposition mapping itself is untouched: this only
# adds (a) an explicit uncertainty note on the sheet, rendered by the
# close layer, and (b) worsening items derived strictly from
# symptoms the patient already reported (PRESENT facts). Whether the
# level itself should move is a clinical-review decision, not made
# here; asking the discriminating questions before close (volume,
# frequency, persistence / recurrence, whether dizziness / palpitations
# persist) would need a planning change and is recorded as a residual.
# ---------------------------------------------------------------------------

UNCERTAINTY_NOTE = (
    "需要说明的是：这次问诊里急症筛查的结果还不明确，"
    "现有信息还不足以完全判断紧急程度，请对照下方加重即就医项留意变化，"
    "必要时尽快线下评估。"
)

# (reported-symptom terms, worsening item, dedup terms already covered
# by an existing safety-net item). Only PRESENT facts are read, so a
# denied or unknown symptom never invents an item.
_G12_WORSENING_MAP = (
    (("便血", "黑便", "血便"), "再出现便血或黑便", ("便血", "黑便")),
    (("呕血", "吐血"), "再出现呕血", ("呕血",)),
    (("头晕",), "头晕加重", ("头晕",)),
    (("心慌",), "心慌加重", ("心慌",)),
)


def _is_undetermined_unknown_screen(state: CaseState) -> bool:
    """G-12 gate: risk UNDETERMINED *and* the emergency screen itself
    answered unknown. A missing screen is not an unknown screen."""
    if state.risk is None or state.risk.level != RiskLevel.UNDETERMINED:
        return False
    screen = state.facts.get("emergency_screen")
    return screen is not None and screen.assertion == Assertion.UNKNOWN


def _apply_undetermined_safety(sheet: Dict[str, Any], state: CaseState) -> Dict[str, Any]:
    """G-12 (a)+(b): uncertainty note + patient-derived worsening items."""
    if not isinstance(sheet, dict) or not _is_undetermined_unknown_screen(state):
        return sheet
    sheet["uncertainty_note"] = UNCERTAINTY_NOTE
    reported = ""
    for fact in state.facts.values():
        if fact.assertion != Assertion.PRESENT:
            continue
        if isinstance(fact.value, str):
            reported += fact.value
        if fact.evidence:
            reported += fact.evidence
    current = sheet.get("return_now_if")
    if isinstance(current, list):
        for terms, item, dedup_terms in _G12_WORSENING_MAP:
            if not any(term in reported for term in terms):
                continue
            if any(
                any(dedup in existing for dedup in dedup_terms)
                for existing in current
                if isinstance(existing, str)
            ):
                continue
            if item not in current:
                current.append(item)
    return sheet


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


def compose_conclusion(
    state: CaseState,
    summary: Optional[Dict[str, Any]] = None,
    baseline_allergies: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """Build the conclusion sheet for a just-closed session, or None when
    no sheet should exist (not closed, emergency route, still open).

    ``baseline_allergies`` is the archive projection's allergy list
    (P2c, from state.profile_baseline — never from facts). When it
    yields a hint, the hint lands in its own self_care field; it is
    never appended to the versioned ``avoid`` list and never derives
    gaps.

    DORMANT under self_care 1.2: no real avoid line mentions allergy,
    so this hint cannot fire on real content today (pinned by
    test_p2_integration's real-content count test). Enabling it needs
    a content line plus clinical review — a content decision, not a
    code change here; do not edit self_care wording to force it.

    Visibility contract (modular-refactor design §4.4, frozen — the
    flag computation below is NOT to be "fixed" by renderers):
    ``directions`` is always computed and always present, gated by
    ``directions_visible``; ``narrative.reasoning`` shares that gate;
    ``disposition.department_hint`` is always computed, gated by
    ``department_visible``; ``narrative.framing`` / ``severity``,
    ``disposition.text``, ``gaps``, ``self_care``, ``return_now_if``,
    ``worry``, ``goal_response``, ``outlook`` and ``follow_up`` are
    ungated and always renderable. Both flags default off.
    """
    if state.phase != Phase.CLOSED:
        return None
    if state.closed_reason not in ("confirmed_by_user", "urgent_route"):
        return None
    if state.risk.level == RiskLevel.EMERGENCY:
        return None
    tier = (
        "urgent"
        if (
            state.closed_reason == "urgent_route"
            or state.risk.level == RiskLevel.URGENT
        )
        else "routine"
    )
    plan = state.follow_up or build_follow_up_plan(state) or {}
    due_hours = plan.get("due_in_hours")

    workspace = RuleBasedProposer().propose(state)
    accepted, _rejected = validate_workspace(state, workspace)
    show_directions = directions_visible()
    directions, abstained = _select_directions(accepted, state)
    disposition = _disposition(state, tier, directions, due_hours, show_directions)
    gaps = _gaps(state, summary, accepted)
    if tier == "routine" and disposition["level"] == "self_care":
        if any(gap["significant"] for gap in gaps):
            window = due_hours or 72
            disposition = {
                "level": "routine_visit",
                "window_hours": window,
                "text": format_window_text(
                    {
                        "level": "routine_visit",
                        "window_hours": window,
                        "window_kind": "after_if_not_improved",
                    },
                    window,
                ),
                "department_hint": None,
            }
    group_key = "generic"
    # Self-care follows the directions actually chosen for the sheet —
    # never a refuted hypothesis the patient will not see (previously a
    # refuted pneumonia could supply the care group on an abstained
    # sheet, contradicting the directions above it).
    for direction in directions:
        hid = direction.get("hypothesis_id")
        if hid in PATHWAY_GROUPS:
            group_key = PATHWAY_GROUPS[hid]
            break
    # Batch B sheet_group: the first direction (selection order) with
    # at least two distinct valid evidence tokens (see
    # _sheet_evidence_count). A single-token cross-system direction
    # (K-12: one respiratory quote on a urinary chief complaint) must
    # not drive outlook, self-care, narrative framing/severity, or
    # the safety net — those take sheet_group and fall back to
    # generic. Disposition, department_hint and the direction
    # selection order itself are untouched (group_key above still
    # feeds the department hint).
    sheet_group = "generic"
    for direction in directions:
        hid = direction.get("hypothesis_id")
        if hid in PATHWAY_GROUPS and _sheet_evidence_count(
            accepted, state, direction
        ) >= 2:
            sheet_group = PATHWAY_GROUPS[hid]
            break
    care = _self_care_group(sheet_group, tier) or {}
    # P2c allergy hint: its own field, never mixed into the versioned
    # avoid list. A patient-entered allergy name containing a banned
    # phrase must not void the whole sheet, so such a hint is dropped
    # instead of reaching the lint below.
    allergy_hint = (
        build_allergy_hint(baseline_allergies, care.get("avoid", []))
        if baseline_allergies
        else None
    )
    if allergy_hint and any(
        phrase in allergy_hint for phrase in BANNED_PHRASES
    ):
        allergy_hint = None
    # Batch B safety net: when directions exist it keys on sheet_group
    # (never the explored pathway — a probed-but-unselected pathway
    # must not supply warnings); when sheet_group is generic the
    # pathway lookup below yields nothing and the generic list wins.
    # When directions abstain, the pre-batch-B explored-pathway
    # fallback is kept verbatim (live-eval run3 case3 pin).
    if abstained:
        alert_group = _explored_pathway_group(state) or "generic"
    else:
        alert_group = sheet_group
    dept_data = _load_json_optional("departments.json")
    disposition["department_hint"] = _department_hint(
        tier, directions, group_key, dept_data
    )
    narrative = _build_narrative(
        _narrative_templates(),
        tier=tier,
        level=disposition["level"],
        group_key=alert_group,
        directions=directions,
        gaps=gaps,
        state=state,  # P4 S-11: severity consistency fallback only
    )
    subject = state.facts.get("consult_for")
    goal_fact = state.facts.get("patient_goal")
    raw_goal = (
        str(goal_fact.value).strip()
        if goal_fact is not None
        and goal_fact.assertion == Assertion.PRESENT
        and str(goal_fact.value or "").strip()
        else None
    )
    # Batch B: a worry sentence sitting in the goal slot is split out
    # into the worry slot and the goal line is emptied (K-03); a
    # genuine goal survives untouched. goal_response is derived from
    # the post-split goal_line only.
    worry, goal_consumed = _build_worry(state, gaps, raw_goal)
    goal_line = None if goal_consumed else raw_goal
    goal_response = _build_goal_response(goal_line)
    previous = state.conclusion or {}
    sheet = {
        "version": 1,
        "revision": int(previous.get("revision", 0)) + 1,
        "tier": tier,
        "subject_kind": subject.value if subject is not None else "self",
        "disposition": disposition,
        "goal_line": goal_line,
        "worry": worry,
        "goal_response": goal_response,
        "directions": directions,
        "directions_abstained": abstained,
        "directions_visible": show_directions,
        "department_visible": department_visible(),
        "abstain_text": ABSTAIN_TEXT if abstained else None,
        "narrative": narrative,
        "gaps": gaps,
        "self_care": {
            "do": list(care.get("do", [])),
            "avoid": list(care.get("avoid", [])),
            "measure": list(care.get("measure", [])),
            **({"allergy_hint": allergy_hint} if allergy_hint else {}),
        },
        "return_now_if": list(
            _pathway_return_now_if(alert_group)
            or (summary or {}).get("return_now_if")
            or RETURN_NOW_IF
        ),
        "outlook": care.get("course"),
        "follow_up": (
            {"kind": plan.get("kind"), "due_in_hours": due_hours}
            if plan
            else None
        ),
        "generated_by": "conclusion-composer/v1",
        "content_versions": {
            "differentials": _load_json("differentials.json").get("version"),
            "self_care": _load_json("self_care.json").get("version"),
            "departments": dept_data.get("version"),
            "conclusion_narrative": _narrative_templates().get("version"),
        },
    }
    # Off-pathway neutral fallback (batch A, W2, verified in batch
    # C for E-02/K-09/Y-07): a direction-less sheet whose chief
    # complaint triggers no pathway at all must not speak with a
    # probed pathway's language. The batch-C check here is
    # deliberately NOT generalised to non-abstained sheets: a
    # direction that carries at least two distinct valid evidence
    # tokens drives its group by the batch-B sheet_group rule
    # (pinned by test_two_valid_quotes_drive_their_group), even
    # when its group differs from the chief's pathway — changing
    # that anchoring would be a reasoning-layer change, outside
    # this narrative layer's scope. The round-1 E-02/K-09/Y-07
    # patterns are all abstained or single-token (see the batch-C
    # tests), so the batch-A fallback already covers them, and
    # apply_neutral_fallback is reused as-is, never duplicated.
    # Directions themselves, disposition, department hint, worry
    # and goal are untouched by apply_neutral_fallback, so
    # direction visibility still rests solely on the C3 gate.
    # Pathway-matched abstained sheets keep the explored-pathway
    # safety net (run3 case3, pinned by test). A safety net the
    # caller supplied via summary is the caller's explicit list
    # and is restored after the fallback (pinned by
    # test_summary_list_used_when_no_pathway). Applied before the
    # lint so the neutral wording is linted like everything else.
    if abstained and not chief_matches_pathway(str(state.get("chief_complaint") or "")):
        sheet = apply_neutral_fallback(sheet)
        summary_alerts = (summary or {}).get("return_now_if")
        if summary_alerts:
            sheet["return_now_if"] = list(summary_alerts)
    # Batch C wording post-processing, in order: assertion-state
    # wording (T-04), chronic-outlook consistency (E-04/U-09),
    # infant-observable wording (P-17), eye-domain generic
    # worsening (Y-03). None of these touch worry/goal slots,
    # disposition level/window, or direction visibility.
    sheet = _adjust_assertion_wording(sheet, state)
    sheet = _adjust_chronic_outlook(sheet, state)
    sheet = _apply_infant_observable(sheet, state)
    sheet = _apply_eye_generic_worsening(sheet, state)
    # G-12 engineering exit (disposition mapping untouched): an
    # UNDETERMINED sheet whose emergency screen is unknown carries an
    # explicit uncertainty note and patient-derived worsening items.
    sheet = _apply_undetermined_safety(sheet, state)
    # The banned-phrase lint guards *system* wording. Patient quotes
    # (support_quotes) are the patient's own words — a patient writing
    # "没事，就是咳" must not silently void the whole sheet.
    lint_view = deepcopy(sheet)
    for direction in lint_view.get("directions", []):
        direction.pop("support_quotes", None)
    rendered = json.dumps(lint_view, ensure_ascii=False)
    if any(phrase in rendered for phrase in BANNED_PHRASES):
        raise ValueError("conclusion sheet failed the banned-phrase lint")
    return sheet


def spoken_close_line(conclusion: Optional[Dict[str, Any]]) -> str:
    """One or two plain-language closing sentences derived from a sheet.

    The sheet itself renders as a card; this line is what the closing
    chat message can *say* out loud: the disposition first, then the
    single most important return_now_if alert. Returns "" when there is
    no usable sheet. Fail-closed: a banned phrase anywhere in the
    assembled line voids it, same as the sheet lint.
    """
    if not conclusion:
        return ""
    disposition = conclusion.get("disposition") or {}
    level = disposition.get("level")
    if not level:
        return ""
    window = disposition.get("window_hours")
    if level == "urgent_today":
        main = "建议今天内到线下医疗机构当面评估，不要在家观察等待。"
    elif level == "routine_visit":
        # Batch C rework (G-02/U-02): single-source the spoken window
        # sentence from the sheet's own disposition dict via
        # format_window_text, so the upgrade variant ("48 小时后还没
        # 有好转") and a visible rule-out label ("排除 …") round-trip
        # verbatim instead of being rewritten into a "within" sentence
        # or dropped. window_kind is not stored on the composed dict,
        # but the helper recovers both from the sheet text; a hidden
        # label stays hidden because the sheet text carries no label.
        # Fallback to the previous hard-coded sentences only if the
        # helper returns "" (it does not for routine_visit today).
        main = format_window_text(disposition) or (
            f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
            if window
            else "可以在家先观察；请尽快到线下医疗机构就诊评估。"
        )
    elif level == "self_care":
        # Batch C rework (P-10): the framing opener already says the
        # case "可以在家先观察处理", so the main sentence must not
        # repeat the home-observation concept ("可以先在家观察，…").
        # Keep only the no-visit clause; framing is untouched.
        main = "暂时不需要专门跑一趟医院。"
    else:
        text = str(disposition.get("text") or "").strip()
        if not text:
            return ""
        main = text if text.endswith(("。", "！", "？")) else text + "。"
    alerts = [
        str(item).strip()
        for item in (conclusion.get("return_now_if") or [])
        if str(item).strip()
    ]
    # The spoken line opens with the narrative's framing sentence for
    # this disposition level (benchmark design §3.1), then the existing
    # disposition + first alert + 120 elements, unchanged. The framing
    # comes from the curated asset, not the sheet, so sheets composed
    # before the narrative existed still speak with it; a missing asset
    # simply drops the opener (fail-open).
    framing = (
        (_narrative_templates().get("spoken_framing") or {}).get(level) or ""
    ).strip()
    line = framing + main if framing else main
    if alerts:
        line += f"如果出现{alerts[0]}，请及时就医；情况紧急时请拨打 120。"
    if any(phrase in line for phrase in BANNED_PHRASES):
        return ""
    return line


def emergency_explanation(triggers) -> str:
    """Patient-facing explanation line for an emergency close.

    ``triggers`` are the deterministic scanner's trigger names (plain
    patient-facing strings, as stored on RiskAssessment.triggers); dicts
    carrying a patient-facing label are also accepted defensively. The
    line names the danger category, tells the patient to call 120 now,
    and gives waiting guidance. Fail-closed on the banned-phrase lint:
    if a trigger name somehow trips it, the names are dropped rather
    than the whole explanation.
    """
    names: List[str] = []
    for trigger in triggers or []:
        text = ""
        if isinstance(trigger, str):
            text = trigger.strip()
        elif isinstance(trigger, dict):
            for key in ("patient_text", "label", "description", "name", "id"):
                value = trigger.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
        if text and text not in names:
            names.append(text)
    guidance = "请现在拨打 120，等待时保持安静休息，不要自行开车，有家人陪同更好。"
    if names:
        line = (
            "你描述的情况包含需要立即处理的危险信号（"
            + "、".join(names[:3])
            + "）。"
            + guidance
        )
        if not any(phrase in line for phrase in BANNED_PHRASES):
            return line
    return "你描述的情况包含需要立即处理的危险信号。" + guidance
