"""Commit patient-visible consultation phase changes in one audited place."""

from __future__ import annotations

from uuid import uuid4

from .domain import CaseState, Phase
from .events import Event, EventStore


def commit_phase(
    events: EventStore,
    state: CaseState,
    reason: str,
    turn_id: str | None,
) -> str | None:
    """Record a committed phase transition once a reply is ready to publish.

    Internal preparation may temporarily alter a case while handling an action.
    This function records only the phase that actually becomes patient-visible,
    so the audit trail cannot claim an intermediate transition that was never
    delivered to the patient.
    """
    if not isinstance(state.phase, Phase):
        raise ValueError("case phase must be a registered Phase")
    previous = state.committed_phase
    if previous == state.phase:
        return None
    transition_id = str(uuid4())
    events.append(
        Event(
            state.session_id,
            "case.phase_transition",
            {
                "transition_id": transition_id,
                "from": previous.value if previous else None,
                "to": state.phase.value,
                "reason": reason,
                "state_revision_before_commit": state.revision,
            },
            turn_id=turn_id,
        )
    )
    state.committed_phase = state.phase
    return transition_id
