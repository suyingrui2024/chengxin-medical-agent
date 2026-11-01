"""Shared clinical fact tables.

These are *data*, not logic: the banned-phrase list, the per-fact "why this
matters" impact copy, and the set of measurement-type facts.  They are read by
two feature packages that must otherwise stay independent — ``conclusion``
(composes the conclusion sheet) and ``subagents`` (the agent's sub-agent
orchestration) — plus a couple of kernel modules (``report_interpretation``,
``bridging``).

Before this package existed, all three tables lived in ``conclusion.constants``
and were re-exported from ``conclusion``, which forced ``subagents`` to import
the entire ``conclusion`` feature package just to read three constants.  That
was one of the six frozen R2 cross-feature edges.  Pulling the tables up here
gives both feature packages a neutral, dependency-free home to share, so the
``subagents -> conclusion`` edge disappears without either side having to know
about the other.

The package is intentionally dependency-free (it imports nothing from
``medagent``), so it can sit at the bottom of the stack: features and the
kernel may import it, and it imports nothing back.
"""

from __future__ import annotations

# Phrases the model must never put in a patient-facing sentence.  The conclusion
# composer, the sub-agent gate, report interpretation, and the bridging layer
# all check generated text against this list.
BANNED_PHRASES = (
    "确诊",
    "可以排除",
    "没事",
    "不用担心",
    "保证",
    "肯定是",
    "一定是",
    "必须吃药",
)


# Per-fact copy explaining *why* recording that fact changes the assessment.
# Used by the conclusion sheet's information-gap section and by sub-agents that
# score how much an answer would help.
GAP_IMPACTS = {
    "temperature_c": "体温读数会影响对感染程度的判断",
    "severity": "难受程度会影响对紧急程度的判断",
    "trajectory": "变化趋势会影响对病情走向的判断",
    "resp_breathing": "呼吸情况会影响对严重程度的判断",
    "gi_details": "腹痛的部位和性质会影响对腹部急症的判断",
    "head_details": "头痛的发作方式和伴随表现会影响对颅内急症的判断",
    "dizzy_details": "头晕的类型和伴随表现会影响对脑部供血问题的判断",
    "back_details": "腰痛是否影响腿部和大小便会影响对腰椎严重问题的判断",
    "urinary_details": "排尿表现和尿色会影响对尿路感染部位的判断",
    "skin_details": "皮疹的形态和诱因会影响对过敏与感染的判断",
}

# Fallback copy used when a fact key has no explicit entry in GAP_IMPACTS.
DEFAULT_GAP_IMPACT = "这项信息会影响判断"

# Fact keys that carry a numeric measurement (as opposed to a categorical or
# free-text observation).
MEASUREMENT_FACTS = {"temperature_c"}


# How much a hypothesis's prior band contributes to its score.  Read by the
# hypothesis ranker and the question orderer (both kernel), and by the
# conclusion sheet's certainty copy — the same table, so it lives here rather
# than in whichever package happened to define it first.
PRIOR_RANK = {"high": 3, "medium": 2, "low": 1}
