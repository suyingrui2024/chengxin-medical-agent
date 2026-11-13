"""Question candidate filtering and ordering interfaces.

HypothesisOrdering reuses HypothesisRanker; DifferentialOrdering shares the
planner's scoring. Their algorithms remain distinct. These strategies only
order candidates: the calling planner owns phase selection, state updates,
task offers and completion. Injected rankers retain their choose() behavior.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Protocol

from ..core.domain import CaseState
from .hypothesis_ranking import HypothesisRanker
from .knowledge import Question, question_fact_gates_pass  # noqa: F401  (re-exported for callers adapting gates)
from .reasoner import OrderedQuestionRanker
from .reasoning import (
    ReasoningWorkspace,
    DifferentialPlanner,
    potential_discrimination,
    load_pathways,
)
from ..core.phase_graph import planning_rank

class QuestionOrdering(Protocol):
    """Unified question-ordering interface (design doc §6.1).

    All strategies share three invariants: the input candidate set is
    already filtered (``order``/``choose`` never filter); an empty set
    yields ``[]`` / ``None``; and the caller — not the strategy —
    owns the fallback (``_plan_next`` falls back to ``same_phase[0]``,
    the adaptive check passes through). ``DifferentialOrdering``
    additionally requires workspace context (see its docstring).
    """

    name: str

    def order(self, state: CaseState, candidates: List[Question]) -> List[str]:
        """Full id order of ``candidates`` (best first)."""
        ...

    def choose(
        self, state: CaseState, candidates: List[Question]
    ) -> Optional[str]:
        """``order(...)[0]``, or ``None`` for an empty set."""
        ...


# ----------------------------------------------------------------------
# Shared scaffold (verbatim from ConsultationEngine._plan_next).
# ----------------------------------------------------------------------


def filter_candidates(
    questions: List[Question],
    state: CaseState,
    applies: Callable[[Question, CaseState], bool],
    gates: Callable[[Question, CaseState], bool],
) -> List[Question]:
    """Shared filter predicate, verbatim semantics of ``_plan_next``:

    a question is a candidate iff its ``fact_key`` is not yet recorded,
    ``applies`` holds, and ``gates`` holds. Callers pass their own
    ``applies``/``gates`` callables (see module docstring: the two
    gate implementations are not merged here).
    """
    return [
        question
        for question in questions
        if question.fact_key not in state.facts
        and applies(question, state)
        and gates(question, state)
    ]


def earliest_phase_candidates(candidates: List[Question]) -> List[Question]:
    """Shared phase step: the candidates in the minimum ``planning_rank``
    phase, in passed order. Empty input yields ``[]`` (the historical
    ``_plan_next`` only reached this step with blocking candidates, so
    this guard changes no existing path).
    """
    if not candidates:
        return []
    earliest_phase = min(planning_rank(item.phase) for item in candidates)
    return [
        item for item in candidates if planning_rank(item.phase) == earliest_phase
    ]


class CatalogOrdering:
    """Catalog-order strategy: return the passed order unchanged.

    This is the ``RulePlanner``/``_plan_next`` same-phase behaviour for
    non-HISTORY phases, the ``OrderedQuestionRanker`` behaviour, and
    the ``_plan_next`` fallback target (``same_phase[0]``) expressed as
    a strategy. It scores nothing.
    """

    name = "catalog"

    def order(self, state: CaseState, candidates: List[Question]) -> List[str]:
        return [question.id for question in candidates]

    def choose(
        self, state: CaseState, candidates: List[Question]
    ) -> Optional[str]:
        ordered = self.order(state, candidates)
        return ordered[0] if ordered else None


class HypothesisOrdering(HypothesisRanker):
    """The shared hypothesis ranker under the question-ordering interface."""

    name = "hypothesis"


class DifferentialOrdering:
    """Order filtered candidates using the differential planner's scoring.

    The caller supplies the validated workspace, changed hypotheses and
    potential counts. Both order() and choose() preserve input order on ties.
    Scoring, weights and pathway counting share the planner's implementation;
    workspace mutation, task offers and completion remain with the planner.
    """

    name = "differential"

    W_RED_FLAG = DifferentialPlanner.W_RED_FLAG
    W_DISCRIMINATION = DifferentialPlanner.W_DISCRIMINATION
    W_REQUIRED = DifferentialPlanner.W_REQUIRED
    W_FRESHNESS = DifferentialPlanner.W_FRESHNESS

    def __init__(
        self, pathways: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Use the planner's pathway source, or explicitly supplied test pathways."""
        self._pathways = pathways if pathways is not None else load_pathways()

    def potential_discrimination(self, state: CaseState) -> Dict[str, int]:
        return potential_discrimination(state, self._pathways)

    score = DifferentialPlanner.score

    def _require_workspace(self, workspace, changed_ids):
        if workspace is None:
            raise ValueError(
                "DifferentialOrdering requires a workspace context "
                "(see DifferentialOrdering docstring); refusing to "
                "degrade silently to catalog order."
            )
        return changed_ids if changed_ids is not None else set()

    def order(
        self,
        state: CaseState,
        candidates: List[Question],
        workspace: Optional[ReasoningWorkspace] = None,
        changed_ids: Optional[set] = None,
        potential_counts: Optional[Dict[str, int]] = None,
    ) -> List[str]:
        changed = self._require_workspace(workspace, changed_ids)
        # Same (score, -index) key as the max pick: earlier candidates
        # win ties, exactly as max() with a negative-index tie-break.
        keyed = sorted(
            enumerate(candidates),
            key=lambda pair: (
                -self.score(pair[1], workspace, changed, potential_counts),
                pair[0],
            ),
        )
        # NOTE: sort key uses (score desc, index asc), which is the
        # same total order as max() over (score, -index); choose()
        # below still uses the verbatim max expression.
        return [question.id for _, question in keyed]

    def choose(
        self,
        state: CaseState,
        candidates: List[Question],
        workspace: Optional[ReasoningWorkspace] = None,
        changed_ids: Optional[set] = None,
        potential_counts: Optional[Dict[str, int]] = None,
    ) -> Optional[str]:
        if not candidates:
            return None
        changed = self._require_workspace(workspace, changed_ids)
        # Verbatim selection expression from DifferentialPlanner.plan:
        best = max(
            candidates,
            key=lambda q: (
                self.score(q, workspace, changed, potential_counts),
                -candidates.index(q),
            ),
        )
        return best.id


