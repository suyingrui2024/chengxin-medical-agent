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

from .constants import (RETURN_NOW_IF, BANNED_PHRASES, CERTAINTY_TEMPLATES, ABSTAIN_TEXT, PATHWAY_GROUPS, PRIOR_RANK, GAP_IMPACTS, DEFAULT_GAP_IMPACT, MEASUREMENT_FACTS, _cache, DETAIL_FACT_GROUPS, _ENCODING_QUOTES, _AGAINST_LABEL_OVERRIDES, _REFUSAL_EVIDENCE_MARKERS, _QUALIFIED_MARKERS, _OUTLOOK_QUALIFIER_TOKENS, _GENERIC_WORD_FRAGMENTS, _SHORT_TERM_OUTLOOK_MARKERS, _CHRONIC_OUTLOOK, _INFANT_OBSERVABLE_DO, _INFANT_OBSERVABLE_MEASURE, _INFANT_WORSENING, _VISIT_GOAL_KEYWORDS, _WORRY_FALLBACK_OBJECT, _GOAL_PART_PREFIXES, _GOAL_DRUG_TREATMENT_WORDS, _GOAL_PART_FIXED_HANDLING, _CHIEF_CHUNK_STOPWORDS, _GOAL_VISIT_PART_KEYWORDS, UNCERTAINTY_NOTE, _G12_WORSENING_MAP, ORAL_CHIEF_TERMS, VISIT_NOTES_CHIEF_MAX_CHARS, VISIT_NOTES_EVIDENCE_MAX, VISIT_NOTES_MAX)
from .adjustments import (_adjust_chronic_outlook, _apply_infant_observable, _apply_eye_generic_worsening, _department_hint, _apply_undetermined_safety, _self_care_group_key, _build_visit_notes, compose_conclusion)
from .disposition import (_is_refusal_evidence, _gap_label, _gaps, format_window_text, _disposition, spoken_close_line, emergency_explanation)
from .goals import (split_goal_parts, _part_keywords, _keyword_addressed, _visit_part_residual, unaddressed_goal_parts, unaddressed_urgent_goal_parts, goal_part_handling)
from .narrative import (_narrative_templates, _narrative_reasoning, _build_narrative, _contains_banned, _worry_tail, _build_worry, _build_goal_response)
from .sheet import (sheet_mode, directions_mode, directions_visible, directions_restricted_visible, department_visible, _load_json, _load_json_optional, _patient_labels, _self_care_group, _explored_pathway_group, _pathway_return_now_if, chief_matches_pathway, apply_neutral_fallback)
from .support import (_is_pure_number, _valid_support_quote, _certainty, _against_note, _select_directions, _strip_qualifiers, _denial_remainders, _denied_texts, _adjust_assertion_wording, _onset_is_chronic_gradual, _is_infant_case, _is_undetermined_unknown_screen, _hypothesis_support_terms, _sheet_evidence_count)
