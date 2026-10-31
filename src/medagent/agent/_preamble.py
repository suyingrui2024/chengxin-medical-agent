"""Shared imports, constants and module-level helpers for the package."""
from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from ..agent_tools import (
    ProposalError,
    TOOL_DEFINITIONS,
    validate_proposals,
    validate_command,
)
from ..backends import DecisionBackend
from ..consultation import (
    SELF_CHECK_SUSPEND_CAP,
    ConsultationEngine,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
    ConsultationBusyError,
    is_refusal_text,
    is_steady_not_improving_evidence,
    is_summary_confirmation_text,
    merge_preserving_denials,
)
from ..decision_wait import DecisionWaiter, DecisionTurn, DecisionInterrupted
from ..domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    Phase,
    QuickReply,
    RiskLevel,
    Source,
)
from ..events import Event
from ..kg import runtime as kg_runtime
from ..kg.ambiguity import should_clarify
from ..consultation_subject import (
    subject_profile,
    reference as subject_reference,
    compatible,
    explicit_switch,
)
from ..interpreter import Interpretation
from ..knowledge import Question, question_fact_gates_pass
from ..reasoning import RuleBasedProposer, load_pathways
from ..text_match import contains_unnegated
from ..worry import extract_worry_object, is_worry_sentence
from ..adaptive_questions import (
    AdaptiveQuestionError,
    RISK_TAG_CUES,
    project_selected_answer,
    validate_adaptive_question,
)
from ..bridging import (
    DETAIL_FACT_KEYS,
    MODEL_BRIDGING_REPAIR_HINT,
    SELF_BRIDGED_OPENERS,
    has_anchorable_fact,
    model_question_bridging_status,
)


SCREENING_FIELDS = frozenset({"associated_symptoms"})

# -- Batch C BURDEN ------------------------------------------------------
# D-02: fluctuation wording must never book as "stable" (变化不大).
# "旧的消了新的又起" describes waxing and waning, not steadiness; the
# J-03 steady-not-improving guard next to this one covers the mirror
# error (steady booked as worsening).
FLUCTUATION_PHRASES = (
    "此起彼伏",
    "旧的消了新的又起",
    "新的又起",
    "消了又起",
    "新旧交替",
    "时起时伏",
    # E2E-P2-5: paroxysmal / intermittent course ("一阵一阵的，
    # 中间有缓解") is fluctuation, not steadiness — it used to
    # slip through this guard and land as stable.
    "一阵一阵",
    "一阵阵",
    "中间有缓解",
    "时好时坏",
)

# Purpose-clause markers that must not be stripped by the denied-item
# filter: a "为什么问" half-sentence may itself contain "有没有".
_PURPOSE_CLAUSE_MARKERS = ("为了", "想先确认", "想确认", "先确认", "好判断")

# Denied-item synonyms for the composite-question filter (Y-10/P-02):
# a recorded "没有发烧" denies a later "发热" item and vice versa.
_DENIED_ITEM_SYNONYMS = {
    "发热": ("发烧",),
    "发烧": ("发热",),
}


def _recorded_denies_token(texts: List[str], token: str) -> bool:
    """True when some recorded fact text explicitly denies ``token``."""
    variants = (token,) + _DENIED_ITEM_SYNONYMS.get(token, ())
    for variant in variants:
        for prefix in ("没有", "无", "不", "没"):
            needle = prefix + variant
            if any(needle in text for text in texts):
                return True
    return False


