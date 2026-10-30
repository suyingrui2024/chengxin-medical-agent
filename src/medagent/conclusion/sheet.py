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

from .constants import DETAIL_FACT_GROUPS, PATHWAY_GROUPS, RETURN_NOW_IF, _cache

def sheet_mode() -> str:
    """off | shadow | on — env override, default on (design §8.3/§9A)."""
    mode = os.environ.get("MEDAGENT_CONCLUSION_SHEET", "on").strip().lower()
    return mode if mode in ("off", "shadow", "on") else "on"


def directions_mode() -> str:
    """off | restricted | on — env override, default restricted
    (design §13.3: v1.3 flips the phase-A shadow default to restricted
    visibility; an unrecognised value falls back to the default)."""
    mode = (
        os.environ.get("MEDAGENT_CONCLUSION_DIRECTIONS", "restricted")
        .strip()
        .lower()
    )
    return mode if mode in ("off", "restricted", "on") else "restricted"


def directions_visible() -> bool:
    """Full-visibility gate only: true exactly when the directions mode
    is ``on`` (design §13.3 — this meaning is unchanged from phase A;
    only the default mode moved from off to restricted).

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
    return directions_mode() == "on"


def directions_restricted_visible() -> bool:
    """Restricted-or-full gate (design §13.2/§13.3): true when the
    directions mode is ``restricted`` (the default) or ``on``. This is
    the gate for the restricted directions section; the full reasoning
    lines still require :func:`directions_visible`."""
    return directions_mode() in ("restricted", "on")


def department_visible() -> bool:
    """Department-hint gate (design §13.3): ``MEDAGENT_CONCLUSION_DEPARTMENT``
    ``on`` always shows, ``off`` never shows (operator kill switch,
    overriding the directions mode); when the variable is unset (or
    unrecognised) the hint follows the directions mode — shown unless
    the directions mode is ``off``.

    Rendering single source (modular-refactor design §4.4, frozen):
    same contract as :func:`directions_visible` — the composer is the
    ONLY place that computes this bool, ``disposition.department_hint``
    is always computed, and renderers show it only when this flag is
    true (missing flag = invisible, fail-closed).
    """
    dept = os.environ.get("MEDAGENT_CONCLUSION_DEPARTMENT", "").strip().lower()
    if dept == "on":
        return True
    if dept == "off":
        return False
    return directions_mode() != "off"


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

from .adjustments import compose_conclusion
from .disposition import emergency_explanation
from .narrative import _narrative_templates
