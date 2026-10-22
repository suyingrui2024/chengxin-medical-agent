"""Consultation phase graph as data (design doc §4.4).

``PLANNING_RANK`` preserves the historical question-ordering semantics of
``ConsultationEngine._phase_rank`` exactly (observation/summary questions
rank last). ``ALLOWED_TRANSITIONS`` documents the legal patient-visible
phase moves, including the safety back-edge: any phase may re-enter
``emergency_screen`` when a turn escalates, and any phase may close.
The graph is descriptive for now; enforcement lands with the pipeline's
transition tests rather than in the commit path, so existing behaviour
cannot be broken by the graph itself.
"""
from __future__ import annotations

from typing import Dict, FrozenSet

from .domain import Phase

PLANNING_RANK: Dict[Phase, int] = {
    Phase.OPENING: 0,
    Phase.EMERGENCY_SCREEN: 1,
    Phase.HISTORY: 2,
    Phase.BACKGROUND: 3,
}

_MAIN_CHAIN = (
    Phase.OPENING,
    Phase.EMERGENCY_SCREEN,
    Phase.HISTORY,
    Phase.BACKGROUND,
    Phase.OBSERVATION,
    Phase.SUMMARY,
    Phase.CLOSED,
)

ALLOWED_TRANSITIONS: Dict[Phase, FrozenSet[Phase]] = {
    phase: frozenset(
        ({_MAIN_CHAIN[i + 1]} if i + 1 < len(_MAIN_CHAIN) else set())
        | {Phase.EMERGENCY_SCREEN, Phase.CLOSED, phase}
    )
    for i, phase in enumerate(_MAIN_CHAIN)
}
# Corrections and back-filling legitimately move between history and
# background in both directions.
ALLOWED_TRANSITIONS[Phase.HISTORY] |= {Phase.BACKGROUND}
ALLOWED_TRANSITIONS[Phase.BACKGROUND] |= {Phase.HISTORY}


def planning_rank(phase: Phase) -> int:
    return PLANNING_RANK.get(phase, 9)


def can_transition(source: Phase, target: Phase) -> bool:
    return target in ALLOWED_TRANSITIONS.get(source, frozenset())
