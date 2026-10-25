"""Differential-diagnosis reasoning core (design doc §4.5).

The workspace holds candidate hypotheses with evidence links back to
recorded facts. A proposer (rule-based v1 here; a model proposer plugs in
behind the same protocol) recomputes a candidate workspace from current
facts, a deterministic validator checks every evidence reference, and
only validated hypotheses are persisted on the case. The
``DifferentialPlanner`` then chooses the next question by red-flag
coverage and discrimination value instead of bank order alone.

The ``DifferentialPlanner`` became the engine default on 2026-10-23
after the planner A/B evaluation passed with no outcome regressions
(scripts/evaluation/medical/evaluate_planner_ab.py); ``MEDAGENT_PLANNER=
rule`` rolls back to the deterministic bank-order ``RulePlanner``, and
``enable_differential_planning`` remains for tests and explicit opt-in.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Dict, List, Optional, Tuple

from .domain import AgentReply, Assertion, CaseState, Phase, RiskLevel
from .events import Event
from .knowledge import question_fact_gates_pass
from .phase_graph import planning_rank
from .text_match import (
    NEGATION_CUES,
    UNCERTAINTY_MARKERS,
    contains_unnegated,
    find_unnegated_certain,
)

ACTIVE_STATUSES = ("active", "supported")


@dataclass
class EvidenceRef:
    fact_key: str
    fact_id: str
    quote: str

    def to_dict(self) -> Dict[str, Any]:
        return {"fact_key": self.fact_key, "fact_id": self.fact_id, "quote": self.quote}


@dataclass
class Hypothesis:
    id: str
    label: str
    category: str
    prior_band: str
    status: str = "active"
    evidence_for: List[EvidenceRef] = field(default_factory=list)
    evidence_against: List[EvidenceRef] = field(default_factory=list)
    red_flags: List[str] = field(default_factory=list)
    discriminating_questions: List[str] = field(default_factory=list)
    # Questions that directly probe this hypothesis's red-flag domain
    # (drives the planner's red-flag coverage weight).
    red_flag_questions: List[str] = field(default_factory=list)
    updated_at_rev: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category,
            "prior_band": self.prior_band,
            "status": self.status,
            "evidence_for": [ref.to_dict() for ref in self.evidence_for],
            "evidence_against": [ref.to_dict() for ref in self.evidence_against],
            "red_flags": list(self.red_flags),
            "discriminating_questions": list(self.discriminating_questions),
            "red_flag_questions": list(self.red_flag_questions),
            "updated_at_rev": self.updated_at_rev,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Hypothesis":
        return cls(
            id=raw["id"],
            label=raw["label"],
            category=raw["category"],
            prior_band=raw["prior_band"],
            status=raw["status"],
            evidence_for=[EvidenceRef(**ref) for ref in raw.get("evidence_for", [])],
            evidence_against=[
                EvidenceRef(**ref) for ref in raw.get("evidence_against", [])
            ],
            red_flags=list(raw.get("red_flags", [])),
            discriminating_questions=list(raw.get("discriminating_questions", [])),
            red_flag_questions=list(raw.get("red_flag_questions", [])),
            updated_at_rev=int(raw.get("updated_at_rev", 0)),
        )


@dataclass
class ReasoningWorkspace:
    version: int = 0
    stagnation_turns: int = 0
    hypotheses: Dict[str, Hypothesis] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "stagnation_turns": self.stagnation_turns,
            "hypotheses": [h.to_dict() for h in self.hypotheses.values()],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ReasoningWorkspace":
        if not raw:
            return cls()
        return cls(
            version=int(raw.get("version", 0)),
            stagnation_turns=int(raw.get("stagnation_turns", 0)),
            hypotheses={
                item["id"]: Hypothesis.from_dict(item)
                for item in raw.get("hypotheses", [])
            },
        )

    def active(self) -> List[Hypothesis]:
        return [h for h in self.hypotheses.values() if h.status in ACTIVE_STATUSES]

    def unresolved_red_flags(self) -> List[Hypothesis]:
        return [h for h in self.active() if h.red_flags]


# ----------------------------------------------------------------------
# Rule-based proposer (v1). Conditions are the small DSL documented in
# content/differentials.json.
# ----------------------------------------------------------------------

def load_pathways() -> List[Dict[str, Any]]:
    raw = (
        resources.files("medagent.content")
        .joinpath("differentials.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)["pathways"]


# Encoded fact values that must never pose as patient quotes. This
# mirrors conclusion._ENCODING_QUOTES (conclusion cannot be imported
# here — it imports this module); keep the two sets in sync.
_ENCODING_VALUES = frozenset(
    {
        "worsening",
        "improving",
        "stable",
        "unknown",
        "present",
        "absent",
        "self",
        "child",
        "other_adult",
    }
)


def _is_cjk(char: str) -> bool:
    return "\u4e00" <= char <= "\u9fff"


def _genuine_wording_quote(fact) -> Optional[str]:
    """The fact's evidence as a quote for a fired equals/gte/lte
    condition — but only when the evidence really is patient wording.

    A structured fact (trajectory, severity, temperature) is recorded
    with a coded value, and ``str(value)`` (``"stable"``, ``"10"``,
    ``"38.5"``) is not something the patient said: the conclusion
    composer's citation gate rightly rejects those as quotes, which
    used to leave every encoding-only hypothesis quoteless. When the
    same fact also carries the patient's own phrasing (trajectory
    ``stable`` recorded as ``"变化不大"``), that phrasing IS quotable
    and is returned instead. The bar is deliberately strict so a score
    can never launder itself into wording: at least two CJK characters
    (``"3分"`` has one, ``"38.5"``/``"10"``/``"stable"`` have none),
    not an encoded value, no negation cue or uncertainty marker
    anywhere in the text, and affirmed-and-certain under the shared
    ``find_unnegated_certain`` semantics. Anything else falls back to
    ``str(value)`` and stays subject to the composer's gate.
    """
    if fact is None or fact.assertion != Assertion.PRESENT:
        return None
    evidence = (fact.evidence or "").strip()
    if len(evidence) < 2 or evidence in _ENCODING_VALUES:
        return None
    if sum(1 for char in evidence if _is_cjk(char)) < 2:
        return None
    if any(cue in evidence for cue in NEGATION_CUES):
        return None
    if any(marker in evidence for marker in UNCERTAINTY_MARKERS):
        return None
    if find_unnegated_certain(evidence, evidence) < 0:
        return None
    return evidence


def _condition_quote(state: CaseState, condition: Dict[str, Any]) -> Optional[str]:
    fact = state.facts.get(condition["fact_key"])
    if fact is None or fact.assertion == Assertion.UNKNOWN:
        return None
    if "assertion" in condition:
        if fact.assertion.value != condition["assertion"]:
            return None
        # Quote the patient's own words (the denial itself), never
        # str(value): for an ABSENT fact value is None and the literal
        # string "None" would enter the evidence chain as a fake quote.
        if fact.evidence:
            return fact.evidence
        return str(fact.value) if isinstance(fact.value, str) else None
    if "contains_any" in condition:
        haystack = fact.value if isinstance(fact.value, str) else ""
        evidence = fact.evidence or ""
        for term in condition["contains_any"]:
            # Negated mentions ("没有呕吐") never count as evidence.
            if contains_unnegated(haystack, term) or contains_unnegated(
                evidence, term
            ):
                return term
        return None
    if "equals" in condition:
        if fact.value != condition["equals"]:
            return None
        return _genuine_wording_quote(fact) or str(fact.value)
    if "gte" in condition:
        ok = isinstance(fact.value, (int, float)) and fact.value >= condition["gte"]
        if not ok:
            return None
        return _genuine_wording_quote(fact) or str(fact.value)
    if "lte" in condition:
        ok = isinstance(fact.value, (int, float)) and fact.value <= condition["lte"]
        if not ok:
            return None
        return _genuine_wording_quote(fact) or str(fact.value)
    return None


class RuleBasedProposer:
    """Recomputes the workspace from current facts on every planning turn."""

    def __init__(
        self,
        pathways: Optional[List[Dict[str, Any]]] = None,
        question_fact_keys: Optional[Dict[str, str]] = None,
    ) -> None:
        self._pathways = pathways if pathways is not None else load_pathways()
        self._question_fact_keys = question_fact_keys or {}

    def propose(self, state: CaseState) -> ReasoningWorkspace:
        chief = str(state.get("chief_complaint", ""))
        workspace = ReasoningWorkspace()
        chief_fact = state.facts.get("chief_complaint")
        for pathway in self._pathways:
            terms = pathway.get("chief_complaint_contains_any", [])
            matched_term = next(
                (t for t in terms if contains_unnegated(chief, t)), None
            )
            if matched_term is None:
                continue
            for raw in pathway["hypotheses"]:
                evidence_for: List[EvidenceRef] = []
                evidence_against: List[EvidenceRef] = []
                for condition in raw.get("support", []):
                    quote = _condition_quote(state, condition)
                    if quote is not None:
                        fact = state.facts[condition["fact_key"]]
                        evidence_for.append(
                            EvidenceRef(condition["fact_key"], fact.fact_id, quote)
                        )
                for condition in raw.get("refute", []):
                    quote = _condition_quote(state, condition)
                    if quote is not None:
                        fact = state.facts[condition["fact_key"]]
                        evidence_against.append(
                            EvidenceRef(condition["fact_key"], fact.fact_id, quote)
                        )
                if not evidence_for and not evidence_against:
                    # Red-flag hypotheses still enter on a matching chief
                    # complaint alone: they exist to be excluded, and the
                    # chief complaint fact is their evidence.
                    if raw.get("red_flags") and chief_fact is not None:
                        evidence_for.append(
                            EvidenceRef("chief_complaint", chief_fact.fact_id, matched_term)
                        )
                    else:
                        continue
                if evidence_against:
                    status = "refuted"
                elif len(evidence_for) >= 2:
                    status = "supported"
                else:
                    status = "active"
                discriminating = list(raw.get("discriminating_questions", []))
                # Entered on the chief complaint alone and every
                # discriminating question has since been answered with
                # nothing supporting it: downgrade to "unlikely" so the
                # abstention rule does not fire on an exhausted workup.
                if (
                    status == "active"
                    and discriminating
                    and len(evidence_for) == 1
                    and evidence_for[0].fact_key == "chief_complaint"
                    and all(
                        self._question_fact_keys.get(qid, qid) in state.facts
                        for qid in discriminating
                    )
                ):
                    status = "unlikely"
                workspace.hypotheses[raw["id"]] = Hypothesis(
                    id=raw["id"],
                    label=raw["label"],
                    category=raw["category"],
                    prior_band=raw["prior_band"],
                    status=status,
                    evidence_for=evidence_for,
                    evidence_against=evidence_against,
                    red_flags=list(raw.get("red_flags", [])),
                    discriminating_questions=discriminating,
                    red_flag_questions=list(raw.get("red_flag_questions", [])),
                    updated_at_rev=state.revision,
                )
        return workspace


# ----------------------------------------------------------------------
# Deterministic validator: a hypothesis is admitted only if every
# evidence reference points at the current fact with that id and the
# quote is a verbatim substring of the fact's recorded evidence/value or
# of a user transcript turn.
# ----------------------------------------------------------------------

def validate_workspace(
    state: CaseState, workspace: ReasoningWorkspace
) -> Tuple[ReasoningWorkspace, List[Tuple[str, str]]]:
    transcript_pool = "\n".join(
        item.get("content", "")
        for item in state.transcript
        if item.get("role") == "user"
    )
    accepted = ReasoningWorkspace(
        version=workspace.version, stagnation_turns=workspace.stagnation_turns
    )
    rejected: List[Tuple[str, str]] = []
    for hypothesis in workspace.hypotheses.values():
        problem: Optional[str] = None
        for ref in [*hypothesis.evidence_for, *hypothesis.evidence_against]:
            fact = state.facts.get(ref.fact_key)
            if fact is None:
                problem = f"evidence fact {ref.fact_key} not recorded"
                break
            if fact.fact_id != ref.fact_id:
                problem = f"evidence fact {ref.fact_key} superseded"
                break
            pool = "\n".join(
                part
                for part in (fact.evidence or "", str(fact.value), transcript_pool)
                if part
            )
            if ref.quote not in pool:
                problem = f"evidence quote not verbatim for {ref.fact_key}"
                break
        if problem is None:
            accepted.hypotheses[hypothesis.id] = hypothesis
        else:
            rejected.append((hypothesis.id, problem))
    return accepted, rejected


# ----------------------------------------------------------------------
# Differential planner.
# ----------------------------------------------------------------------

class DifferentialPlanner:
    """Question selection by red-flag coverage + discrimination value."""

    name = "differential"

    def __init__(self, engine, proposer: Optional[RuleBasedProposer] = None) -> None:
        self._engine = engine
        if proposer is None:
            proposer = RuleBasedProposer(
                question_fact_keys={
                    q.id: q.fact_key for q in engine.content.questions
                }
            )
        self._proposer = proposer

    # weights from design doc §4.5 (v1 values; tune only with an eval report)
    # v1 has no burden term: question burden is governed by phase order and
    # the close conditions, not by score — a former W_BURDEN constant that
    # was never wired into score() was removed so tuning notes don't lie.
    W_RED_FLAG = 3.0
    W_DISCRIMINATION = 2.0
    W_REQUIRED = 1.0
    W_FRESHNESS = 0.5

    def _refresh_workspace(self, state: CaseState) -> Tuple[ReasoningWorkspace, set]:
        engine = self._engine
        proposed = self._proposer.propose(state)
        accepted, rejected = validate_workspace(state, proposed)
        for hypothesis_id, problem in rejected:
            engine.events.append(
                Event(
                    state.session_id,
                    "reasoning.proposal_rejected",
                    {"hypothesis_id": hypothesis_id, "problem": problem},
                )
            )
        previous = ReasoningWorkspace.from_dict(state.reasoning)
        old_status = {h.id: h.status for h in previous.hypotheses.values()}
        changed_ids = {
            h.id
            for h in accepted.hypotheses.values()
            if old_status.get(h.id) != h.status
        }

        def signature(workspace: ReasoningWorkspace):
            # updated_at_rev changes every turn by construction; progress
            # means a status or evidence change, nothing else.
            return {
                h.id: (
                    h.status,
                    tuple(sorted(r.fact_id for r in h.evidence_for)),
                    tuple(sorted(r.fact_id for r in h.evidence_against)),
                )
                for h in workspace.hypotheses.values()
            }

        if signature(accepted) != signature(previous):
            accepted.version = previous.version + 1
            accepted.stagnation_turns = 0
            state.reasoning = accepted.to_dict()
            engine.events.append(
                Event(
                    state.session_id,
                    "reasoning.workspace_updated",
                    {
                        "version": accepted.version,
                        "hypotheses": [
                            {"id": h.id, "status": h.status}
                            for h in accepted.hypotheses.values()
                        ],
                    },
                )
            )
        else:
            accepted.version = previous.version
            accepted.stagnation_turns = previous.stagnation_turns + (
                1 if accepted.hypotheses else 0
            )
            state.reasoning = accepted.to_dict()
        return accepted, changed_ids

    def _potential_discrimination(self, state: CaseState) -> Dict[str, int]:
        """Discriminating-question counts from chief-matched pathways.

        Hypotheses whose support conditions avoid the chief complaint
        itself (the abstention-preserving content rule) cannot enter the
        workspace until their detail question has been answered — a
        chicken-and-egg problem for ordering. The pathway match on the
        chief complaint alone is enough to know which questions *would*
        discriminate, so ordering can use it without touching workspace
        or abstention semantics.
        """
        chief = str(state.get("chief_complaint", ""))
        counts: Dict[str, int] = {}
        if not chief:
            return counts
        for pathway in self._proposer._pathways:
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

    def _reason(self, question, workspace: ReasoningWorkspace) -> str:
        active = workspace.active()
        red = [h.label for h in active if question.id in h.discriminating_questions and h.red_flags]
        if red:
            return f"为排除红旗相关假设（{'、'.join(red)}）提问 {question.id}"
        disc = [h.label for h in active if question.id in h.discriminating_questions]
        if len(disc) >= 2:
            return f"为区分假设（{'、'.join(disc)}）提问 {question.id}"
        if disc:
            return f"为评估假设（{disc[0]}）提问 {question.id}"
        return f"{question.fact_key} 是当前阶段尚未获得的关键字段"

    def plan(self, state: CaseState) -> AgentReply:
        engine = self._engine
        workspace, changed_ids = self._refresh_workspace(state)
        chief = str(state.get("chief_complaint", ""))
        candidates = [
            question
            for question in engine.content.questions
            if question.fact_key not in state.facts
            and engine.content.question_applies_in_state(question, state)
            and question_fact_gates_pass(question, state.facts)
        ]
        # Same parity rule as ConsultationEngine._plan_next: non-blocking
        # questions stay askable but never gate the finish on their own.
        if any(question.blocking for question in candidates):
            earliest_phase = min(planning_rank(item.phase) for item in candidates)
            same_phase = [
                item for item in candidates if planning_rank(item.phase) == earliest_phase
            ]
            if same_phase[0].phase == Phase.BACKGROUND:
                offered = engine._offer_task(state, chief)
                if offered:
                    return offered
            potential = self._potential_discrimination(state)
            best = max(
                same_phase,
                key=lambda q: (
                    self.score(q, workspace, changed_ids, potential),
                    -same_phase.index(q),
                ),
            )
            return engine._ask(state, best, reason=self._reason(best, workspace))

        offered = engine._offer_task(state, chief)
        if offered:
            return offered
        # Abstention (v1 mapping of design §4.5): a red-flag hypothesis
        # that is still merely "active" (never supported, never refuted,
        # never exhausted into "unlikely") with no discriminating
        # questions left and no workspace progress for 3 planning turns
        # -> advise prompt care instead of closing with a benign summary.
        # Supported hypotheses finish normally: their evidence belongs in
        # the summary, and escalation remains the triage layer's job.
        stuck = [
            h for h in workspace.active() if h.red_flags and h.status == "active"
        ]
        if stuck and workspace.stagnation_turns >= 3:
            # Never call _urgent directly on a downgraded risk: after
            # a deferral stands the urgent close down (empty triggers),
            # _urgent would emit a SUMMARY-phase reply of kind
            # "urgent" -- a malformed combination. A still-URGENT risk
            # goes through the minimum-fact gate; a downgraded risk
            # (ROUTINE, or any risk level after a triage.urgent_deferred
            # event) falls through to the normal finish, a summary for
            # patient review rather than an urgent close on empty
            # triggers. A never-assessed UNDETERMINED risk with no
            # prior deferral keeps the standing abstention-urgent
            # behaviour pinned by test_reasoning.py.
            deferred = any(
                event.event_type == "triage.urgent_deferred"
                for event in engine.events.for_session(state.session_id)
            )
            if state.risk.level == RiskLevel.URGENT:
                gated = engine._urgent_or_defer(state)
                if gated is not None:
                    return gated
                return engine._finish(state)
            if state.risk.level == RiskLevel.ROUTINE or deferred:
                return engine._finish(state)
            return engine._urgent(state)
        return engine._finish(state)


def enable_differential_planning(engine) -> DifferentialPlanner:
    """Swap an engine's planner to the differential planner (opt-in)."""
    planner = DifferentialPlanner(engine)
    engine.planner = planner
    return planner
