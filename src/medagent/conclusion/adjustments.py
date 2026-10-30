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

from .constants import ABSTAIN_TEXT, BANNED_PHRASES, ORAL_CHIEF_TERMS, PATHWAY_GROUPS, RETURN_NOW_IF, UNCERTAINTY_NOTE, VISIT_NOTES_CHIEF_MAX_CHARS, VISIT_NOTES_EVIDENCE_MAX, VISIT_NOTES_MAX, _CHRONIC_OUTLOOK, _G12_WORSENING_MAP, _INFANT_OBSERVABLE_DO, _INFANT_OBSERVABLE_MEASURE, _INFANT_WORSENING, _SHORT_TERM_OUTLOOK_MARKERS

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


def _self_care_group_key(sheet_group: str, chief_complaint: str) -> str:
    """Self-care selection key for a sheet.

    Only when the sheet group is ``generic`` and the chief complaint
    verbatim contains an oral term does the self-care selection take
    ``oral`` (whose ``oral`` group is defined in self_care.json).
    Every other group variable
    keeps ``sheet_group`` — callers must not reuse this key for the
    narrative, safety net, department hint, or outlook's group logic
    beyond the self-care group's own course (outlook reads the
    selected self-care group's course, so it follows this key).
    """
    chief = chief_complaint or ""
    if sheet_group == "generic" and any(term in chief for term in ORAL_CHIEF_TERMS):
        return "oral"
    return sheet_group


def _build_visit_notes(
    state: CaseState,
    directions: List[Dict[str, Any]],
    abstained: bool,
    worry: Optional[Dict[str, str]],
) -> List[str]:
    """Visit-notes checklist (design §13.4): already-recorded facts
    only, in order — ① chief complaint verbatim (truncated to 80 chars
    plus … when longer, never rewritten); ② worry object verbatim when
    present; ③ age (birth year converted) and chronic-condition names
    verbatim, only when a patient archive is bound
    (``state.profile_baseline`` non-empty); ④ direction support quotes
    deduplicated, at most 3 — omitted entirely when the sheet abstains
    on directions. Unbound sessions carry only ①②④. Total cap 8
    items; entries are patient/fact verbatim, so the sheet lint treats
    them like support_quotes (see compose_conclusion)."""
    notes: List[str] = []
    chief = str(state.get("chief_complaint") or "").strip()
    if chief:
        if len(chief) > VISIT_NOTES_CHIEF_MAX_CHARS:
            chief = chief[:VISIT_NOTES_CHIEF_MAX_CHARS] + "…"
        notes.append(chief)
    obj = worry.get("object") if isinstance(worry, dict) else None
    if obj and str(obj).strip():
        notes.append(str(obj).strip())
    baseline = getattr(state, "profile_baseline", None) or {}
    if isinstance(baseline, dict):
        birth_year = baseline.get("birth_year")
        if isinstance(birth_year, int) and not isinstance(birth_year, bool):
            age = datetime.now().year - birth_year
            if 0 <= age <= 120:
                notes.append(f"年龄：{age}岁")
        for name in baseline.get("chronic_conditions") or []:
            if isinstance(name, str) and name.strip():
                notes.append(name.strip())
    if not abstained:
        seen = set()
        evidence: List[str] = []
        for direction in directions or []:
            for quote in direction.get("support_quotes") or []:
                quote_text = str(quote).strip()
                if quote_text and quote_text not in seen:
                    seen.add(quote_text)
                    evidence.append(quote_text)
                    if len(evidence) >= VISIT_NOTES_EVIDENCE_MAX:
                        break
            if len(evidence) >= VISIT_NOTES_EVIDENCE_MAX:
                break
        notes.extend(evidence)
    return notes[:VISIT_NOTES_MAX]


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
    ``directions_visible`` (full mode only) and
    ``directions_restricted_visible`` (restricted or full); the sheet
    records the mode in ``directions_mode`` (off | restricted | on,
    default restricted, design §13.3); ``narrative.reasoning`` shares
    the full gate only; ``disposition.department_hint`` is always
    computed, gated by ``department_visible`` (department variable on
    always shows, off never shows, unset follows the directions mode);
    ``narrative.framing`` / ``severity``, ``disposition.text``,
    ``gaps``, ``self_care``, ``return_now_if``, ``worry``,
    ``goal_response``, ``outlook`` and ``follow_up`` are ungated and
    always renderable. New additive fields (``directions_mode``,
    ``visit_notes``) are optional: a missing flag still means
    invisible (fail-closed), and a missing ``visit_notes`` means no
    visit-notes section.
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
    # v1.3 oral routing (design §13.7): only the self-care selection
    # key may become "oral"; sheet_group itself — and therefore the
    # narrative group, safety-net group and department group below —
    # is untouched.
    care = (
        _self_care_group(
            _self_care_group_key(
                sheet_group, str(state.get("chief_complaint") or "")
            ),
            tier,
        )
        or {}
    )
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
        "directions_mode": directions_mode(),
        "department_visible": department_visible(),
        "visit_notes": _build_visit_notes(state, directions, abstained, worry),
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
        # D1 (v1.3): restore the oral self-care group after the
        # fallback, under the same condition as the pre-fallback
        # selection (reused helper, no second keyword list). The
        # fallback guards against probed-pathway language, but oral
        # self-care matches the chief complaint itself, not that
        # failure mode — so only self-care and its course (outlook)
        # are restored. The neutral narrative, the generic safety
        # net, and every other field stay exactly as the fallback
        # left them, including allergy_hint handling: the restored
        # self-care carries over whatever allergy_hint the fallback
        # result already has, and introduces none of its own.
        if _self_care_group_key(
            sheet_group, str(state.get("chief_complaint") or "")
        ) == "oral":
            oral_care = _self_care_group("oral", tier) or {}
            if oral_care.get("match") == "oral":
                previous_care = sheet.get("self_care") or {}
                sheet["self_care"] = {
                    "do": list(oral_care.get("do", [])),
                    "avoid": list(oral_care.get("avoid", [])),
                    "measure": list(oral_care.get("measure", [])),
                    **(
                        {"allergy_hint": previous_care["allergy_hint"]}
                        if previous_care.get("allergy_hint")
                        else {}
                    ),
                }
                if oral_care.get("course"):
                    sheet["outlook"] = oral_care["course"]
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
    # visit_notes entries are patient/fact verbatim too (design §13.4)
    # and are excluded the same way; section titles and other system
    # wording still pass through the lint normally.
    lint_view = deepcopy(sheet)
    for direction in lint_view.get("directions", []):
        direction.pop("support_quotes", None)
    lint_view.pop("visit_notes", None)
    rendered = json.dumps(lint_view, ensure_ascii=False)
    if any(phrase in rendered for phrase in BANNED_PHRASES):
        raise ValueError("conclusion sheet failed the banned-phrase lint")
    return sheet

from .disposition import _disposition, _gaps, format_window_text
from .narrative import _build_goal_response, _build_narrative, _build_worry, _narrative_templates
from .sheet import _explored_pathway_group, _load_json, _load_json_optional, _pathway_return_now_if, _self_care_group, apply_neutral_fallback, chief_matches_pathway, department_visible, directions_mode, directions_restricted_visible, directions_visible
from .support import _adjust_assertion_wording, _is_infant_case, _is_undetermined_unknown_screen, _onset_is_chronic_gradual, _select_directions, _sheet_evidence_count
