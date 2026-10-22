"""Planning strategies for the consultation pipeline (design doc §4.4).

The pipeline's terminal handler delegates to a ``Planner``. Today the only
production planner is ``RulePlanner``, which preserves the historical
``ConsultationEngine._plan_next`` behaviour. The adaptive (model-driven)
planner and the differential-diagnosis planner (design phase 3) implement
the same protocol and are swapped in at engine construction, so the safety
and intake parts of the pipeline exist exactly once.
"""
from __future__ import annotations

from typing import Protocol

from .domain import AgentReply, CaseState


class Planner(Protocol):
    name: str

    def plan(self, state: CaseState) -> AgentReply:
        """Produce the next reply for a turn that reached planning."""
        ...


class RulePlanner:
    """Deterministic planner: earliest-phase unanswered question first."""

    name = "rule"

    def __init__(self, engine) -> None:
        self._engine = engine

    def plan(self, state: CaseState) -> AgentReply:
        return self._engine._plan_next(state)