def _strip_denied_items(text: str, facts: Mapping[str, Any]) -> str:
    """Drop already-denied items from a composite question (Y-10/P-02).

    Only "有没有/有无 …？" clauses are filtered, and only tokens with
    an explicit recorded denial ("没有发热" denies "发热"). A clause
    that loses every token is dropped; the genuinely new remainder
    stays askable. Purpose clauses ("为了确认有没有…") are never
    touched. Recorded fact texts (values + evidence) are the denial
    corpus; nothing here invents or rewords a clinical term.
    """
    recorded: List[str] = []
    for fact in facts.values():
        value = getattr(fact, "value", None)
        if isinstance(value, str):
            recorded.append(value)
        evidence = getattr(fact, "evidence", None)
        if isinstance(evidence, str):
            recorded.append(evidence)
    if not recorded:
        return text
    # A model question carries its bridging prefix in the same clause
    # as the question ("你说…，想先确认一下：有没有…？"). The prefix's
    # purpose half-sentence must not be mistaken for a question clause,
    # so it is split off at the colon that follows the purpose marker
    # and only the question body is filtered.
    prefix, body = "", text
    for opener in SELF_BRIDGED_OPENERS:
        if not text.startswith(opener):
            continue
        for marker in _PURPOSE_CLAUSE_MARKERS:
            marker_at = text.find(marker)
            if marker_at < 0:
                continue
            colon_at = text.find("：", marker_at)
            if colon_at >= 0:
                prefix, body = text[: colon_at + 1], text[colon_at + 1 :]
            break
        break
    filtered = _filter_question_clauses(body, recorded)
    result = prefix + filtered
    return result if result.strip() else text


def _filter_question_clauses(text: str, recorded: List[str]) -> str:
    clauses = re.split(r"(？|\?|；|;)", text)
    out: List[str] = []
    index = 0
    while index < len(clauses):
        clause = clauses[index]
        delimiter = clauses[index + 1] if index + 1 < len(clauses) else ""
        index += 2
        match = re.search(r"(有没有|有无)", clause)
        if match is None or any(
            marker in clause for marker in _PURPOSE_CLAUSE_MARKERS
        ):
            out.append(clause + delimiter)
            continue
        head, tail = clause[: match.end()], clause[match.end() :]
        tokens = [part for part in re.split(r"[、，,或]", tail) if part.strip()]
        kept = [
            token
            for token in tokens
            if not _recorded_denies_token(recorded, token.strip())
        ]
        if not kept:
            continue
        if len(kept) == len(tokens):
            out.append(clause + delimiter)
        else:
            out.append(head + "、".join(token.strip() for token in kept) + delimiter)
    result = "".join(out)
    return result if result.strip() else text

# Task kinds the *model* may propose via spawn_subagent (the tool
# description's contract). Controller-only kinds — case_digest (the
# controller's own long-case digest) and followup_batch (server-triggered)
# — are deliberately absent: the coordinator only checks ``kind in
# KINDS``, which includes those, so it cannot backstop this layer
# (healthcheck P1-A-D2). The decision layer enforces the whitelist
# before any coordinator call or spawn-budget consumption.
MODEL_SPAWN_TASK_KINDS = frozenset({"evidence_lookup", "second_opinion"})

# Messages raised by backends.parse_decision (backends.py itself is
# another lane's file and is not touched here). These mean the model's
# own decision output was malformed, so the model can repair it —
# unlike a transport failure, which it cannot. Matched verbatim so
# unrelated backend ValueErrors (config errors, bridge envelope
# errors, ...) keep the old service_error path.
_DECISION_ENVELOPE_ERRORS = {
    "invalid decision size": (
        "决策输出超过长度上限或不是文本：请只输出一个简短的 "
        "{tool, arguments} JSON 对象，不要附加解释或重复内容。"
    ),
    "decision must contain only tool and arguments": (
        "决策 JSON 必须只含 tool 和 arguments 两个键：请删掉多余键、"
        "补齐缺失键后按注册定义重提。"
    ),
    "invalid decision envelope": (
        "决策信封无效：tool 必须是字符串、arguments 必须是对象，"
        "请按工具目录的注册定义重提。"
    ),
}


def _decision_envelope_guidance(exc: BaseException) -> Optional[str]:
    """Repair guidance for a repairable decision-envelope error.

    Returns None for anything else. Bad JSON surfaces as
    ``json.JSONDecodeError`` (a ValueError subclass) from the same
    parser, so it is repairable too; every other exception type or
    message is a genuine backend failure, not a model output error.
    """
    if isinstance(exc, json.JSONDecodeError):
        return (
            "决策输出不是合法 JSON：请只输出一个 {tool, arguments} "
            "JSON 对象，不要附加解释、代码围栏或多余文本。"
        )
    if isinstance(exc, ValueError):
        return _DECISION_ENVELOPE_ERRORS.get(str(exc))
    return None