class _DelegateOrdering:
    """Compatibility shim for injected rankers of unknown type.

    Only used by :func:`ordering_for_ranker` when the injected ranker is
    neither a ``HypothesisRanker`` nor an ``OrderedQuestionRanker``
    (e.g. a test double implementing the ``QuestionRanker`` protocol).
    It delegates ``choose`` verbatim and derives ``order`` from the
    ranker's own ``order`` when present. It is not a scoring strategy
    and must not be used to paper over a real strategy choice.
    """

    name = "delegate"

    def __init__(self, ranker: Any) -> None:
        self._ranker = ranker

    def order(self, state: CaseState, candidates: List[Question]) -> List[str]:
        order = getattr(self._ranker, "order", None)
        if callable(order):
            return list(order(state, candidates))
        pick = self._ranker.choose(state, candidates)
        return [pick] if pick is not None else []

    def choose(
        self, state: CaseState, candidates: List[Question]
    ) -> Optional[str]:
        return self._ranker.choose(state, candidates)


def ordering_for_ranker(ranker: Any) -> Any:
    """Map an engine's injected ranker to its new-module strategy.

    * ``HypothesisRanker`` → ``HypothesisOrdering`` carrying the same
      ``season_override`` (so a test engine pinned to spring/winter
      keeps its season; production engines use the default, i.e. the
      current date, exactly as before).
    * ``OrderedQuestionRanker`` → ``CatalogOrdering`` (its
      ``choose`` is ``candidates[0].id`` — identical to catalog order).
    * Anything else → a delegating shim that calls the injected
      ranker's own ``choose`` (so unknown test doubles keep working
      unchanged, and a ``None`` ranker still fails exactly where the
      old direct call failed).

    This mapping is what lets ``_plan_next`` run through the new
    interface without changing the result for any injected ranker.
    """
    if isinstance(ranker, HypothesisRanker):
        return HypothesisOrdering(
            season_override=getattr(ranker, "_season_override", None)
        )
    if isinstance(ranker, OrderedQuestionRanker):
        return CatalogOrdering()
    return _DelegateOrdering(ranker)
