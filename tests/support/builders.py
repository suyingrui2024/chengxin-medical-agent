"""Shared builders for the test suite.

Before this module existed, 30 test files each rebuilt the same two things:

* a ``Fact`` (10 near-identical ``_fact`` / ``fact`` helpers), and
* a CLOSED ``CaseState`` with a recorded chief complaint (13 near-identical
  ``closed_state`` / ``_closed_state`` helpers).

The repetition was not gratuitous — the variants genuinely differ in
``session_id``, whether they append to ``transcript``, and how they handle
extra facts.  So this module owns only the part that is *identical* everywhere
(the closed-state preamble and the fact shape); callers keep their own
signature and add whatever their file needs on top.

Deliberately not included: the one-line ``_fact`` wrappers.  They differ in
argument *order* (``(key, value, evidence, assertion)`` vs ``(key, value,
assertion, evidence)``), so folding them into one signature would silently
swap positional arguments at the call sites — for a saving of about ten lines.
"""
from __future__ import annotations

from medagent.core.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)

__all__ = ["add_fact", "closed_state", "make_fact"]


def make_fact(
    key: str,
    value=None,
    *,
    assertion: Assertion = Assertion.PRESENT,
    evidence=None,
    turn_id: str = "t1",
) -> Fact:
    """A patient-sourced fact.

    ``evidence`` defaults to ``str(value)``: several assertions run
    ``fact.evidence.strip()``, so a numeric value (a temperature, say) must not
    leak through as an ``int``.
    """
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id=turn_id,
        evidence=(
            evidence
            if evidence is not None
            else (str(value) if value is not None else None)
        ),
    )


def add_fact(
    state: CaseState,
    key: str,
    value=None,
    assertion: Assertion = Assertion.PRESENT,
    evidence=None,
) -> Fact:
    """Record a patient-sourced fact on ``state`` and return it.

    ``turn_id`` is ``t2`` because these helpers record facts *after* the chief
    complaint (``t1``), which is what every caller's original local copy did.
    """
    fact = make_fact(key, value, assertion=assertion, evidence=evidence, turn_id="t2")
    state.record(fact)
    return fact


def closed_state(
    chief: str = "发烧两天",
    risk: RiskLevel = RiskLevel.ROUTINE,
    reason: str = "confirmed_by_user",
    *,
    session_id: str = "unit-test",
    transcript: bool = True,
) -> CaseState:
    """A CLOSED ``CaseState`` with the chief complaint already recorded.

    This is the shared preamble: closed phase, close reason, risk level, and a
    PRESENT ``chief_complaint`` fact whose evidence is the chief text itself.
    Callers that need extra facts, or that must not touch ``transcript``, pass
    the flags and append afterwards.
    """
    state = CaseState(session_id=session_id)
    state.phase = Phase.CLOSED
    state.closed_reason = reason
    state.risk = RiskAssessment(risk)
    state.record(
        Fact(
            key="chief_complaint",
            value=chief,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=chief,
        )
    )
    if transcript:
        state.transcript.append({"role": "user", "content": chief})
    return state