def _capability_advertisement(item):
    """What the model is told about one patient-facing capability.

    Kept deliberately thin. With ~60 patient-facing capabilities the
    advertisement had grown to 30 KB — more than half the static prefix —
    because every entry repeated ``version``, ``review_status``,
    ``patient_form`` and a per-item call note, and formulas shipped their
    full input schema twice. None of that helps the model choose; the
    shared facts now live once in ``capability_note``.

    What remains is what selection actually needs: id, name, size, and
    the aliases a patient might say.
    """
    base = {"id": item["id"], "name": item["name"]}
    if item.get("patient_form") == "scale_form/v1":
        scale = item.get("scale") or {}
        aggregate = scale.get("aggregate") or {}
        base["items"] = len(scale.get("items") or [])
        base["range"] = [aggregate.get("min"), aggregate.get("max")]
        base["aliases"] = list(item.get("aliases") or [])
        return base
    formula = item.get("formula")
    if isinstance(formula, dict):
        base["inputs"] = [entry["id"] for entry in formula.get("inputs") or []]
        base["unit"] = (formula.get("output") or {}).get("unit", "")
        base["aliases"] = list(item.get("aliases") or [])
        return base
    # Unit conversions keep their schema: the prompt tells the model to
    # prefill value/from_unit when the patient already said them, so it
    # needs the field names. There are only two of these.
    base["proposal_input_schema"] = {**item["input_schema"], "required": []}
    base["execution_input_schema"] = item["input_schema"]
    return base


__all__ = [
    "json",
    "hashlib",
    "re",
    "deepcopy",
    "resources",
    "Any",
    "Dict",
    "List",
    "Mapping",
    "Optional",
    "uuid4",
    "ProposalError",
    "TOOL_DEFINITIONS",
    "validate_proposals",
    "validate_command",
    "DecisionBackend",
    "SELF_CHECK_SUSPEND_CAP",
    "ConsultationEngine",
    "StaleActionError",
    "SubjectBoundaryError",
    "UserInput",
    "ConsultationBusyError",
    "is_refusal_text",
    "is_steady_not_improving_evidence",
    "is_summary_confirmation_text",
    "merge_preserving_denials",
    "DecisionWaiter",
    "DecisionTurn",
    "DecisionInterrupted",
    "AgentReply",
    "Assertion",
    "CaseState",
    "Fact",
    "Phase",
    "QuickReply",
    "RiskLevel",
    "Source",
    "Event",
    "kg_runtime",
    "should_clarify",
    "subject_profile",
    "subject_reference",
    "compatible",
    "explicit_switch",
    "Interpretation",
    "Question",
    "question_fact_gates_pass",
    "RuleBasedProposer",
    "load_pathways",
    "contains_unnegated",
    "extract_worry_object",
    "is_worry_sentence",
    "AdaptiveQuestionError",
    "RISK_TAG_CUES",
    "project_selected_answer",
    "validate_adaptive_question",
    "DETAIL_FACT_KEYS",
    "MODEL_BRIDGING_REPAIR_HINT",
    "SELF_BRIDGED_OPENERS",
    "has_anchorable_fact",
    "model_question_bridging_status",
    "SCREENING_FIELDS",
    "FLUCTUATION_PHRASES",
    "_PURPOSE_CLAUSE_MARKERS",
    "_DENIED_ITEM_SYNONYMS",
    "_recorded_denies_token",
    "_strip_denied_items",
    "_filter_question_clauses",
    "MODEL_SPAWN_TASK_KINDS",
    "_DECISION_ENVELOPE_ERRORS",
    "_decision_envelope_guidance",
    "_capability_advertisement",
]
