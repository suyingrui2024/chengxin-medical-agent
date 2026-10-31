"""Shared question-ordering scaffold + strategies (modular-refactor package A).

Design doc: ``docs/模块化重整设计文档.md`` §2 (call-site map) and §6.1
(interface). This module is a *move*, not a redesign:

* The shared scaffold (candidate filtering, earliest-phase selection,
  caller-side fallback) is the code ``ConsultationEngine._plan_next``
  already ran, lifted verbatim into helpers. The BACKGROUND
  ``_offer_task`` / suspend-cap wrapping stays with the planners (the
  §2.2 difference-1 between ``_plan_next`` and ``DifferentialPlanner``
  is NOT flattened here).
* The three strategies keep their own scoring, verbatim, and are never
  merged. ``CatalogOrdering`` returns passed order; ``HypothesisOrdering``
  is ``hypothesis_ranking.HypothesisRanker`` moved here (HISTORY groups
  + ``q_score`` only, season/age weights still all 1.0 pending clinical
  sign-off); ``DifferentialOrdering`` is ``reasoning.DifferentialPlanner``
  scoring moved here (3.0/2.0/1.0/0.5 + ``max`` with negative-index
  tie-break). Whether the two scorings ever converge is a follow-up
  requiring a separate A/B evaluation and user sign-off (§2.4) — this
  package does not merge them, does not change either path's output
  order, and does not flip any default.
* Wiring status in this package: ``ConsultationEngine._plan_next`` (the
  ``MEDAGENT_PLANNER=rule`` rollback path) now runs through
  :func:`filter_candidates`, :func:`earliest_phase_candidates` and
  :func:`ordering_for_ranker`. The default ``DifferentialPlanner.plan``
  path in ``reasoning.py`` is deliberately NOT rewired here (that file
  is outside this package's file lock); ``DifferentialOrdering`` is
  extracted and equivalence-checked so the later one-line wiring can
  land without re-deriving the scoring. The adaptive-engine check in
  ``agent.py`` likewise keeps calling the injected ranker directly in
  this package; it must keep receiving the full (un-phased) candidate
  set when it is eventually switched over (§2.2(e)).
* Strategies never filter, never change phase order, never add/remove
  candidates, never write state, and never emit events. Workspace
  refresh and ``reasoning.*`` events stay in the reasoning layer.
* The two existing fact-gate implementations (deterministic
  ``knowledge.question_fact_gates_pass(question, facts)`` vs the
  adaptive engine's ``_fact_gates_pass(state, question)``) are NOT
  merged here: doc §6.1 lists their equivalence as still to verify, so
  :func:`filter_candidates` takes the gate predicate as a caller-supplied
  callable in ``(question, state)`` shape and each caller adapts its own
  gate. No gate semantics are changed by this move.
"""

from __future__ import annotations

import json
import re
from datetime import date
from importlib import resources
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from .conclusion import PRIOR_RANK, chief_matches_pathway
from .domain import CaseState, Phase, RiskLevel
from .hypothesis_ranking import HypothesisRanker
from .knowledge import Question, question_fact_gates_pass  # noqa: F401  (re-exported for callers adapting gates)
from .reasoner import OrderedQuestionRanker
from .reasoning import (
    ReasoningWorkspace,
    RuleBasedProposer,
    load_pathways,
)
from .phase_graph import planning_rank
from .text_match import contains_unnegated

try:  # P3 module lands in parallel; absence must not break ranking.
    from .out_of_pathway import warning_questions as _warning_questions
except ImportError:  # pragma: no cover - P3 not landed yet
    _warning_questions = None


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


# ----------------------------------------------------------------------
# Hypothesis strategy: verbatim move of hypothesis_ranking.HypothesisRanker.
# Only the class name changed; every method body below is identical to
# the original (verified by AST comparison in the package-A report).
# The original module is left untouched in this package.
# ----------------------------------------------------------------------

DEEP_DIVE_IDS = ("onset", "trajectory", "severity")
ASSOCIATED_SYMPTOMS_ID = "associated_symptoms"
# Must mirror consultation.URGENT_MINIMUM_FACTS (kept local to avoid a
# consultation import cycle; a test pins the two equal).
URGENT_MINIMUM_FACTS = ("severity", "associated_symptoms", "patient_goal")
_GENERAL_DOMAIN = "general"
_ADULT_BAND = "adult"


