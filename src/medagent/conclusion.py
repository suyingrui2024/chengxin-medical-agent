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
from copy import deepcopy
from importlib import resources
from typing import Any, Dict, List, Optional

from .baseline_projection import build_allergy_hint
from .domain import Assertion, CaseState, Phase, RiskLevel
from .followups import build_follow_up_plan
from .handoff import LABELS as FACT_LABELS
from .reasoning import RuleBasedProposer, validate_workspace
from .text_match import contains_unnegated, find_unnegated_certain
from .worry import extract_worry_object, is_worry_sentence

# Batch B citation validation uses text_match's shared
# ``find_unnegated_certain`` directly (imported above). An earlier
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
    evaluation gate and clinician review (design §9)."""
    return os.environ.get("MEDAGENT_CONCLUSION_DIRECTIONS", "off").strip().lower() == "on"


def department_visible() -> bool:
    """Department hints ship in shadow too (benchmark design §3.4/D2):
    the composer always fills the hint from reviewed content, and this
    flag tells the renderer whether it may be shown before the C12
    clinical sign-off."""
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
# affirmed-and-certain test is text_match.find_unnegated_certain
# itself (imported at the top); there is deliberately no local copy
# of its negation/uncertainty rules in this module.
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
        if quote in source and find_unnegated_certain(source, quote) >= 0:
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
        valid_quotes = [
            ref.quote
            for ref in hypothesis.evidence_for
            if _valid_support_quote(state, ref)
        ]
        if not valid_quotes:
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


def _disposition(state, tier, directions, due_hours, show_directions):
    if tier == "urgent":
        return {
            "level": "urgent_today",
            "window_hours": due_hours or 24,
            "text": "建议今天内到线下医疗机构当面评估，不要在家观察等待。",
            "department_hint": None,
        }
    rule_outs = [d for d in directions if d["group"] == "rule_out"]
    if rule_outs:
        window = due_hours or 48
        top = rule_outs[0]
        if show_directions and top.get("patient_label"):
            text = (
                f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊，"
                f"排除 {top['patient_label']}。"
            )
        else:
            text = f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
        return {
            "level": "routine_visit",
            "window_hours": window,
            "text": text,
            "department_hint": None,
        }
    return {
        "level": "self_care",
        "window_hours": due_hours,
        "text": (
            "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。"
        ),
        "department_hint": None,
    }


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
) -> Optional[Dict[str, Any]]:
    """Three-part narrative (benchmark design §3.1): framing （定性）,
    reasoning （原理）, severity （大白话程度）. Framing and severity are
    always present on a sheet when the asset is available; reasoning
    may be None. Deterministic template assembly only."""
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
    return {
        "framing": framing_template.replace("{group}", group_label),
        "reasoning": _narrative_reasoning(
            templates.get("reasoning") or {}, directions, gaps
        ),
        "severity": severity_template.replace("{group}", group_label),
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
    the hypothesis's DSL terms attested — unnegated and certain under
    the shared find_unnegated_certain — in the value/evidence of the
    facts its evidence refs actually point at. A single token can
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
                if find_unnegated_certain(text, term) >= 0:
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
                "text": (
                    "可以在家先观察；把下方缺的信息补上，"
                    f"如果 {window} 小时后还没有好转，请到线下医疗机构就诊。"
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
    # Off-pathway neutral fallback (batch A, W2): a direction-less sheet
    # whose chief complaint triggers no pathway at all must not speak
    # with a probed pathway's language. Applied before the lint so the
    # neutral wording is linted like everything else. Pathway-matched
    # abstained sheets keep the explored-pathway safety net (run3
    # case3, pinned by test). A safety net the caller supplied via
    # summary is the caller's explicit list and is restored after the
    # fallback (pinned by test_summary_list_used_when_no_pathway).
    if abstained and not chief_matches_pathway(str(state.get("chief_complaint") or "")):
        sheet = apply_neutral_fallback(sheet)
        summary_alerts = (summary or {}).get("return_now_if")
        if summary_alerts:
            sheet["return_now_if"] = list(summary_alerts)
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
    elif level == "routine_visit" and window:
        main = f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
    elif level == "routine_visit":
        main = "可以在家先观察；请尽快到线下医疗机构就诊评估。"
    elif level == "self_care":
        main = "可以先在家观察，暂时不需要专门跑一趟医院。"
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
