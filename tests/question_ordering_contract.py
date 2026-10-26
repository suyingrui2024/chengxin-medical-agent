"""Tests-side question-order contract helper (modular-refactor package D).

Implements design doc §6.4 ``expected_question_order`` as a *tests-side*
thin helper (the design explicitly allows the helper to live in tests):
the fixture first declares which engine it means, then reads the
expected order. It never guesses the engine from the state.

Engine kinds (fail-closed, no default):

- ``"rule"``: the ``MEDAGENT_PLANNER=rule`` rollback path — shared
  filter + earliest phase + ``HypothesisOrdering`` (the strategy
  ``_plan_next`` runs through ``ordering_for_ranker``).
- ``"differential"``: the default path — shared filter + earliest
  phase + ``DifferentialOrdering`` with a *read-only* workspace
  (``propose`` + ``validate_workspace`` + previous-workspace diff,
  exactly the inputs ``DifferentialPlanner._refresh_workspace``
  computes, but without writing ``state.reasoning`` or appending
  ``reasoning.*`` events).

Refusals (deliberate, not gaps):

- ``"adaptive"`` (or anything else) raises ``ValueError``: the
  adaptive check in ``agent.py`` runs over the *full* candidate set
  without phase-splitting (§2.2(e)), so a phased order would model it
  wrong. The adaptive check's own fact-gate implementation
  (``_fact_gates_pass``) is design §10待核实2 — its equivalence with
  the deterministic ``question_fact_gates_pass`` is unverified, so
  this helper models the deterministic paths only and says so.

Scope limits (callers must respect them; they mirror ``_plan_next`` /
``DifferentialPlanner.plan`` structure, not a new decision):

- The helper returns the *candidate order* of the earliest phase. It
  does not decide the BACKGROUND ``_offer_task`` (a planner decision
  when ``same_phase[0]`` is BACKGROUND), the G-12(d) discriminator,
  the abstention branch, or ``_finish`` — those run only when no
  blocking candidate remains, which is outside ordering.
- The helper never filters differently from the engines: unrecorded
  ``fact_key`` + ``question_applies_in_state`` + deterministic fact
  gates, verbatim from both planners' candidate comprehensions.

Requires package A: ``medagent.question_ordering`` must exist (the
helper imports the strategies, it does not re-implement scoring).
"""

from __future__ import annotations

from typing import List, Optional

from medagent.knowledge import question_fact_gates_pass
from medagent.question_ordering import (
    DifferentialOrdering,
    HypothesisOrdering,
    earliest_phase_candidates,
    filter_candidates,
)
from medagent.reasoning import (
    ReasoningWorkspace,
    RuleBasedProposer,
    validate_workspace,
)

ENGINE_KINDS = ("rule", "differential")


def expected_question_order(
    engine_kind: str,
    state,
    content,
    season_override=None,
) -> List[str]:
    """Return the engine's candidate order for ``state`` (§6.4).

    ``engine_kind`` must be ``"rule"`` or ``"differential"``; anything
    else raises ``ValueError`` rather than silently picking a strategy.
    ``content`` is the engine's content repository (``engine.content``):
    its ``.questions`` supply the candidates and its
    ``.question_applies_in_state`` the applies predicate.
    """
    if engine_kind not in ENGINE_KINDS:
        raise ValueError(
            "expected_question_order requires an explicit engine_kind "
            f"in {ENGINE_KINDS}; got {engine_kind!r}. Refusing to guess: "
            "a rule order must never be asserted against a differential "
            "engine or vice versa."
        )
    candidates = filter_candidates(
        content.questions,
        state,
        applies=content.question_applies_in_state,
        gates=lambda question, st: question_fact_gates_pass(
            question, st.facts
        ),
    )
    same_phase = earliest_phase_candidates(candidates)
    if not same_phase:
        return []
    if engine_kind == "rule":
        return HypothesisOrdering(season_override=season_override).order(
            state, same_phase
        )
    # Differential: read-only workspace, mirroring
    # DifferentialPlanner._refresh_workspace's inputs without its writes.
    ordering = DifferentialOrdering()
    proposer = RuleBasedProposer(
        question_fact_keys={q.id: q.fact_key for q in content.questions}
    )
    workspace, _rejected = validate_workspace(state, proposer.propose(state))
    previous = ReasoningWorkspace.from_dict(state.reasoning)
    old_status = {h.id: h.status for h in previous.hypotheses.values()}
    changed_ids = {
        h.id for h in workspace.hypotheses.values() if old_status.get(h.id) != h.status
    }
    return ordering.order(
        state,
        same_phase,
        workspace=workspace,
        changed_ids=changed_ids,
        potential_counts=ordering.potential_discrimination(state),
    )


def expected_next_question(
    engine_kind: str,
    state,
    content,
    season_override=None,
) -> Optional[str]:
    """``expected_question_order(...)[0]``, or ``None`` when empty."""
    ordered = expected_question_order(
        engine_kind, state, content, season_override=season_override
    )
    return ordered[0] if ordered else None