def _season_for_month(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def _load_prior_weights() -> Dict[str, Any]:
    raw = (
        resources.files("medagent.content")
        .joinpath("prior_weights.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _coerce_age(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        return int(match.group(0)) if match else None
    return None


def _load_question_order() -> List[str]:
    raw = (
        resources.files("medagent.content")
        .joinpath("questions.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw)
    return [
        str(item["id"])
        for item in (data.get("questions", []) if isinstance(data, dict) else [])
        if item.get("id")
    ]


class HypothesisOrdering:
    """Deterministic hypothesis-driven ordering (``QuestionOrdering``).

    Verbatim move of ``hypothesis_ranking.HypothesisRanker``: orders
    *only* the already-filtered ``same_phase`` candidate set handed to
    it by the engines. It never filters candidates, never changes the
    phase order, and never adds a phase: candidate filtering (unrecorded
    + ``question_applies_in_state`` + fact gates) and phase selection
    stay exactly where they were. Non-HISTORY phases keep catalog order;
    only the HISTORY phase is reordered, by group.

    Hypothesis score (deterministic function; ``PRIOR_RANK`` is reused
    from ``conclusion.py``, support/refute matching is reused read-only
    from ``reasoning.py``'s proposer — this module changes neither):

        score(h) = acute(h) x PRIOR_RANK[h.prior_band] x season_w x age_w
                   x evidence(h)

        acute(h)    = 3 if h.category == "red_flag_condition" else 1
        PRIOR_RANK  = high 3 / medium 2 / low 1
        season_w    = prior_weights.json seasons[current season][domain]
        age_w       = prior_weights.json age_weights[age band][domain]
        evidence(h) = max(0, 1 + 0.5 x support_matched - 1.0 x refute_matched)

    where ``domain`` is the hypothesis's differentials pathway id, the
    season is derived from the current date (Mar-May spring, Jun-Aug
    summer, Sep-Nov autumn, Dec-Feb winter; ``season_override`` pins it
    for tests), and the age band comes from ``state.facts["age"]``
    (falling back to the actually recorded ``age_years`` key) matched
    against the table's ``age_bands``; a missing age resolves to
    ``"adult"``. v1 weights are all 1.0 (no clinical sign-off for
    anything else). A season key absent from the table degrades to
    equal weights (1.0) — another season's weights are never silently
    reused for an expired/missing season key.

    Question score, within a HISTORY group:

        q_score = 2 x (q is falsifiable ? 1 : 0)
                  + (number of top-5 hypotheses listing q in
                     discriminating_questions)

    A question is falsifiable when its ``fact_key`` appears in a refute
    condition of a top-3 hypothesis, or its id appears in the
    ``discriminating_questions`` of a workspace hypothesis outside the
    top-3. Ties break by questions.json catalog order (stable,
    reproducible).

    HISTORY group order: (0) off-pathway warning questions (P3 hook, see
    below); (1) missing URGENT_MINIMUM_FACTS items when the risk carries
    an urgent trigger; (2) the deep-dive group = {onset, trajectory,
    severity} plus detail questions whose ``applies_when`` chief terms
    match the chief complaint; (3) ``associated_symptoms``; (4) all
    remaining HISTORY questions, by question score. Groups (2) and (4)
    are internally ordered by question score.

    Off-pathway hook (P3): when ``chief_matches_pathway(chief)`` is
    False, ``warning_questions(chief)`` from ``out_of_pathway.py`` is
    consulted and any returned items carrying a question ``id`` that is
    among the candidates are placed at the HISTORY head. The import is
    defensive: while the P3 module is absent the hook degrades to an
    empty list and ranking proceeds unchanged. NOTE (integration point,
    supervisor to re-verify after P3 lands): the P3
    ``warning_questions`` contract returns ``{"domain", "question"}``
    dicts with no question id, so those warnings cannot be expressed as
    a ``choose()`` return value (a question id) and currently do not
    enter the candidate stream; wiring them into an actual asked
    question needs a P3 id/fact-key seam or engine support for
    non-catalog questions, both outside this package's file list.
    """

    name = "hypothesis"

    def __init__(self, season_override: Optional[str] = None) -> None:
        self._season_override = season_override
        self._weights = _load_prior_weights()
        self._catalog_order = {
            qid: index for index, qid in enumerate(_load_question_order())
        }
        self._pathways = load_pathways()
        self._proposer = RuleBasedProposer(pathways=self._pathways)
        self._domain_by_hypothesis: Dict[str, str] = {}
        self._refute_keys_by_hypothesis: Dict[str, frozenset] = {}
        self._discriminating_by_hypothesis: Dict[str, Tuple[str, ...]] = {}
        for pathway in self._pathways:
            domain = str(pathway.get("id", _GENERAL_DOMAIN))
            for raw in pathway.get("hypotheses", []):
                hid = str(raw.get("id", ""))
                if not hid:
                    continue
                self._domain_by_hypothesis[hid] = domain
                self._refute_keys_by_hypothesis[hid] = frozenset(
                    str(condition.get("fact_key", ""))
                    for condition in raw.get("refute", [])
                    if condition.get("fact_key")
                )
                self._discriminating_by_hypothesis[hid] = tuple(
                    str(qid) for qid in raw.get("discriminating_questions", [])
                )

    # -- prior weights -------------------------------------------------

    def _season(self) -> str:
        if self._season_override:
            return self._season_override
        return _season_for_month(date.today().month)

    def _age_band(self, state: CaseState) -> str:
        raw = state.get("age", None)
        if raw is None:
            raw = state.get("age_years", None)
        age = _coerce_age(raw)
        if age is None:
            return _ADULT_BAND
        for band in self._weights.get("age_bands", []) or []:
            try:
                if band["min_age"] <= age <= band["max_age"]:
                    return str(band["name"])
            except (KeyError, TypeError):
                continue
        return _ADULT_BAND

    def _weight(self, table: str, key: str, domain: str) -> float:
        """Equal weight (1.0) on any miss; never another key's weights."""
        entry = (self._weights.get(table, {}) or {}).get(key) or {}
        value = entry.get(domain, 1.0)
        return float(value) if isinstance(value, (int, float)) else 1.0

    # -- hypothesis scoring ---------------------------------------------

    def ranked_hypotheses(self, state: CaseState) -> List[Tuple[str, float]]:
        """[(hypothesis id, score)] best first; ties by content order."""
        workspace = self._proposer.propose(state)
        season = self._season()
        band = self._age_band(state)
        scored: List[Tuple[float, int, int, str]] = []
        for pathway_index, pathway in enumerate(self._pathways):
            domain = str(pathway.get("id", _GENERAL_DOMAIN))
            season_w = self._weight("seasons", season, domain)
            age_w = self._weight("age_weights", band, domain)
            for hypothesis_index, raw in enumerate(pathway.get("hypotheses", [])):
                hyp = workspace.hypotheses.get(str(raw.get("id", "")))
                if hyp is None:
                    continue
                support = len(hyp.evidence_for)
                refute = len(hyp.evidence_against)
                evidence = max(0.0, 1.0 + 0.5 * support - 1.0 * refute)
                acute = 3 if hyp.category == "red_flag_condition" else 1
                prior = PRIOR_RANK.get(hyp.prior_band, 1)
                score = acute * prior * season_w * age_w * evidence
                scored.append((score, pathway_index, hypothesis_index, hyp.id))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [(hid, score) for score, _, _, hid in scored]

    def _question_scorers(self, state: CaseState):
        ranked = [hid for hid, _ in self.ranked_hypotheses(state)]
        top3 = ranked[:3]
        top5 = ranked[:5]
        top3_ids = set(top3)
        refute_keys_top3 = set()
        for hid in top3:
            refute_keys_top3 |= set(self._refute_keys_by_hypothesis.get(hid, ()))
        discriminating_non_top = set()
        for hid in ranked:
            if hid not in top3_ids:
                discriminating_non_top |= set(
                    self._discriminating_by_hypothesis.get(hid, ())
                )

        def falsifiable(question: Question) -> bool:
            return (
                question.fact_key in refute_keys_top3
                or question.id in discriminating_non_top
            )

        def q_score(question: Question) -> int:
            return (2 if falsifiable(question) else 0) + sum(
                1
                for hid in top5
                if question.id in self._discriminating_by_hypothesis.get(hid, ())
            )

        return falsifiable, q_score

    # -- off-pathway hook ------------------------------------------------

    def off_pathway_warnings(self, chief: str) -> List[Dict[str, str]]:
        """Raw P3 warning dicts for an off-pathway chief (``[]`` if none).

        Kept as a separate accessor so the seam is inspectable even
        though id-less warnings cannot enter ``choose()``'s id stream.
        """
        if _warning_questions is None or not (chief or "").strip():
            return []
        try:
            items = _warning_questions(chief) or []
        except Exception:
            return []
        return [item for item in items if isinstance(item, dict)]

    def _warning_ids(self, chief: str, candidates: List[Question]) -> List[str]:
        candidate_ids = {question.id for question in candidates}
        ordered: List[str] = []
        for item in self.off_pathway_warnings(chief):
            # ``warning_questions`` keeps its frozen ``{domain,
            # question}`` contract (tests/test_out_of_pathway.py), so
            # the id is derived the same way bridging._p3_warning_ids
            # derives it. Without this fallback every warning question
            # is id-less and can never enter the id stream — which is
            # exactly why the P3 hook returned [] before the question
            # layer compiled them.
            wid = item.get("id") or (
                f"warning_{item['domain']}" if item.get("domain") else None
            )
            if wid and str(wid) in candidate_ids and str(wid) not in ordered:
                ordered.append(str(wid))
        return ordered

    # -- ordering ---------------------------------------------------------

    @staticmethod
    def _is_chief_detail(question: Question, chief: str) -> bool:
        terms = (question.applies_when or {}).get(
            "chief_complaint_contains_any"
        ) or []
        return bool(chief) and any(
            contains_unnegated(chief, term) for term in terms
        )

    def order(self, state: CaseState, candidates: List[Question]) -> List[str]:
        """Full deterministic ordering of ``candidates`` (ids, best first)."""
        ordered_candidates = list(candidates)
        if not ordered_candidates:
            return []
        if ordered_candidates[0].phase != Phase.HISTORY:
            # Only HISTORY is reordered; every other phase keeps the
            # catalog order the engines have always used.
            return [question.id for question in ordered_candidates]
        chief = str(state.get("chief_complaint", "") or "")
        _, q_score = self._question_scorers(state)
        passed_index = {q.id: index for index, q in enumerate(ordered_candidates)}
        catalog_size = len(self._catalog_order)

        def by_score(question: Question):
            # Ties break by questions.json catalog order (stable,
            # reproducible), never by the order candidates were passed
            # in; ids absent from the catalog fall back to passed order
            # after all catalog ids.
            catalog_idx = self._catalog_order.get(question.id)
            tie = (
                catalog_idx
                if catalog_idx is not None
                else catalog_size + passed_index[question.id]
            )
            return (-q_score(question), tie)

        result: List[str] = []
        seen: set = set()

        def take(questions):
            for question in questions:
                if question.id not in seen:
                    seen.add(question.id)
                    result.append(question.id)

        by_id = {q.id: q for q in ordered_candidates}
        if chief and not chief_matches_pathway(chief):
            take(
                by_id[wid]
                for wid in self._warning_ids(chief, ordered_candidates)
                if wid in by_id
            )
        if state.risk.level in (RiskLevel.URGENT, RiskLevel.EMERGENCY):
            missing = [
                q
                for q in ordered_candidates
                if q.fact_key in URGENT_MINIMUM_FACTS
            ]
            missing.sort(key=lambda q: URGENT_MINIMUM_FACTS.index(q.fact_key))
            take(missing)
        deep_dive = [
            q
            for q in ordered_candidates
            if q.id in DEEP_DIVE_IDS or self._is_chief_detail(q, chief)
        ]
        deep_dive.sort(key=by_score)
        take(deep_dive)
        take(
            q for q in ordered_candidates if q.id == ASSOCIATED_SYMPTOMS_ID
        )
        rest = [q for q in ordered_candidates if q.id not in seen]
        rest.sort(key=by_score)
        take(rest)
        return result

    def choose(
        self, state: CaseState, candidates: List[Question]
    ) -> Optional[str]:
        ordered = self.order(state, candidates)
        return ordered[0] if ordered else None


# ----------------------------------------------------------------------
# Differential strategy: verbatim move of reasoning.DifferentialPlanner
# scoring + selection. Workspace refresh, potential-discrimination
# inputs, abstention, events, and the planner's phase/BACKGROUND handling
# stay in reasoning.py; this strategy only scores and picks.
# ----------------------------------------------------------------------


class DifferentialOrdering:
    """Differential scoring strategy (``QuestionOrdering`` + context).

    ``score()`` and the ``max`` selection expression are verbatim from
    ``reasoning.DifferentialPlanner`` (weights from design doc §4.5, v1
    values; v1 has no burden term — a former W_BURDEN constant that was
    never wired into ``score()`` was removed so tuning notes don't lie).
    The strategy deliberately does NOT reuse ``HypothesisOrdering``'s
    ``q_score`` or HISTORY groups, and ``HypothesisOrdering`` does not
    reuse this score: the two orderings stay separate (§2.4 follow-up).

    Context (design doc §6.1: the interface must allow a strategy to
    carry its own context, not truncate it to ``(state, candidates)``):
    ``order``/``choose`` require ``workspace`` (a validated
    ``ReasoningWorkspace``, as produced by the reasoning layer's refresh)
    and ``changed_ids`` (hypothesis ids whose status changed on that
    refresh); ``potential_counts`` is the chief-matched discriminating
    counts (see :meth:`potential_discrimination`). Calling without a
    workspace raises ``ValueError`` rather than silently degrading to
    catalog order — a silent fallback would invent a new behaviour.

    ``order()`` sorts the full candidate set by the same
    ``(score, -index)`` key the ``max`` pick uses, so ``order()[0]``
    is provably the original ``max`` pick; ``choose()`` itself uses the
    original ``max`` expression verbatim, not the sort, so the pick
    path has no new code in it at all.
    """

    name = "differential"

    # weights from design doc §4.5 (v1 values; tune only with an eval report)
    # v1 has no burden term: question burden is governed by phase order and
    # the close conditions, not by score — a former W_BURDEN constant that
    # was never wired into score() was removed so tuning notes don't lie.
    W_RED_FLAG = 3.0
    W_DISCRIMINATION = 2.0
    W_REQUIRED = 1.0
    W_FRESHNESS = 0.5

    def __init__(
        self, pathways: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        # Same content source as DifferentialPlanner's proposer
        # (differentials.json via load_pathways); the caller may inject
        # pathways for tests. This is only used by
        # potential_discrimination; scoring itself needs no pathways.
        self._pathways = pathways if pathways is not None else load_pathways()

    def potential_discrimination(self, state: CaseState) -> Dict[str, int]:
        """Discriminating-question counts from chief-matched pathways.

        Verbatim from ``DifferentialPlanner._potential_discrimination``
        (only the pathway source differs: constructor-supplied pathways
        instead of ``self._proposer._pathways`` — same
        ``load_pathways()`` content). Hypotheses whose support
        conditions avoid the chief complaint itself (the
        abstention-preserving content rule) cannot enter the workspace
        until their detail question has been answered — a chicken-and-
        egg problem for ordering. The pathway match on the chief
        complaint alone is enough to know which questions *would*
        discriminate, so ordering can use it without touching workspace
        or abstention semantics.
        """
        chief = str(state.get("chief_complaint", ""))
        counts: Dict[str, int] = {}
        if not chief:
            return counts
        for pathway in self._pathways:
            terms = pathway.get("chief_complaint_contains_any", [])
            if not any(contains_unnegated(chief, term) for term in terms):
                continue
            for raw in pathway.get("hypotheses", []):
                for qid in raw.get("discriminating_questions", []):
                    counts[qid] = counts.get(qid, 0) + 1
        return counts

    def score(
        self,
        question,
        workspace: ReasoningWorkspace,
        changed_ids: set,
        potential_counts: Optional[Dict[str, int]] = None,
    ) -> float:
        active = workspace.active()
        red_flag = any(
            question.id in h.red_flag_questions and h.red_flags for h in active
        )
        discrimination = (
            sum(1 for h in active if question.id in h.discriminating_questions) >= 2
            or (potential_counts or {}).get(question.id, 0) >= 2
        )
        freshness = any(
            question.id in h.discriminating_questions
            for h in active
            if h.id in changed_ids
        )
        return (
            self.W_RED_FLAG * red_flag
            + self.W_DISCRIMINATION * discrimination
            + self.W_REQUIRED
            + self.W_FRESHNESS * freshness
        )

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
