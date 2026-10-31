"""Hypothesis-driven question ranking (implementation plan P0).

The ranker orders *only* the already-filtered ``same_phase`` candidate set
handed to it by the engines. It never filters candidates, never changes
the phase order, and never adds a phase: candidate filtering (unrecorded
+ ``question_applies_in_state`` + fact gates) and phase selection stay
exactly where they were. Non-HISTORY phases keep catalog order; only the
HISTORY phase is reordered, by group.

Hypothesis score (deterministic function; ``PRIOR_RANK`` is reused from
``conclusion.py``, support/refute matching is reused read-only from
``reasoning.py``'s proposer — this module changes neither):

    score(h) = acute(h) x PRIOR_RANK[h.prior_band] x season_w x age_w
               x evidence(h)

    acute(h)    = 3 if h.category == "red_flag_condition" else 1
    PRIOR_RANK  = high 3 / medium 2 / low 1
    season_w    = prior_weights.json seasons[current season][domain]
    age_w       = prior_weights.json age_weights[age band][domain]
    evidence(h) = max(0, 1 + 0.5 x support_matched - 1.0 x refute_matched)

where ``domain`` is the hypothesis's differentials pathway id, the season
is derived from the current date (Mar-May spring, Jun-Aug summer,
Sep-Nov autumn, Dec-Feb winter; ``season_override`` pins it for tests),
and the age band comes from ``state.facts["age"]`` (falling back to the
actually recorded ``age_years`` key) matched against the table's
``age_bands``; a missing age resolves to ``"adult"``. v1 weights are all
1.0 (no clinical sign-off for anything else). A season key absent from
the table degrades to equal weights (1.0) — another season's weights are
never silently reused for an expired/missing season key.

Question score, within a HISTORY group:

    q_score = 2 x (q is falsifiable ? 1 : 0)
              + (number of top-5 hypotheses listing q in
                 discriminating_questions)

A question is falsifiable when its ``fact_key`` appears in a refute
condition of a top-3 hypothesis, or its id appears in the
``discriminating_questions`` of a workspace hypothesis outside the
top-3. Ties break by questions.json catalog order (stable, reproducible).

HISTORY group order: (0) off-pathway warning questions (P3 hook, see
below); (1) missing URGENT_MINIMUM_FACTS items when the risk carries an
urgent trigger; (2) the deep-dive group = {onset, trajectory, severity}
plus detail questions whose ``applies_when`` chief terms match the chief
complaint; (3) ``associated_symptoms``; (4) all remaining HISTORY
questions, by question score. Groups (2) and (4) are internally ordered
by question score.

Off-pathway hook (P3): when ``chief_matches_pathway(chief)`` is False,
``warning_questions(chief)`` from ``out_of_pathway.py`` is consulted and
any returned items carrying a question ``id`` that is among the
candidates are placed at the HISTORY head. The import is defensive:
while the P3 module is absent the hook degrades to an empty list and
ranking proceeds unchanged. NOTE (integration point, supervisor to
re-verify after P3 lands): the P3 ``warning_questions`` contract returns
``{"domain", "question"}`` dicts with no question id, so those warnings
cannot be expressed as a ``choose()`` return value (a question id) and
currently do not enter the candidate stream; wiring them into an actual
asked question needs a P3 id/fact-key seam or engine support for
non-catalog questions, both outside this package's file list.
"""

from __future__ import annotations

import json
import re
from datetime import date
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from .conclusion import PRIOR_RANK, chief_matches_pathway
from .domain import CaseState, Phase, RiskLevel
from .knowledge import Question
from .reasoning import RuleBasedProposer, load_pathways
from .text_match import contains_unnegated

try:  # P3 module lands in parallel; absence must not break ranking.
    from .out_of_pathway import warning_questions as _warning_questions
except ImportError:  # pragma: no cover - P3 not landed yet
    _warning_questions = None

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


class HypothesisRanker:
    """Deterministic hypothesis-driven ranker (``QuestionRanker`` protocol)."""

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
            # Same derived-id convention as bridging._p3_warning_ids:
            # ``warning_questions`` keeps its frozen ``{domain,
            # question}`` contract, so the id comes from the domain.
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
