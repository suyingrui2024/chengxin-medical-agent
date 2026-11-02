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

This module is the package's public surface and nothing else. It used to be
the composer itself; when that body moved into the sibling modules, every
import it had made came along, and 118 names stayed bound here long after
nothing in the file read them -- `from medagent.conclusion import CaseState`
answered, which suggested the conclusion package owned `CaseState`. What is
listed below is what callers actually import; `__all__` states it, so a name
cannot become public by being imported once.
"""

from __future__ import annotations

from .constants import (
    ABSTAIN_TEXT,
    BANNED_PHRASES,
    CERTAINTY_TEMPLATES,
    RETURN_NOW_IF,
    UNCERTAINTY_NOTE,
    _cache,
    _CHRONIC_OUTLOOK,
    _GOAL_PART_FIXED_HANDLING,
)
from ..core.text_match import find_unnegated_certain

from .adjustments import (
    _adjust_chronic_outlook,
    _apply_infant_observable,
    _build_visit_notes,
    _self_care_group_key,
    compose_conclusion,
)
from .disposition import (
    _gaps,
    emergency_explanation,
    format_window_text,
    spoken_close_line,
)
from .goals import (
    _keyword_addressed,
    _part_keywords,
    _visit_part_residual,
    goal_part_handling,
    split_goal_parts,
    unaddressed_goal_parts,
    unaddressed_urgent_goal_parts,
)
from .assets import _narrative_templates
from .narrative import _build_goal_response, _build_worry
from ..clinical.reasoning import chief_matches_pathway
from .sheet import (
    _load_json,
    _self_care_group,
    apply_neutral_fallback,
    department_visible,
    directions_mode,
    directions_restricted_visible,
    directions_visible,
    sheet_mode,
)
from .support import (
    _adjust_assertion_wording,
    _against_note,
    _select_directions,
    _sheet_evidence_count,
    _valid_support_quote,
)

__all__ = [
    "ABSTAIN_TEXT",
    "BANNED_PHRASES",
    "CERTAINTY_TEMPLATES",
    "RETURN_NOW_IF",
    "UNCERTAINTY_NOTE",
    "_CHRONIC_OUTLOOK",
    "_GOAL_PART_FIXED_HANDLING",
    "_adjust_assertion_wording",
    "_adjust_chronic_outlook",
    "_against_note",
    "_apply_infant_observable",
    "_build_goal_response",
    "_build_visit_notes",
    "_build_worry",
    "_cache",
    "_gaps",
    "_keyword_addressed",
    "_load_json",
    "_narrative_templates",
    "_part_keywords",
    "_select_directions",
    "_self_care_group",
    "_self_care_group_key",
    "_sheet_evidence_count",
    "_valid_support_quote",
    "_visit_part_residual",
    "apply_neutral_fallback",
    "chief_matches_pathway",
    "compose_conclusion",
    "department_visible",
    "directions_mode",
    "directions_restricted_visible",
    "directions_visible",
    "emergency_explanation",
    "find_unnegated_certain",
    "format_window_text",
    "goal_part_handling",
    "sheet_mode",
    "split_goal_parts",
    "spoken_close_line",
    "unaddressed_goal_parts",
    "unaddressed_urgent_goal_parts",
]
