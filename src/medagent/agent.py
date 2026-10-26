"""Bounded medical ReAct controller shared by the DSH and compatible transports."""
from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional
from uuid import uuid4

from .agent_tools import (
    ProposalError,
    TOOL_DEFINITIONS,
    validate_proposals,
    validate_command,
)
from .backends import DecisionBackend
from .consultation import (
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
from .decision_wait import DecisionWaiter, DecisionTurn, DecisionInterrupted
from .domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    Phase,
    QuickReply,
    RiskLevel,
    Source,
)
from .events import Event
from .consultation_subject import (
    subject_profile,
    reference as subject_reference,
    compatible,
    explicit_switch,
)
from .interpreter import Interpretation
from .knowledge import Question, question_fact_gates_pass
from .reasoning import RuleBasedProposer, load_pathways
from .text_match import contains_unnegated
from .worry import extract_worry_object, is_worry_sentence
from .adaptive_questions import (
    AdaptiveQuestionError,
    RISK_TAG_CUES,
    project_selected_answer,
    validate_adaptive_question,
)
from .bridging import (
    DETAIL_FACT_KEYS,
    MODEL_BRIDGING_REPAIR_HINT,
    SELF_BRIDGED_OPENERS,
    has_anchorable_fact,
    model_question_bridging_status,
)


# Screening fields whose incidental mention (recorded while a *different*
# question was active) must not silently count as a completed screening.
# The engine flags them in the model context instead (wave-2 spillover
# signal); they still leave missing_required once recorded.
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


class AdaptiveConsultationEngine(ConsultationEngine):
    """LLM proposes actions; evidence/type/eligibility gates own every effect."""

    def __init__(
        self, *args, backend: DecisionBackend, subagent_coordinator=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.backend = backend
        # Optional medagent.subagents.SubagentCoordinator; None keeps the
        # spawn_subagent tool unavailable (the server assembles and injects
        # it; other construction paths stay coordinator-free by default).
        self.subagent_coordinator = subagent_coordinator
        self._round_spawn_counts: Dict[str, int] = {}
        # session_id -> screening fact keys recorded only incidentally
        # (see SCREENING_FIELDS); advisory context signal, never persisted.
        self._incidental_records: Dict[str, set] = {}
        self._active_turns = {}
        self._decision_waiter = DecisionWaiter()
        # Pathway close-gate structures (live-eval case 3: a triggered
        # chief-complaint pathway whose details were never probed must not
        # be silently summarized away). Triggering reuses the reasoning
        # core's own pathway definitions and proposer — no second keyword
        # table. A pathway's detail facts are derived from the DSL: fact
        # keys cited by the pathway's hypotheses in support/refute
        # conditions that are collected by a chief-complaint-gated
        # question (the *_details-style fields). Generic background fields
        # (trajectory, severity, relevant_history…) are cited by many
        # pathways and never count as "the pathway was probed".
        self._pathways = load_pathways()
        self._pathway_proposer = RuleBasedProposer(
            pathways=self._pathways,
            question_fact_keys={q.id: q.fact_key for q in self.content.questions},
        )
        self._questions_by_fact = {}
        for question in self.content.questions:
            self._questions_by_fact.setdefault(question.fact_key, question)
        gated_fact_keys = {
            q.fact_key
            for q in self.content.questions
            if q.applies_when.get("chief_complaint_contains_any")
        }
        self._pathway_detail_facts: Dict[str, Any] = {}
        for pathway in self._pathways:
            cited = {
                condition["fact_key"]
                for hypothesis in pathway.get("hypotheses", [])
                for condition in [
                    *hypothesis.get("support", []),
                    *hypothesis.get("refute", []),
                ]
            }
            self._pathway_detail_facts[pathway["id"]] = sorted(
                fact_key
                for fact_key in cited
                if fact_key != "chief_complaint" and fact_key in gated_fact_keys
            )
        self.prompt = (
            resources.files("medagent.content")
            .joinpath("agent_prompt.txt")
            .read_text(encoding="utf-8")
        )
        # Immutable per-engine catalog precedes ALL case-specific data. This is
        # provider KV-prefix reuse, not a cache of patient answers or decisions.
        # The base catalog always carries the full tool list; which tools
        # are *advertised* to the model is resolved lazily at message-build
        # time (see _effective_static_context): production assembly injects
        # the subagent coordinator AFTER construction (server startup
        # setattr, Workspace._attach_subagents on settings apply), so a
        # tool list frozen here would advertise spawn_subagent exactly
        # never in the real product — and would keep advertising it if
        # the coordinator were later detached or switched off.
        self.static_context = {
            "tools": TOOL_DEFINITIONS,
            "fact_schema": [
                {
                    "key": q.fact_key,
                    "type": q.input,
                    "options": [o.value for o in q.options],
                    "value_contract": self._value_contract(q),
                }
                for q in self.content.questions
            ],
            "question_catalog": [
                {
                    "id": q.id,
                    "field": q.fact_key,
                    "prompt": q.prompt,
                    "applies_when": q.applies_when,
                }
                for q in self.content.questions
            ],
            "self_check_catalog": [
                {
                    key: task[key]
                    for key in (
                        "id",
                        "title",
                        "purpose",
                        "result_key",
                        "input_kind",
                        "applies_when",
                        "review_status",
                    )
                }
                | (
                    {"answer_contract": task["answer_contract"]}
                    if task.get("answer_contract")
                    else {}
                )
                for task in self.content.tasks.values()
            ],
            "available_capabilities": [
                {
                    key: item[key]
                    for key in (
                        "id",
                        "name",
                        "version",
                        "description",
                        "review_status",
                        "patient_form",
                    )
                }
                | {
                    "proposal_input_schema": {**item["input_schema"], "required": []},
                    "execution_input_schema": item["input_schema"],
                }
                for item in self.content.registry.descriptions()
                if item.get("patient_form")
            ],
            "adaptive_question_risk_tags": {
                name: list(cues) for name, cues in RISK_TAG_CUES.items()
            },
            "content_versions": self.content.versions,
        }
        self._prompt_variants: Dict[bool, tuple] = {}
        self.system_prompt, self.prefix_sha256 = self._current_prefix()

    def _subagent_mode_for(self, task_kind) -> str:
        """Effective subagent mode for one task kind, resolved live.

        Current coordinators resolve per-kind overrides through
        ``mode_for()`` (an override wins; kinds without one fall back
        to the coordinator's global mode). Older coordinators — and the
        test doubles written against them — carry only the global
        ``mode``, which is then the effective mode for every kind. No
        coordinator at all reads as "off".
        """
        coordinator = self.subagent_coordinator
        if coordinator is None:
            return "off"
        mode_for = getattr(coordinator, "mode_for", None)
        if callable(mode_for):
            return mode_for(task_kind)
        return getattr(coordinator, "mode", "off")

    def _spawn_advertised(self) -> bool:
        """Whether spawn_subagent may be advertised *right now*.

        Advertise spawn_subagent only when a coordinator is actually
        wired in and at least one *model-proposed* kind is enabled:
        evidence_lookup or second_opinion. case_digest is triggered by
        the controller and followup_batch by the server, so enabling
        only those must not put the tool in front of the model. Listing
        an unavailable tool invites proposals that can only fail closed
        at execution time (the _execute kind whitelist below).
        """
        return any(
            self._subagent_mode_for(kind) != "off"
            for kind in MODEL_SPAWN_TASK_KINDS
        )

    def _effective_static_context(self):
        """The catalog actually serialized into the system prompt.

        Identical to ``static_context`` except the tool list, which is
        resolved against the live coordinator (see the __init__ note):
        the base catalog is immutable content, the advertisement is not.
        """
        if self._spawn_advertised():
            return self.static_context
        context = dict(self.static_context)
        context["tools"] = [
            tool
            for tool in self.static_context["tools"]
            if tool.get("name") != "spawn_subagent"
        ]
        return context

    def _current_prefix(self):
        """(system_prompt, sha256) for the currently advertised catalog.

        Exactly two variants exist per engine (spawn advertised or not);
        each is built once and cached, so the prefix stays byte-stable
        within a variant — the property provider KV-prefix reuse relies
        on. ``system_prompt``/``prefix_sha256`` are refreshed to the live
        variant on every call, so the agent.started event record and
        offline evaluators reading those attributes see the prefix the
        model is actually being sent, not the construction-time one.
        """
        advertised = self._spawn_advertised()
        variant = self._prompt_variants.get(advertised)
        if variant is None:
            prompt = (
                self.prompt
                + "\n\n以下是本会谈固定的工具与内容目录（JSON）。动态输入中的候选范围优先。\n"
                + self._json(self._effective_static_context())
            )
            variant = (prompt, hashlib.sha256(prompt.encode()).hexdigest())
            self._prompt_variants[advertised] = variant
        self.system_prompt, self.prefix_sha256 = variant
        return variant

    @staticmethod
    def _json(value):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _value_contract(question):
        if question.input == "age":
            present = {
                "type": "number",
                "unit": "years",
                "minimum": 0,
                "maximum": 120,
                "examples": [32, 0.667],
            }
        elif question.input == "choice":
            present = {
                "type": "string",
                "enum": [o.value for o in question.options if o.value != "unknown"],
            }
        elif question.input == "yes_no_unknown":
            present = {"const": True}
        elif question.input == "severity":
            present = {"types": ["number", "string"], "numeric_range": [0, 10]}
        else:
            present = {"type": "string", "minLength": 1, "maxLength": 2000}
        return {"present": present, "absent_or_unknown_value": None}

    def create_session(self, request_id=None):
        trace_id = str(uuid4())
        state = self._create_state(trace_id, request_id)
        try:
            state.active_question = "chief_complaint"
            state.asked_questions = ["chief_complaint"]
            reply = self._ask(
                state, self.content.question("chief_complaint"), "先听取主诉，自然叙述后再补关键缺口"
            )
            reply.message = "你好，这次是你自己还是替家人咨询？请先说说最困扰的问题，以及大约从什么时候开始。"
            self._record_reply(state, reply, "session_start", "开放式开场")
            self.events.append(
                Event(
                    state.session_id,
                    "agent.started",
                    {
                        "backend": self.backend.name,
                        "reply": reply.to_dict(),
                        "policy_version": "medical-tools-v0.11.17",
                        "prompt_sha256": self._current_prefix()[1],
                        "prompt_layout": "static-catalog-prefix/v1",
                    },
                )
            )
        finally:
            self.events.clear_trace(state.session_id)
        return state, reply

    # Patient-facing age prompt for non-child subjects. The catalog text
    # ("婴幼儿请尽量写清月龄…") is what the *model* sees in its catalog;
    # presenting it verbatim to a 32- or 65-year-old asking for themselves
    # reads as a template bug (live-eval cases 1/5/9). Child consultations
    # keep the catalog wording, where the months phrasing is the point.
    _ADULT_AGE_PROMPT = "你多大？可直接写年龄，例如 32 岁。"

    def _ask(self, state: CaseState, question: Question, reason: str) -> AgentReply:
        reply = super()._ask(state, question, reason)
        if question.fact_key == "age_years" and not self._subject_is_child(state):
            reply.message = self._ADULT_AGE_PROMPT
        # Batch C BURDEN (Y-10): a fixed composite prompt must not
        # re-list items the patient already denied. The filter runs
        # only on pathway-detail composites: the emergency-screen list
        # is a safety list and is never trimmed, whatever was denied.
        if question.fact_key in DETAIL_FACT_KEYS:
            reply.message = _strip_denied_items(reply.message, state.facts)
        return reply

    def _screening_list_text(self) -> Optional[str]:
        """The registered emergency-screen item list, from questions.json.

        The list is read from the registered prompt at render time, never
        restated from a second copy: O-06's list-less screening question
        ("以下任一情况" with no items) is repaired by appending exactly
        this registry text.
        """
        try:
            prompt = self.content.question("emergency_screen").prompt
        except Exception:
            return None
        if "：" in prompt:
            items = prompt.split("：", 1)[1].strip().rstrip("？?")
            return items or None
        return None

    def _ensure_screening_list(self, message: str) -> str:
        """Append the screening item list when the text names none of it."""
        items = self._screening_list_text()
        if not items:
            return message
        alternatives: List[str] = []
        for item in re.split(r"[、，,]", items):
            alternatives.extend(part for part in item.split("或") if len(part) >= 2)
        if any(alt in message for alt in alternatives):
            return message
        return message + f"（具体包括：{items}）"

    def _completeness_gaps(self, state: CaseState) -> List[str]:
        """Chief-complaint key items a recorded detail fact still lacks.

        Batch C BURDEN (G-07): a constipation case whose gi_details was
        recorded but never mentions straining effort or weight change
        must not reach its first summary draft on the patient's own
        correction. The check is a completeness gate, not a clinical
        threshold: it only names which recorded-detail sub-items are
        still missing so they can be asked first.
        """
        chief_texts = [
            text for text in self.content.chief_complaint_texts(state) if text
        ]
        chief = " ".join(chief_texts)
        if not any(
            term in chief
            for term in ("便秘", "排便困难", "大便干", "排便费力", "拉不出", "解不出")
        ):
            return []
        detail = state.facts.get("gi_details")
        if detail is None:
            return []
        text = ""
        if isinstance(detail.value, str):
            text += detail.value
        if detail.evidence:
            text += str(detail.evidence)
        gaps: List[str] = []
        if not any(term in text for term in ("费力", "费劲", "用力", "困难")):
            gaps.append("排便费力程度")
        if not any(
            term in text
            for term in ("体重", "瘦", "胖", "轻了", "重了", "没变", "不变", "下降", "增加")
        ):
            gaps.append("体重变化")
        return gaps

    @staticmethod
    def _subject_is_child(state: CaseState) -> bool:
        if state.get("consult_for") == "child":
            return True
        profile = subject_profile(state)
        reference = (profile or {}).get("reference") or {}
        attributes = reference.get("attributes") or []
        return bool(attributes) and attributes[0] == "child"

    def _interrupt_or_reject(self, state, text, explicit_help=False):
        active = self._active_turns.get(state.session_id)
        if active and not active.cancelled.is_set():
            if not explicit_help and not self.triage.scan_text(text):
                raise ConsultationBusyError("上一条信息正在处理，这条补充尚未保存。请稍后再提交。")
            active.cancelled.set()
            self.events.append(
                Event(
                    state.session_id,
                    "agent.turn_preempted",
                    {"reason": "emergency_input", "decision_turn": active.identity},
                    turn_id=active.turn_id or None,
                )
            )

    def submit(self, session_id, user_input):
        state = self.state(session_id)
        with self._session_locks[session_id]:
            self._interrupt_or_reject(
                state,
                self._location_input_text(user_input),
                user_input.choice_value == "request_emergency_help",
            )
            turn = DecisionTurn()
            self._active_turns[session_id] = turn
            trace_id = str(uuid4())
            decision_id = str(uuid4())
            self.events.bind_trace(
                session_id,
                trace_id,
                user_input.request_id,
                decision_id,
            )
            try:
                return self._submit(state, user_input)
            finally:
                self.events.clear_trace(session_id)
                if self._active_turns.get(session_id) is turn:
                    self._active_turns.pop(session_id, None)

    def _submit(self, state: CaseState, user_input: UserInput) -> AgentReply:
        if user_input.choice_value == "request_emergency_help":
            return self._requested_emergency_help(state, user_input.text)
        expected = f"{state.session_id}:{state.revision}"
        if (
            user_input.expected_action_id
            and user_input.expected_action_id != expected
            and not self.triage.scan_text(self._location_input_text(user_input))
        ):
            raise StaleActionError("回答对应的页面已更新，请刷新当前问题")
        text = (
            state.pending_input
            if user_input.choice_value == "retry_turn" and state.pending_input
            else user_input.text.strip()
        )
        adaptive_answer = self._prepare_adaptive_answer(state, user_input, text)
        if adaptive_answer:
            text = adaptive_answer["text"]
        location_input_text = self._location_input_text(user_input)
        if self.triage.scan_text(location_input_text):
            text = location_input_text
        prepared_location = None
        if not self.triage.scan_text(text):
            prepared_location = self._prepare_location_answer(state, user_input)
            if prepared_location:
                text = prepared_location["text"]
            self._check_subject_action(state, user_input)
            self._check_calculation_action(state, user_input)
            self._check_observation_action(state, user_input)
            self._check_task_conversation_action(state, user_input)
            self._check_location_revision_action(state, user_input)
        turn_id = str(uuid4())
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {
                    "text": text,
                    **(
                        {
                            "adaptive_answer": {
                                "selected": adaptive_answer["selected"],
                                "kind": adaptive_answer["kind"],
                                "risk_tags": adaptive_answer["risk_tags"],
                            }
                        }
                        if adaptive_answer
                        else {}
                    ),
                    **(
                        {"structured_input": prepared_location["structured_input"]}
                        if prepared_location
                        else {}
                    ),
                },
                turn_id=turn_id,
            )
        )
        state.transcript.append({"role": "user", "content": text})
        # Batch B worry capture: the adaptive engine does not share the
        # deterministic engine's _begin_turn, so its own _submit calls
        # the same shared helper here, on the finalized turn text, with
        # the same first-wins / never-patient_goal semantics.
        self._capture_worry(state, text, turn_id)

        # Emergency evaluation precedes model invocation, including closed contexts.
        risk = self.triage.assess(
            state,
            text,
            adaptive_answer["risk_tags"] if adaptive_answer else (),
        )
        if state.closed_reason == "emergency_route":
            risk = state.risk
        state.risk = risk
        if user_input.choice_value == "confirm_emergency_clarification":
            confirmed = self._confirm_emergency_clarification(state, turn_id)
            if confirmed is not None:
                return self._deliver(state, confirmed, turn_id)
        if user_input.choice_value == "deny_emergency_clarification":
            pending = self._emergency_clarification_pending(state)
            if pending is not None:
                # P5 (C-07/C-08): the denial negates the danger signals,
                # not the chief-complaint statement itself. Remember the
                # clarified turn's text, scoped to this denial turn's
                # input, so the model round that follows may quote it
                # for chief facts that never landed (see _evidence_sources
                # in agent_tools). The scope key (for_input) keeps the
                # exception inert on every later turn: the current-turn
                # evidence rule is otherwise untouched.
                denied_text = pending.get("text")
                if isinstance(denied_text, str) and denied_text.strip():
                    state.runtime_snapshot[
                        "emergency_clarification_denied"
                    ] = {"text": denied_text, "for_input": text}
                state.runtime_snapshot.pop("emergency_clarification", None)
                self.events.append(
                    Event(
                        state.session_id,
                        "emergency.clarification_denied",
                        {},
                        turn_id=turn_id,
                    )
                )
        # P5 (round-2 ④-4): the urgent gate is NOT evaluated here, before
        # this round's record_facts. The old pre-model gate closed on
        # "asked" minimum facts (see _urgent_gate_fact_satisfied) while
        # the closing round's answer was still unrecorded, so the
        # associated_symptoms / patient_goal answer never reached the
        # final state. Mirror the deterministic order (record, then gate,
        # then plan): _run evaluates _urgent_or_defer after each
        # record_facts execution (and before delivering a model-planned
        # question), where record_facts has already re-assessed risk from
        # the landed facts. The gate's trigger conditions and minimum
        # facts are unchanged; only the evaluation point moved. The
        # pending-calculation/self-check branch below keeps its own gate:
        # that path resumes pending work rather than recording a fresh
        # model answer, so its gate is not a closing-round answer gate.
        if risk.level == RiskLevel.EMERGENCY:
            action = self._emergency_clarification_action(
                state,
                text,
                adaptive_answer["risk_tags"] if adaptive_answer else (),
            )
            if action == "clarify":
                return self._deliver(
                    state,
                    self._emergency_clarification_reply(state, text, turn_id),
                    turn_id,
                )
            if action == "confirmed":
                return self._deliver(
                    state,
                    self._emergency_reply(
                        state, risk.triggers, include_clarification=True
                    ),
                    turn_id,
                )
            return self._deliver(
                state, self._emergency_reply(state, risk.triggers), turn_id
            )
        # (P5 ④-4: the adaptive-answer duplicate of the pre-model urgent
        # gate is removed for the same record-first reason; _run's
        # post-record gate covers it.)
        revision_reply = self._handle_location_revision_action(
            state, user_input, text, turn_id
        )
        if revision_reply is not None:
            return self._deliver(state, revision_reply, turn_id)
        if state.phase == Phase.CLOSED:
            return self._deliver(
                state,
                AgentReply("这次会谈已结束。新问题可重新开始问诊。", Phase.CLOSED, kind="closed"),
                turn_id,
            )
        if state.risk.level == RiskLevel.URGENT and (
            state.pending_calculation
            or state.calculation_resume
            or state.self_check_conversation
        ):
            gated = self._urgent_or_defer(state, turn_id)
            if gated is not None:
                return self._deliver(state, gated, turn_id)
        subject_reply = self._subject_control(state, user_input, text, turn_id)
        if subject_reply is not None:
            return self._deliver(state, subject_reply, turn_id)
        if prepared_location:
            revised = self._complete_location_revision(
                state, prepared_location, turn_id
            )
            if revised is not None:
                if (
                    state.observation_feedback
                    and state.observation_feedback["has_supplement"]
                ):
                    return self._run(state, text, turn_id)
                return self._deliver(state, revised, turn_id)
            self._record_location_answer(state, prepared_location, turn_id)
            return self._run(state, text, turn_id)
        task_action = self._handle_task_conversation_action(state, user_input, turn_id)
        if isinstance(task_action, AgentReply):
            return self._deliver(state, task_action, turn_id)
        if task_action is not None:
            return self._run(state, task_action["text"], task_action["source_turn_id"])
        if user_input.choice_value and user_input.choice_value.startswith(
            "process_calculation_supplement:"
        ):
            return self._process_calculation_followup(state)
        if (
            user_input.choice_value == "retry_turn"
            and state.calculation_feedback
            and state.calculation_feedback["processing"]
        ):
            return self._process_calculation_followup(state)
        if user_input.choice_value == "retry_turn" and state.observation_feedback:
            feedback = state.observation_feedback
            return self._run(state, feedback["text"], feedback["source_turn_id"])
        if state.calculation_feedback:
            state.calculation_feedback["processing"] = False
        if state.observation_feedback:
            state.observation_feedback = None
        # D-03/D-04: a confirmed summary must enter the conclusion flow
        # and must not be re-suspended by a self-check step. This early
        # confirm runs before observation handling; the existing confirm
        # branch below remains as the fallback when feedback is pending.
        if (
            state.phase == Phase.SUMMARY
            and (
                user_input.choice_value == "confirm_summary"
                or is_summary_confirmation_text(text)
            )
            and not state.calculation_feedback
            and not state.observation_feedback
        ):
            return self._deliver(
                state, self._confirmed_summary_reply(state, turn_id), turn_id
            )
        observation_reply = self._handle_observation_action(
            state, user_input, text, turn_id
        )
        if observation_reply is not None:
            if (
                state.observation_feedback
                and state.observation_feedback.get("has_supplement", True)
                and observation_reply.kind
                not in {
                    "urgent",
                    "emergency",
                    "closed",
                }
            ):
                feedback = state.observation_feedback
                self._clear_calculation(
                    state, "supplemental_patient_information", turn_id
                )
                state.pending_input = feedback["text"]
                return self._run(state, feedback["text"], feedback["source_turn_id"])
            return self._deliver(state, observation_reply, turn_id)
        calculation_reply = self._handle_calculation_action(
            state, user_input, text, turn_id
        )
        if (
            user_input.choice_value in {"skip_calculation", "resume_after_calculation"}
            and state.calculation_feedback
        ):
            return self._process_calculation_followup(state)
        if calculation_reply is not None:
            return self._deliver(state, calculation_reply, turn_id)
        self._sync_task_requests(state, text, turn_id)
        if state.active_question == "emergency_screen" and user_input.choice_value in {
            "yes",
            "no",
            "unknown",
        }:
            interpreted = self.interpreter.interpret(
                self.content.question("emergency_screen"), text, user_input.choice_value
            )
            assertion = interpreted.assertion.value
            # A clicked fixed option is controller-owned evidence, not an LLM
            # interpretation. Raw urgent text has already passed the priority gate.
            source = (
                Source.CAREGIVER
                if state.get("consult_for") in {"child", "other_adult"}
                else Source.PATIENT
            )
            state.record(
                Fact(
                    key="emergency_screen",
                    value=True if assertion == "present" else None,
                    assertion=Assertion(assertion),
                    source=source,
                    evidence=text,
                    turn_id=turn_id,
                )
            )
            self.events.append(
                Event(
                    state.session_id,
                    "fact.recorded",
                    state.facts["emergency_screen"].to_dict(),
                    turn_id=turn_id,
                )
            )
            state.active_question = None
            state.risk = self.triage.assess(state, text)
            if state.risk.level == RiskLevel.EMERGENCY:
                action = self._emergency_clarification_action(state, text, ())
                if action == "clarify":
                    return self._deliver(
                        state,
                        self._emergency_clarification_reply(state, text, turn_id),
                        turn_id,
                    )
                if action == "confirmed":
                    return self._deliver(
                        state,
                        self._emergency_reply(
                            state, state.risk.triggers, include_clarification=True
                        ),
                        turn_id,
                    )
                return self._deliver(
                    state, self._emergency_reply(state, state.risk.triggers), turn_id
                )
            # Only canonical option-only answers take the zero-model fast path.
            # Mixed free text must still reach the planner for fact extraction.
            if text.strip(" 。！!，,") in {"都没有", "这些危险情况都没有", "没有上述情况", "没有危险信号"}:
                pending_reply = self._offer_pending_self_check(state)
                if pending_reply:
                    return self._deliver(state, pending_reply, turn_id)
        if state.phase == Phase.SUMMARY and (
            user_input.choice_value == "confirm_summary"
            or is_summary_confirmation_text(text)
        ):
            return self._deliver(
                state, self._confirmed_summary_reply(state, turn_id), turn_id
            )
        if state.active_task:
            from .task_conversation import is_observation_entry

            if is_observation_entry(
                self.content.tasks[state.active_task], text, user_input.choice_value
            ):
                failure = self._try_task_result(
                    state, text, user_input.choice_value, turn_id
                )
                if failure:
                    return self._deliver(state, failure, turn_id)
            else:
                self._suspend_task_conversation(state, turn_id)
            state.risk = self.triage.assess(state, text)
            if state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    return self._deliver(state, gated, turn_id)
        state.pending_input = text
        return self._run(state, text, turn_id)

    def _confirmed_summary_reply(self, state, turn_id):
        """Shared user-confirmed summary close (button or free text)."""
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.active_question = None
        reply = AgentReply(
            "你已确认本次摘要，会谈现已结束。可以把它提供给接诊医生；情况有变化时请重新评估。",
            Phase.CLOSED,
            kind="closed",
            summary=self._summary_payload(
                state, review_status="confirmed_by_user"
            ),
        )
        # Parity with the deterministic pipeline's confirm handler:
        # a user-confirmed close also derives the follow-up plan.
        self._schedule_follow_up(state, reply, turn_id)
        return reply

    @staticmethod
    def _is_declined_proposal(state, text, item) -> bool:
        """Whether a record_facts item is a refusal, not an unknown.

        Narrow by construction: assertion unknown, value null, evidence a
        verbatim refusal quote from this turn, and the field not already
        recorded (unless an explicit correction). Anything else keeps
        the validator's existing unknown rules.
        """
        key = item.get("key")
        if not isinstance(key, str):
            return False
        if item.get("assertion") != "unknown":
            return False
        if item.get("value") is not None:
            return False
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return False
        if evidence not in text:
            return False
        if not is_refusal_text(evidence):
            return False
        if key in state.facts and not item.get("correction"):
            return False
        return True

    def _prepare_adaptive_answer(self, state, user_input, text):
        choice = user_input.choice_value
        if not choice or not choice.startswith("adaptive_"):
            return None
        latest = self._latest_replies.get(state.session_id)
        if not latest or not latest.adaptive_question:
            raise StaleActionError("动态问题已经更新，请按当前问题重新回答")
        try:
            return project_selected_answer(latest.adaptive_question, choice, text)
        except AdaptiveQuestionError as exc:
            raise StaleActionError(str(exc)) from exc

    @staticmethod
    def _fact_gates_pass(state: CaseState, question) -> bool:
        """Fact-dependent eligibility, evaluated at candidate time.

        Shared implementation: ``knowledge.question_fact_gates_pass``
        (the deterministic engine applies the same gates). The gate
        only fires on known facts — an unknown age keeps the question
        eligible.
        """
        return question_fact_gates_pass(question, state.facts)

    def _track_incidental(self, state: CaseState, proposal, active_before) -> None:
        """Flag screening facts that were only mentioned in passing.

        A screening field (SCREENING_FIELDS) recorded while a *different*
        question was active — or none — was volunteered, not screened. The
        fact still counts as recorded (it leaves missing_required), but the
        context keeps flagging it via ``incidentally_recorded`` so the model
        can still run the formal screen. Formally answering the field's own
        question, or a deliberate correction, clears the flag.
        """
        key = proposal["key"]
        if key not in SCREENING_FIELDS:
            return
        marks = self._incidental_records.setdefault(state.session_id, set())
        if proposal.get("correction") or (
            active_before is not None and active_before.fact_key == key
        ):
            marks.discard(key)
        else:
            marks.add(key)

    def _pathway_exploration(self, state: CaseState):
        """Per triggered pathway: were its details actually probed?

        Triggering reuses the reasoning core's rule (chief complaint
        contains an unnegated pathway term). A triggered pathway counts
        as explored when any of these holds:
        - one of its detail facts is recorded (any assertion — being
          asked and answered "不知道" is a probe, same semantics as
          missing_required), or its detail question was asked at all;
        - every detail question is excluded by known facts (an age/sex
          gate), so nothing askable remains;
        - the rule proposer, run over current facts, holds at least one
          evidence entry for one of the pathway's hypotheses that rests
          on a fact other than the chief complaint itself (this is the
          only signal for pathways with no dedicated detail field, e.g.
          chest, and a backstop for evidence gathered via other fields).

        Testability note (healthcheck P2-A-D5): the chest gate is
        submit-unreachable by design — a chest complaint is intercepted
        by the emergency triage in _submit before _run ever sees it, and
        that precedence must not be weakened. Its gate behaviour is
        therefore verified by calling _execute(review_summary) directly
        on a constructed state (see test_pathway_close_gate.py), never
        by routing a chest complaint through submit().
        """
        chief = str(state.get("chief_complaint", ""))
        if not chief:
            return []
        workspace = self._pathway_proposer.propose(state)
        exploration = []
        for pathway in self._pathways:
            terms = pathway.get("chief_complaint_contains_any", [])
            if not any(contains_unnegated(chief, term) for term in terms):
                continue
            detail_facts = self._pathway_detail_facts.get(pathway["id"], [])
            explored = any(
                fact_key in state.facts
                or self._questions_by_fact[fact_key].id in state.asked_questions
                for fact_key in detail_facts
            )
            if not explored and detail_facts:
                explored = all(
                    not self._fact_gates_pass(
                        state, self._questions_by_fact[fact_key]
                    )
                    for fact_key in detail_facts
                )
            if not explored:
                explored = any(
                    ref.fact_key != "chief_complaint"
                    for raw in pathway.get("hypotheses", [])
                    if (hypothesis := workspace.hypotheses.get(raw["id"]))
                    is not None
                    for ref in [
                        *hypothesis.evidence_for,
                        *hypothesis.evidence_against,
                    ]
                )
            exploration.append(
                {
                    "pathway": pathway["id"],
                    "detail_fields": detail_facts,
                    "explored": explored,
                }
            )
        return exploration

    @staticmethod
    def _unexplored_pathway_error(unexplored) -> ProposalError:
        parts = []
        for entry in unexplored:
            if entry["detail_fields"]:
                fields = "、".join(entry["detail_fields"])
            else:
                fields = "该通路无专属细节字段，需伴随症状、病史等鉴别证据落账"
            parts.append(f"{entry['pathway']} 通路（细节字段：{fields}）")
        return ProposalError(
            "主诉通路尚未探明，不能直接核对摘要："
            + "；".join(parts)
            + "。请先用 ask_question/ask_patient 探明对应细节字段；"
            "若临床上确不适用，重新调用 review_summary 并填写 abstain_reason 说明理由。"
        )

    def _pathway_gate_redirect(self, state: CaseState, turn_id: str):
        """Controller close-gate for the host-driven auto-summary.

        Mirrors the review_summary tool gate: instead of summarizing an
        unexplored triggered pathway away, ask its first unrecorded,
        still-eligible detail question directly. _ask registers the
        question as asked, so a patient who cannot elaborate further is
        not asked in a loop — the next close attempt sees the pathway as
        attempted. Pathways without a detail field cannot be probed
        deterministically here and are left to the tool gate and the
        triage layers.
        """
        for entry in self._pathway_exploration(state):
            if entry["explored"]:
                continue
            for fact_key in entry["detail_fields"]:
                if fact_key in state.facts:
                    continue
                question = self._questions_by_fact[fact_key]
                if not self._fact_gates_pass(state, question):
                    continue
                self.events.append(
                    Event(
                        state.session_id,
                        "review.pathway_gate_redirect",
                        {"pathway": entry["pathway"], "detail_field": fact_key},
                        turn_id=turn_id,
                    )
                )
                return self._ask(
                    state,
                    question,
                    "主诉通路尚未探明，控制器在摘要前先补问其细节字段",
                )
        return None

    def _controller_summary(self, state, text, turn_id):
        """Controller auto-summary with a fallback that never kills the
        session (C-06/J-02).

        The model-called review_summary path already survives a gate
        rejection (the error returns as a tool result and the model can
        abstain); the controller's own auto-summary used to let the
        ProposalError propagate out of _run and strand the session with
        no conclusion. Order: detail-question redirect, then the plain
        summary, then one discrimination probe for detail-less pathways
        (chest: associated symptoms / relevant history — no new chest
        detail question is invented, per the batch A scope), then an
        abstain re-call when probing is genuinely inapplicable, then a
        last-resort return to planning. A ProposalError must never
        reach the driver from here.
        """
        redirect = self._pathway_gate_redirect(state, turn_id)
        if redirect is not None:
            return redirect
        try:
            return self._execute(
                state, {"tool": "review_summary", "arguments": {}}, text, turn_id
            )
        except ProposalError:
            pass
        for fact_key in ("associated_symptoms", "relevant_history"):
            if fact_key in state.facts:
                continue
            question = self._questions_by_fact.get(fact_key)
            if question is None or question.id in state.asked_questions:
                continue
            if not self.content.question_applies_in_state(question, state):
                continue
            if not self._fact_gates_pass(state, question):
                continue
            self.events.append(
                Event(
                    state.session_id,
                    "review.pathway_discrimination_redirect",
                    {"detail_field": fact_key},
                    turn_id=turn_id,
                )
            )
            return self._ask(
                state,
                question,
                "主诉通路尚未探明，控制器在摘要前先补问鉴别字段",
            )
        try:
            return self._execute(
                state,
                {
                    "tool": "review_summary",
                    "arguments": {
                        "abstain_reason": (
                            "该通路无专属细节字段，已记录的伴随症状、病史等"
                            "鉴别证据不足以单独探明，摘要前不再追问该通路细节；"
                            "结论与随访仍按已记录事实生成。"
                        )
                    },
                },
                text,
                turn_id,
            )
        except ProposalError:
            pass
        if state.self_check_conversation:
            try:
                return self._resume_task_conversation(state)
            except ValueError:
                # A resume that fails (e.g. the task's basis changed and
                # the frame was cleared by the resume itself) must fall
                # back to planning, never escape to the driver. Note: a
                # still-blocked frame can raise again inside _plan_next;
                # that path keeps the block visible rather than silently
                # dropping the suspended step.
                pass
        self.events.append(
            Event(
                state.session_id,
                "review.controller_fallback_exhausted",
                {},
                turn_id=turn_id,
            )
        )
        return self._plan_next(state)

    def _candidate_questions(self, state: CaseState, chief: str):
        return [
            q
            for q in self.content.questions
            if q.fact_key not in state.facts
            and self.content.question_applies_in_state(q, state)
            and self._fact_gates_pass(state, q)
        ]

    def _context(self, state: CaseState, text: str) -> Dict[str, Any]:
        chief = str(state.get("chief_complaint", ""))
        candidates = self._candidate_questions(state, chief)
        active_question = next(
            (q for q in self.content.questions if q.id == state.active_question),
            None,
        )
        recent = state.transcript[-16:]
        recent_user_texts = {
            item["content"] for item in recent if item["role"] == "user"
        }
        facts = {
            k: {
                name: value
                for name, value in v.to_dict().items()
                if name
                in {
                    "key",
                    "value",
                    "assertion",
                    "source",
                    "quality",
                    "evidence",
                    "structured_input",
                }
            }
            for k, v in state.facts.items()
        }
        earlier = {}
        source_turns = self._source_turns(state)
        for key, fact in state.facts.items():
            context = self._fact_provenance(fact, source_turns).get("context")
            if context and context["text"] not in recent_user_texts:
                facts[key]["source_context_id"] = context["turn_id"]
                earlier[context["turn_id"]] = context["text"]
        return {
            "latest_user_text": text,
            "active_question": state.active_question,
            "active_task": state.active_task,
            "self_check_conversation": self._task_conversation_context(state),
            "consultation_subject": subject_profile(state),
            "facts": facts,
            **(
                {
                    "earlier_source_turns": [
                        {"id": key, "text": value} for key, value in earlier.items()
                    ]
                }
                if earlier
                else {}
            ),
            # patient_goal is a blocking field like any other: the prompt
            # requires it to be recorded before review_summary, and
            # excluding it here let an abstained summary close with the
            # patient's goal never recorded (healthcheck P1-A-D1). The
            # controller auto-summary path only fires after the goal
            # answer itself lands, so it still sees an empty list there.
            "missing_required": [
                q.fact_key for q in candidates if q.blocking
            ],
            **(
                {"pathway_exploration": exploration}
                if (exploration := self._pathway_exploration(state))
                else {}
            ),
            "questions": [{"id": q.id, "field": q.fact_key} for q in candidates],
            **(
                {
                    "incidentally_recorded": sorted(
                        self._incidental_records.get(state.session_id, ())
                    )
                }
                if self._incidental_records.get(state.session_id)
                else {}
            ),
            **(
                {"active_question_field_already_recorded": active_question.fact_key}
                if active_question is not None
                and active_question.fact_key in state.facts
                else {}
            ),
            "available_self_checks": [
                {key: t[key] for key in ("id", "title", "result_key")}
                for t in self.content.applicable_tasks(chief)
                if t["result_key"] not in state.facts
                and not self._self_check_block_reason(state)
            ],
            "self_check_block_reason": self._self_check_block_reason(state),
            "requestable_self_checks": [
                {key: task[key] for key in ("id", "title", "result_key")}
                for task in self.content.applicable_tasks(chief)
                if task["result_key"] not in state.facts
            ],
            "pending_self_checks": state.pending_self_checks,
            "repeatable_self_checks": [
                {key: task[key] for key in ("id", "title", "result_key")}
                for task in self.content.applicable_tasks(chief)
                if task.get("repeatable") and task["result_key"] in state.facts
            ],
            "recent_observation_attempts": state.observation_attempts[-8:],
            "observation_completion": self._observation_completion_context(state),
            "observation_fields_read_only": sorted(
                {t["result_key"] for t in self.content.tasks.values()}
            ),
            "completed_observation_with_supplement": (
                state.observation_feedback
                if state.observation_feedback
                and state.observation_feedback.get("has_supplement", True)
                else None
            ),
            "calculation_request_with_supplement": self._calculation_followup_context(
                state
            ),
            "transcript": recent,
            "calculation_drafts": [
                {key: value for key, value in result.items() if key != "presentation"}
                for result in state.calculations[-3:]
            ],
            "patient_confirmed_report_items": [
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "reported_value",
                        "unit",
                        "reference_range",
                        "record_title",
                        "measured_at",
                        "source",
                    )
                }
                for item in state.report_items[-20:]
            ],
            # P2c: archive reference sits next to the confirmed report
            # items, gated by the same session switch. It is context
            # only and never lands in state.facts.
            **(
                {"profile_baseline": dict(state.profile_baseline)}
                if state.baseline_reference_enabled and state.profile_baseline
                else {}
            ),
            **(
                {"background_review": state.background_review["advice"]}
                if state.background_review
                and state.background_review.get("mode") == "advisory"
                and state.background_review.get("status") == "completed"
                and state.background_review.get("source_revision") == state.revision
                and isinstance(state.background_review.get("advice"), dict)
                else {}
            ),
            **(
                {"earlier_digest": state.subagent_digest["digest"]}
                if state.subagent_digest
                and state.subagent_digest.get("status") == "ready"
                and (state.subagent_digest.get("watermark") or {}).get("revision")
                == state.revision
                and isinstance(state.subagent_digest.get("digest"), dict)
                else {}
            ),
            **(
                {
                    "prior_case": {
                        key: prior.get(key)
                        for key in (
                            "chief_complaint",
                            "disposition_level",
                            "disposition_text",
                            "outcome",
                            "outcome_note",
                        )
                    }
                }
                if (
                    prior := (state.runtime_snapshot or {}).get("prior_case")
                )
                else {}
            ),
        }

    def _messages(self, state, text):
        return [
            {"role": "system", "content": self._current_prefix()[0]},
            {"role": "user", "content": self._json(self._context(state, text))},
        ]

    def _run(self, state: CaseState, text: str, turn_id: str) -> AgentReply:
        if state.calculation_feedback:
            state.calculation_feedback["processing"] = (
                text == state.calculation_feedback["text"]
                and turn_id == state.calculation_feedback["source_turn_id"]
            )
        if state.observation_feedback:
            state.observation_feedback["forwarded"] = True
        turn = self._active_turns[state.session_id]
        turn.turn_id = turn_id
        # Controller-triggered case digest (design §5.7 case_digest): once
        # the transcript outgrows the context window, a coordinator-verified
        # digest replaces out-of-window turns via _context. It consumes this
        # round's single spawn allowance, so the model cannot also spawn.
        spawns = 0
        coordinator = self.subagent_coordinator
        if (
            coordinator is not None
            and self._subagent_mode_for("case_digest") == "advisory"
            and len(state.transcript) > 16
            and (
                not state.subagent_digest
                or (state.subagent_digest.get("watermark") or {}).get("revision")
                != state.revision
            )
        ):
            digest = coordinator.run_case_digest(
                state, session_id=state.session_id, cancelled=turn.cancelled
            )
            if digest is not None:
                from .subagents import watermark

                state.subagent_digest = {
                    "watermark": watermark(state),
                    "digest": digest,
                    "status": "ready",
                }
                spawns = 1
        self._round_spawn_counts[state.session_id] = spawns
        messages = self._messages(state, text)
        writes = 0
        # Fact keys rejected by record_facts earlier in this round.
        # A later call containing ONLY such keys is a correction retry
        # of the rejected items (the prompt's "只需修正 rejected 项重提"),
        # not a fresh write: it must not be starved by the two-write
        # cap, and it does not consume fresh-write budget either
        # (healthcheck P1-A-D4). Any call touching a new key is still
        # capped exactly as before, so the cap itself is not relaxed.
        rejected_keys = set()
        for step in range(6):
            if step:
                pending_reply = self._offer_pending_self_check(state)
                if pending_reply:
                    return self._deliver(state, pending_reply, turn_id)
            try:
                command = self._decision_waiter.invoke(
                    self.backend,
                    state.session_id,
                    messages,
                    turn,
                    self._session_conditions[state.session_id],
                    lambda: self.events.append(
                        Event(
                            state.session_id,
                            "agent.decision_discarded",
                            {"reason": "preempted", "decision_turn": turn.identity},
                            turn_id=turn_id,
                        )
                    ),
                )
            except DecisionInterrupted:
                return deepcopy(self._latest_replies[state.session_id])
            except Exception as exc:
                # Repairable decision-envelope errors (healthcheck
                # P1-A-D3) are the model's own malformed output, so
                # they are fed back as a tool_result inside the same
                # 6-step self-correction loop instead of ending the
                # round with a blind service_error. The backend_failed
                # event is still recorded for audit. Anything else
                # keeps the old service_error semantics below, and
                # the loop's exhaustion fallback still applies if the
                # model keeps producing bad envelopes.
                guidance = _decision_envelope_guidance(exc)
                if guidance is not None:
                    self.events.append(
                        Event(
                            state.session_id,
                            "agent.backend_failed",
                            {
                                "error_type": type(exc).__name__,
                                "repairable_envelope": True,
                            },
                            turn_id=turn_id,
                        )
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": self._json(
                                {"tool_result": {"ok": False, "error": guidance}}
                            ),
                        }
                    )
                    continue
                state.pending_input = text
                self.events.append(
                    Event(
                        state.session_id,
                        "agent.backend_failed",
                        {
                            "error_type": type(exc).__name__,
                            "status_code": getattr(exc, "code", None)
                            if type(getattr(exc, "code", None)) is int
                            else None,
                        },
                        turn_id=turn_id,
                    )
                )
                return self._deliver(
                    state,
                    AgentReply(
                        "这次反馈已处理。后续对话暂时不可用，重试即可继续。"
                        if self._observation_completion_context(state)
                        else "这一步的对话服务暂时不可用，已保存的内容会保留。请稍后重试，或先查看目前记录。",
                        state.phase,
                        kind="service_error",
                    ),
                    turn_id,
                )
            self.events.append(
                Event(
                    state.session_id,
                    "tool.requested",
                    {"step": step, **command},
                    turn_id=turn_id,
                )
            )
            goal_answered = False
            try:
                correction_only = False
                if command["tool"] == "record_facts":
                    # Peek at the proposed keys before executing: a
                    # correction-only retry bypasses the cap below.
                    # Malformed shapes fall through as non-correction
                    # and are rejected by _execute's own validation.
                    facts_arg = (
                        command["arguments"].get("facts")
                        if isinstance(command.get("arguments"), dict)
                        else None
                    )
                    proposed_keys = None
                    if isinstance(facts_arg, list) and facts_arg:
                        proposed_keys = []
                        for item in facts_arg:
                            if not isinstance(item, dict) or not isinstance(
                                item.get("key"), str
                            ):
                                proposed_keys = None
                                break
                            proposed_keys.append(item["key"])
                    correction_only = (
                        proposed_keys is not None
                        and bool(proposed_keys)
                        and set(proposed_keys) <= rejected_keys
                    )
                    if writes >= 2 and not correction_only:
                        raise ProposalError("本轮写入次数已达上限，请提问或核对摘要")
                answering_goal = state.active_question == "patient_goal"
                goal_fact_id_before = (
                    state.facts["patient_goal"].fact_id
                    if "patient_goal" in state.facts
                    else None
                )
                result = self._execute(state, command, text, turn_id)
                if command["tool"] == "record_facts":
                    if not correction_only:
                        writes += 1
                    if isinstance(result, dict):
                        for item in result.get("rejected") or []:
                            if isinstance(item, dict) and isinstance(
                                item.get("key"), str
                            ):
                                rejected_keys.add(item["key"])
                    # A rejected key that has since landed (via a later
                    # batch's accepted side) is no longer awaiting
                    # correction; re-proposing it is a fresh overwrite
                    # and must be capped normally.
                    rejected_keys = {
                        key for key in rejected_keys if key not in state.facts
                    }
                    # Judge by what actually landed: under partial recording
                    # the patient_goal item itself may have been rejected.
                    goal_answered = answering_goal and "patient_goal" in state.facts
                    # P5 (round-2 ④-3): a worry sentence filed as
                    # patient_goal is diverted, never booked as the goal.
                    # The single worry contract is worry.py's
                    # is_worry_sentence / extract_worry_object — no local
                    # pattern is invented here. The diverted goal fact is
                    # removed from facts (history keeps the audit trail),
                    # the worry lands in patient_worry only, goal_answered
                    # stays False, and the goal question stays active so
                    # the patient's real goal is still asked afterwards.
                    # This applies to any newly landed goal value, not
                    # only the answering-goal round: a worry must never
                    # occupy the goal slot.
                    if command["tool"] == "record_facts":
                        goal_fact = state.facts.get("patient_goal")
                        if (
                            goal_fact is not None
                            and goal_fact.fact_id != goal_fact_id_before
                        ):
                            goal_text = (
                                goal_fact.value
                                if isinstance(goal_fact.value, str)
                                else ""
                            )
                            if not goal_text and isinstance(
                                goal_fact.evidence, str
                            ):
                                goal_text = goal_fact.evidence
                            worry_obj = (
                                extract_worry_object(goal_text)
                                if goal_text
                                else None
                            )
                            if worry_obj is not None and is_worry_sentence(
                                goal_text
                            ):
                                del state.facts["patient_goal"]
                                if "patient_worry" not in state.facts:
                                    state.record(
                                        Fact(
                                            key="patient_worry",
                                            value=worry_obj,
                                            assertion=Assertion.PRESENT,
                                            source=goal_fact.source,
                                            turn_id=turn_id,
                                            evidence=goal_text,
                                        )
                                    )
                                if answering_goal:
                                    state.active_question = "patient_goal"
                                    state.active_question_is_clarification = (
                                        False
                                    )
                                goal_answered = False
                                self.events.append(
                                    Event(
                                        state.session_id,
                                        "goal.worry_diverted",
                                        {"object": worry_obj},
                                        turn_id=turn_id,
                                    )
                                )
            except SubjectBoundaryError as exc:
                self.events.append(
                    Event(
                        state.session_id,
                        "tool.completed",
                        {
                            "tool": command["tool"],
                            "result": {"ok": False, "error": str(exc)},
                        },
                        turn_id=turn_id,
                    )
                )
                return self._deliver(
                    state,
                    self._subject_change_reply(state, exc.requested, text, turn_id),
                    turn_id,
                )
            except (ValueError, KeyError, TypeError) as exc:
                # A wholly rejected record_facts batch raises instead
                # of returning a partial result, but its items are
                # rejected all the same: seed them so a later
                # correction-only retry is not starved either. (The
                # guarded access matters: the failure itself may be a
                # malformed command with no "tool" key.)
                if isinstance(command, dict) and command.get("tool") == "record_facts":
                    for verdict in getattr(exc, "verdicts", None) or []:
                        if (
                            isinstance(verdict, dict)
                            and not verdict.get("ok")
                            and isinstance(verdict.get("key"), str)
                        ):
                            rejected_keys.add(verdict["key"])
                result = {"ok": False, "error": str(exc)}
            if isinstance(result, AgentReply):
                self.events.append(
                    Event(
                        state.session_id,
                        "tool.completed",
                        {"tool": command["tool"], "terminal": True},
                        turn_id=turn_id,
                    )
                )
                # P5 (④-4, record-first): a model-planned question is
                # the plan step, so the urgent gate runs before it is
                # delivered — never before this round's record_facts.
                # A passing gate closes urgently (with any facts this
                # round already landed); a deferring gate stands risk
                # down, so the question itself stays deliverable.
                # Only questions are intercepted: subject/task/self-check
                # replies keep their own _submit handlers and gates.
                if (
                    result.kind == "question"
                    and state.risk.level == RiskLevel.URGENT
                ):
                    gated = self._urgent_or_defer(state, turn_id)
                    if gated is not None:
                        return self._deliver(state, gated, turn_id)
                return self._deliver(state, result, turn_id)
            self.events.append(
                Event(
                    state.session_id,
                    "tool.completed",
                    {"tool": command["tool"], "result": result},
                    turn_id=turn_id,
                )
            )
            messages.append({"role": "assistant", "content": self._json(command)})
            messages.append(
                {"role": "user", "content": self._json({"tool_result": result})}
            )
            if state.risk.level == RiskLevel.EMERGENCY:
                action = self._emergency_clarification_action(state, text, ())
                if action == "clarify":
                    return self._deliver(
                        state,
                        self._emergency_clarification_reply(state, text, turn_id),
                        turn_id,
                    )
                if action == "confirmed":
                    return self._deliver(
                        state,
                        self._emergency_reply(
                            state, state.risk.triggers, include_clarification=True
                        ),
                        turn_id,
                    )
                return self._deliver(
                    state, self._emergency_reply(state, state.risk.triggers), turn_id
                )
            if state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    return self._deliver(state, gated, turn_id)
            if (
                goal_answered
                and not state.pending_self_checks
                and not self._context(state, text)["missing_required"]
            ):
                # All blocking fields, including the final patient-goal answer,
                # are recorded. Non-blocking candidates (sex, reported
                # temperature) stay askable but never gate the summary. A
                # projection of known facts needs no extra LLM request; the
                # same summary gate and confirmation still apply. One
                # exception: a triggered pathway whose details were never
                # probed must not be silently summarized away — the
                # controller asks its detail question first (the pathway
                # term list is wider than the question catalog's, so this
                # fires exactly where missing_required cannot see the gap).
                reply = self._controller_summary(state, text, turn_id)
                if reply.phase == Phase.SUMMARY:
                    self.events.append(
                        Event(
                            state.session_id,
                            "agent.summary_ready",
                            {
                                "origin": "controller",
                                "reason": "all_fields_attempted_after_goal",
                            },
                            turn_id=turn_id,
                        )
                    )
                return self._deliver(state, reply, turn_id)
        state.pending_input = text
        return self._deliver(
            state,
            AgentReply(
                "我还需要核实信息才能继续。这轮未能选定合适的问题，请重试或查看已记录内容。",
                state.phase,
                kind="service_error",
            ),
            turn_id,
        )

    def _execute(
        self, state: CaseState, command: Dict[str, Any], text: str, turn_id: str
    ):
        validate_command(command)
        tool, args = command["tool"], command["arguments"]
        if tool == "change_subject":
            evidence = args["evidence"]
            if (
                not isinstance(evidence, str)
                or not 1 <= len(evidence) <= 300
                or evidence not in text
            ):
                raise ProposalError("换人需引用本轮明确对象的连续原话。")
            requested = subject_reference(evidence)
            current = subject_profile(state)
            if not requested or not state.facts:
                raise ProposalError("尚未确定当前对象时先澄清或记录，不必另建问诊。")
            if not explicit_switch(text, current) and (
                not current
                or not current["reference"]
                or compatible(current["reference"], requested)
            ):
                raise ProposalError("这可能是当前对象的称谓补充。先澄清，不把普通家族史当作换人。")
            return self._subject_change_reply(
                state,
                {
                    "category": "self"
                    if requested["attributes"][0] == "self"
                    else "proxy",
                    "label": requested["label"],
                },
                text,
                turn_id,
            )
        if tool == "repeat_self_check":
            from .observations import repeat_intent

            evidence = args["evidence"]
            if (
                state.observation_feedback
                and state.observation_feedback["task_id"] == args["task_id"]
            ):
                raise ProposalError("这次测量已处理，请先处理同轮补充信息，不要重复已完成的动作")
            if not isinstance(args["task_id"], str):
                raise ProposalError("图示任务ID必须是字符串")
            task = self._repeat_task(state, args["task_id"])
            if (
                not isinstance(evidence, str)
                or not 2 <= len(evidence) <= 300
                or evidence not in text
                or not repeat_intent(task, text)
                or not repeat_intent(task, evidence)
            ):
                raise ProposalError("重复观察必须引用本轮明确请求的连续原文")
            return self._request_repeat(state, task["id"], text, turn_id)
        if tool == "revise_self_check":
            self._request_location_revision(state, args, text, turn_id)
            return {
                "ok": True,
                "case": self._context(state, text),
                "next": "修改卡已建立；可用propose_self_check_result整理本轮明确更正，或show_self_check展示核对。原事实尚未修改。",
            }
        if tool == "request_self_check":
            task_id, evidence = args["task_id"], args["evidence"]
            task = next(
                (
                    t
                    for t in self.content.applicable_tasks(
                        str(state.get("chief_complaint", ""))
                    )
                    if t["id"] == task_id and t["result_key"] not in state.facts
                ),
                None,
            )
            if (
                task is None
                or not isinstance(evidence, str)
                or not 2 <= len(evidence) <= 300
                or evidence not in text
            ):
                raise ProposalError("请求必须针对已注册适用任务，并引用本轮明确请求的连续原文")
            if not any(
                item["task_id"] == task_id for item in state.pending_self_checks
            ):
                request = {"task_id": task_id, "evidence": evidence, "turn_id": turn_id}
                state.pending_self_checks.append(request)
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.requested",
                        dict(request),
                        turn_id=turn_id,
                    )
                )
            return {"ok": True, "case": self._context(state, text)}
        if tool == "run_capability":
            if not isinstance(args["id"], str) or not isinstance(args["input"], dict):
                raise ProposalError("能力 ID 或输入格式无效")
            return self.request_calculation(
                state, args["id"], args["input"], args["evidence"], text, turn_id
            )
        if tool == "spawn_subagent":
            # Decision-layer kind whitelist (healthcheck P1-A-D2): only
            # the two model-proposed kinds may be spawned from here.
            # Rejected before the coordinator is called and before the
            # per-round spawn budget is consumed, so the model can
            # retry in the same round with a legal kind.
            task_kind = args.get("task_kind")
            if not isinstance(task_kind, str) or task_kind not in MODEL_SPAWN_TASK_KINDS:
                return {
                    "ok": False,
                    "error": (
                        "task_kind 只能是 evidence_lookup 或 second_opinion；"
                        f"「{task_kind}」不能由模型提议（控制器专属种类如 "
                        "case_digest 不在模型可提议范围内），"
                        "请改用 evidence_lookup 或 second_opinion 重提"
                    ),
                }
            coordinator = self.subagent_coordinator
            if coordinator is None:
                return {"ok": False, "status": "unavailable"}
            if self._round_spawn_counts.get(state.session_id, 0) >= 1:
                return {"ok": False, "error": "每轮至多派生一次子智能体"}
            self._round_spawn_counts[state.session_id] = (
                self._round_spawn_counts.get(state.session_id, 0) + 1
            )
            turn = self._active_turns.get(state.session_id)
            result = coordinator.run_spawn(
                state,
                args,
                session_id=state.session_id,
                cancelled=turn.cancelled if turn is not None else None,
            )
            if self._subagent_mode_for(args.get("task_kind")) != "advisory":
                if isinstance(result, dict) and result.get("ok"):
                    # Shadow: the run executed and is recorded as events
                    # only; nothing a subagent produced enters the context.
                    return {
                        "ok": True,
                        "spawn": {
                            "status": "shadow_recorded",
                            "spawn_id": result.get("spawn_id"),
                        },
                    }
                # off / disabled / over the daily fuse / unavailable: the
                # spawn did not run; report its real status, not shadow.
                return {
                    "ok": False,
                    "status": (
                        result.get("status")
                        if isinstance(result, dict)
                        else None
                    )
                    or "unavailable",
                }
            if isinstance(result, dict):
                # Design §5.7.2: a blinded second opinion whose structured
                # tendency disagrees with the case's current risk group is
                # surfaced to the main model as a flag, never to the patient.
                tendency_groups = {
                    "self_care": "routine",
                    "routine_visit": "routine",
                    "urgent_today": "urgent",
                    "emergency": "emergency",
                }
                current = (
                    state.risk.level.value
                    if getattr(state, "risk", None) is not None
                    else None
                )
                for branch in result.get("branches") or []:
                    if branch.get("task_kind") != "second_opinion" or (
                        branch.get("status") not in {"completed", "partial"}
                    ):
                        continue
                    tendency = branch.get("disposition_tendency")
                    if (
                        tendency in tendency_groups
                        and current
                        and tendency_groups[tendency] != current
                    ):
                        result = {
                            **result,
                            "second_opinion_disagreement": True,
                            "second_opinion_tendency": tendency,
                        }
                        # The flag above is per-turn model feedback; this
                        # event is the durable record the metrics layer
                        # aggregates disagreement rates from. Same firing
                        # condition, no change to what is fed back.
                        self.events.append(
                            Event(
                                state.session_id,
                                "subagent.disagreement",
                                {
                                    "task_kind": "second_opinion",
                                    "tendency": tendency,
                                    "risk_group": current,
                                },
                                turn_id=turn_id,
                            )
                        )
                        break
            return {"ok": True, "spawn": result}
        if tool == "propose_self_check_result":
            return self._propose_task_result(state, args, text, turn_id)
        if tool == "record_facts":
            rejected = []
            # J-01: a refusal proposal (unknown + a refusal quote from
            # this turn) bypasses the unknown-evidence validator, which
            # only accepts "不知道"-style quotes and would otherwise
            # deadlock the summary gate. The refusal still lands as a
            # recorded UNKNOWN fact with the verbatim quote — no value
            # is invented, and the field leaves missing_required exactly
            # like any other recorded field. Age and sex are treated
            # identically: the gate is satisfied by recording, not by
            # demoting either field to non-blocking.
            declined_keys = set()
            declined_proposals = []
            kept_facts = None
            raw_facts = args.get("facts") if isinstance(args, dict) else None
            if isinstance(raw_facts, list):
                kept_facts = []
                for item in raw_facts:
                    if isinstance(item, dict) and self._is_declined_proposal(
                        state, text, item
                    ):
                        declined_keys.add(item["key"])
                        declined_proposals.append(
                            {
                                "key": item["key"],
                                "value": None,
                                "assertion": Assertion.UNKNOWN,
                                "evidence": item.get("evidence"),
                                "correction": bool(item.get("correction")),
                            }
                        )
                    else:
                        kept_facts.append(item)
            if kept_facts is not None and not kept_facts and declined_proposals:
                proposals = []
            else:
                try:
                    proposals = validate_proposals(
                        self.content,
                        state,
                        text,
                        kept_facts if kept_facts is not None else args.get("facts"),
                    )
                except ProposalError as exc:
                    # Partial recording (wave-2 P2): items that passed validation
                    # land now, and only the failures go back to the model with
                    # per-item reasons and repair guidance — the model re-proposes
                    # just the fixed items instead of re-sending (and risking)
                    # the whole batch. All-or-nothing callers are unaffected:
                    # correct_fact pre-validates its single item before calling,
                    # and the controller's direct emergency-screen recording
                    # bypasses this tool entirely. A batch where nothing passed
                    # keeps the old atomic behaviour and raises unchanged.
                    accepted = getattr(exc, "accepted", None)
                    if not exc.verdicts or not accepted:
                        raise
                    proposals = accepted
                    rejected = [v for v in exc.verdicts if not v["ok"]]
            proposals = list(proposals) + declined_proposals
            # J-03 trajectory evidence guard (host side): steady,
            # not-improving wording must not book as worsening. The item
            # is rejected with repair guidance (record stable, or clarify
            # whether it truly worsened) instead of landing; the urgent
            # single-clue gate is the backstop if a worsening value ever
            # lands through another path.
            guarded_proposals = []
            for proposal in proposals:
                if (
                    proposal["key"] == "trajectory"
                    and proposal["value"] == "worsening"
                    and is_steady_not_improving_evidence(
                        proposal.get("evidence") or ""
                    )
                ):
                    rejected.append(
                        {
                            "key": "trajectory",
                            "ok": False,
                            "reason": (
                                "证据表述为平稳未好转，不得记为加重"
                            ),
                            "guidance": (
                                "请按变化不大/平稳落账，或先澄清是否确实加重后再提议。"
                            ),
                        }
                    )
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.trajectory_guard",
                            {
                                "key": "trajectory",
                                "evidence": proposal.get("evidence"),
                            },
                            turn_id=turn_id,
                        )
                    )
                elif (
                    proposal["key"] == "trajectory"
                    and proposal["value"] == "stable"
                    and any(
                        phrase in (proposal.get("evidence") or "")
                        for phrase in FLUCTUATION_PHRASES
                    )
                ):
                    # Batch C BURDEN (D-02): fluctuation wording
                    # ("此起彼伏，旧的消了新的又起") is waxing and
                    # waning, not steadiness. A free-text answer that
                    # matches no single option must not be forced onto
                    # the nearest one: reject with guidance to record
                    # unknown or clarify with an open question instead.
                    rejected.append(
                        {
                            "key": "trajectory",
                            "ok": False,
                            "reason": (
                                "证据表述为波动交替，不得记为变化不大"
                            ),
                            "guidance": (
                                "请记为说不清（unknown），或用开放式追问澄清总体"
                                "趋势后再提议，不得把波动描述强行映射为稳定。"
                            ),
                        }
                    )
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.trajectory_fluctuation_guard",
                            {
                                "key": "trajectory",
                                "evidence": proposal.get("evidence"),
                            },
                            turn_id=turn_id,
                        )
                    )
                else:
                    guarded_proposals.append(proposal)
            proposals = guarded_proposals
            # Batch C BURDEN (O-06): an answer given while the screening
            # question is active IS the screening answer. When the model
            # files it elsewhere (associated_symptoms) instead, the host
            # files the denial as the screening fact itself, so the
            # screen is not left unanswered to be re-asked verbatim.
            # This mirrors the deterministic engine, which interprets
            # any reply against the active question; only denial replies
            # are backstopped here (affirmations are triage-owned).
            active_screen = next(
                (
                    q
                    for q in self.content.questions
                    if q.id == state.active_question
                ),
                None,
            )
            if (
                active_screen is not None
                and active_screen.fact_key == "emergency_screen"
                and not any(
                    item["key"] == "emergency_screen" for item in proposals
                )
                and (
                    "都没有" in text
                    or text.strip().startswith(("没有", "无", "都没", "否认"))
                )
            ):
                proposals.append(
                    {
                        "key": "emergency_screen",
                        "value": None,
                        "assertion": Assertion.ABSENT,
                        "evidence": text,
                        "correction": False,
                    }
                )
            if not proposals and rejected:
                return {
                    "ok": False,
                    "partial": True,
                    "recorded": [],
                    "rejected": [
                        {
                            "key": verdict["key"],
                            "reason": verdict["reason"],
                            "guidance": verdict["guidance"],
                        }
                        for verdict in rejected
                    ],
                    "next": (
                        "已通过项已写入，不要重复提议；只需修正 rejected 中的"
                        "字段后重新提议 record_facts（只含修正项）。"
                    ),
                    "case": self._context(state, text),
                }
            self._guard_calculation_followup_updates(state, proposals)
            # No mutation happens until the accepted set has validated per
            # item and been dependency-checked as a set (_prepare_fact_updates
            # still raises on a subject boundary before anything is recorded).
            incoming_subject = next(
                (p["value"] for p in proposals if p["key"] == "consult_for"),
                state.get("consult_for"),
            )
            self._prepare_fact_updates(state, proposals, turn_id)
            active_before = next(
                (q for q in self.content.questions if q.id == state.active_question),
                None,
            )
            for proposal in proposals:
                source = (
                    Source.CAREGIVER
                    if incoming_subject in {"child", "other_adult"}
                    else Source.PATIENT
                )
                # Batch C BURDEN (O-12), same merge as the deterministic
                # engine's _record_fact: a non-correction writeback must
                # not drop earlier denial/unknown segments. (In practice
                # validated non-correction proposals cannot target an
                # already-recorded key, so this is a backstop; explicit
                # corrections replace, as in correct_fact.)
                value = proposal["value"]
                if not proposal.get("correction"):
                    previous = state.facts.get(proposal["key"])
                    if previous is not None and isinstance(value, str):
                        previous_text = (
                            previous.value
                            if isinstance(previous.value, str)
                            else previous.evidence
                        )
                        value = merge_preserving_denials(previous_text, value)
                fact = Fact(
                    key=proposal["key"],
                    value=value,
                    assertion=proposal["assertion"],
                    source=source,
                    turn_id=turn_id,
                    evidence=proposal["evidence"],
                )
                state.record(fact)
                self.events.append(
                    Event(
                        state.session_id,
                        "fact.recorded",
                        fact.to_dict(),
                        turn_id=turn_id,
                    )
                )
                self._track_incidental(state, proposal, active_before)
                if state.closed_reason != "emergency_route":
                    state.closed_reason = None
            for key in declined_keys:
                if key in state.facts:
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.declined",
                            {"fact_key": key},
                            turn_id=turn_id,
                        )
                    )
            self._sync_task_requests(state, text, turn_id)
            active = next(
                (q for q in self.content.questions if q.id == state.active_question),
                None,
            )
            if active and any(item["key"] == active.fact_key for item in proposals):
                state.active_question = None
                state.active_question_is_clarification = False
            state.risk = self.triage.assess(state, text)
            self.events.append(
                Event(
                    state.session_id,
                    "triage.assessed",
                    state.risk.to_dict(),
                    turn_id=turn_id,
                )
            )
            if rejected:
                return {
                    "ok": False,
                    "partial": True,
                    "recorded": [p["key"] for p in proposals],
                    "rejected": [
                        {
                            "key": verdict["key"],
                            "reason": verdict["reason"],
                            "guidance": verdict["guidance"],
                        }
                        for verdict in rejected
                    ],
                    "next": (
                        "已通过项已写入，不要重复提议；只需修正 rejected 中的"
                        "字段后重新提议 record_facts（只含修正项）。"
                    ),
                    "case": self._context(state, text),
                }
            return {"ok": True, "case": self._context(state, text)}
        if tool in {"inspect_case", "list_questions"}:
            return {"ok": True, "case": self._context(state, text)}
        if tool == "ask_question":
            if (
                state.pending_self_checks
                and not state.self_check_conversation
                and not self._self_check_block_reason(state)
            ):
                raise ProposalError("患者请求的图示任务已满足条件，应先处理待办")
            question = self.content.question(args["question_id"])
            if question.fact_key in state.facts:
                raise ProposalError("已记录这个字段；需要核实时使用 clarify")
            if not self.content.question_applies_in_state(question, state):
                raise ProposalError("问题不适用于当前主诉")
            if not self._fact_gates_pass(state, question):
                raise ProposalError("该问题已被当前已知事实排除（如性别或年龄范围不适用）")
            # Hypothesis-order check (P0): the model may phrase the
            # question, but what to ask next is the ranker's call. The
            # existing 6-step self-correction loop re-proposes on this
            # error. Exempt: clarification rounds (the clarify tool has
            # its own branch below; here a clarification round is
            # flagged by active_question_is_clarification), follow-ups
            # of the active question, and pending emergency
            # clarifications. The pick must come from the same validated
            # candidate set, otherwise the check passes through rather
            # than risk a proposal loop the model cannot exit.
            ranker = getattr(self, "ranker", None)
            if ranker is not None and not state.active_question_is_clarification:
                chief = str(state.get("chief_complaint", "") or "")
                candidates = self._candidate_questions(state, chief)
                candidate_ids = {item.id for item in candidates}
                pick = ranker.choose(state, candidates)
                if (
                    pick is not None
                    and pick in candidate_ids
                    and args["question_id"] in candidate_ids
                    and args["question_id"] != state.active_question
                    and self._emergency_clarification_pending(state) is None
                    and args["question_id"] != pick
                ):
                    raise ProposalError(
                        f"按假设排序应先问 {pick}，当前提议 {args['question_id']} 暂缓"
                    )
            return self._ask(state, question, "根据本轮证据与尚未处理的缺口选择下一问")
        if tool == "ask_patient":
            if state.self_check_conversation:
                raise ProposalError("图示交互进行中；先用固定问题澄清或接续当前图示")
            if (
                state.pending_self_checks
                and not state.self_check_conversation
                and not self._self_check_block_reason(state)
            ):
                raise ProposalError("患者请求的图示任务已满足条件，应先处理待办")
            spec = validate_adaptive_question(
                args,
                allowed_topics={question.fact_key for question in self.content.questions},
            )
            # B route: a model-written question must carry its own bridging
            # sentence plus a "why ask" half-sentence. Enforced only when an
            # anchorable fact exists (an opening round has nothing to anchor
            # on, and forcing it would loop the self-correction); the host
            # A-route bridge is the fallback and never stacks on a question
            # that already self-bridges (SELF_BRIDGED_OPENERS).
            if has_anchorable_fact(state.facts):
                has_bridge, has_why = model_question_bridging_status(spec["question"])
                if not (has_bridge and has_why):
                    raise ProposalError(MODEL_BRIDGING_REPAIR_HINT)
            question = next(
                item
                for item in self.content.questions
                if item.fact_key == spec["topic"]
            )
            # Topic legality is the registered field set, not the dynamic
            # candidate list: a colloquial chief complaint may never have
            # candidated the field that matters most (live-eval cases 3/6),
            # and an already-recorded field may still need a targeted
            # follow-up — its answer then lands via correction=true, which
            # the context flags (active_question_field_already_recorded).
            # Fact-dependent exclusions still apply: a field excluded by
            # known facts (e.g. pregnancy once sex is male) is not askable.
            if not self._fact_gates_pass(state, question):
                raise ProposalError(
                    "该字段已被当前已知事实排除（如性别或年龄范围不适用），请改问其他主题"
                )
            # Batch C BURDEN (D-01/P-02): a topic whose fixed question
            # was already asked AND whose fact is already recorded
            # (including unknown) must not be re-asked as a fresh model
            # question — that is the verbatim-repeat loop ("刚才说过了").
            # Targeted follow-ups on merely recorded, never-asked topics
            # stay allowed (pinned by
            # test_recorded_topic_allows_targeted_follow_up), and a
            # genuine re-ask stays possible through clarify, which is
            # capped at two per topic.
            if (
                question.id in state.asked_questions
                and spec["topic"] in state.facts
            ):
                raise ProposalError(
                    "该主题已经问过且已有记录，不得原样重问；"
                    "如需新信息请用 clarify，并先说明还不够区分什么，"
                    "追问所得的否认项须落账进同一事实键。"
                )
            state.phase = question.phase
            state.active_question = question.id
            state.active_question_is_clarification = False
            state.active_task = None
            if question.id not in state.asked_questions:
                state.asked_questions.append(question.id)
            # J-01: a model-defined decline option must reach the patient
            # view as a quick reply, not only inside adaptive_question
            # (the live-eval patient view reads quick_replies). Only
            # decline-like, single-mode, detail-free options are mirrored:
            # mirroring every option would duplicate the question pane's
            # adaptive choices in the composer; the decline button is the
            # one that must never be unreachable.
            decline_replies = [
                QuickReply(item["label"], f"adaptive_single:{item['id']}")
                for item in spec["options"]
                if spec["selection_mode"] == "single"
                and not item.get("requires_detail")
                and any(
                    phrase in item["label"]
                    for phrase in ("不想说", "不说", "拒绝", "不方便")
                )
            ]
            # Batch C BURDEN render layer (Y-10/O-06/K-04): the delivered
            # text, not just the decision, is what the patient reads.
            # Detail composites drop already-denied items; a screening
            # question gains the registered item list; and a
            # multi-option question whose text points at "以下这些" but
            # lists no option must list its options in the text, so the
            # option set is visible somewhere (quick_replies or text)
            # instead of dangling as K-04's empty quick_replies did.
            display_question = spec["question"]
            if spec["topic"] in DETAIL_FACT_KEYS:
                display_question = _strip_denied_items(
                    display_question, state.facts
                )
            if spec["topic"] == "emergency_screen":
                display_question = self._ensure_screening_list(display_question)
            labels = [
                item["label"] for item in spec["options"] if item.get("label")
            ]
            if (
                labels
                and any(word in display_question for word in ("以下", "下列"))
                and not any(label in display_question for label in labels)
            ):
                display_question = (
                    display_question + "（选项：" + "、".join(labels) + "）"
                )
            return AgentReply(
                display_question,
                question.phase,
                kind="question",
                question_id=question.id,
                adaptive_question=spec,
                quick_replies=decline_replies,
                developer_trace={
                    "reason": "模型根据当前病例生成可变数量的受控患者选项",
                    "content_version": self.content.versions["questions"],
                },
            )
        if tool == "clarify":
            topic, message = args.get("topic"), args.get("message")
            fields = {q.fact_key for q in self.content.questions}
            frame = state.self_check_conversation
            location_topic = bool(
                frame
                and frame["task"]["id"] == "body_location"
                and topic == frame["task"]["result_key"]
            )
            if (
                (topic not in fields and not location_topic)
                or not isinstance(message, str)
                or not message.strip()
                or not 2 <= len(message) <= 160
            ):
                raise ProposalError("澄清需要注册主题和 2 到 160 字的简短问题")
            if state.followup_counts.get(topic, 0) >= 2:
                raise ProposalError("该主题已澄清两次；请保留未知或改问其他主题")
            prohibited = ("毫克", "mg", "服用", "确诊", "可以排除", "用力按", "屏住呼吸")
            if any(p in message for p in prohibited):
                raise ProposalError("澄清不能生成处方、排除性结论或自查动作")
            # Batch C BURDEN (O-06): a completed screen is not re-asked
            # verbatim. A supplement for uncovered items stays possible,
            # but it must say so ("补问"/"新增") and ask only the new
            # items — the render layer below still attaches the list.
            if (
                topic == "emergency_screen"
                and topic in state.facts
                and not any(word in message for word in ("补问", "新增"))
            ):
                raise ProposalError(
                    "急症筛查已有记录，不得原样重问；"
                    "补问未覆盖项目时请说明是补问并只问新增项目。"
                )
            # B route, same discipline as ask_patient. The body-location
            # task path is exempt: its messages are short task prompts
            # (<=80 chars) where a bridging sentence would break the format.
            if not location_topic and has_anchorable_fact(state.facts):
                has_bridge, has_why = model_question_bridging_status(message)
                if not (has_bridge and has_why):
                    raise ProposalError(MODEL_BRIDGING_REPAIR_HINT)
            if location_topic:
                if len(message) > 80:
                    raise ProposalError("定位补问最多80字，保持一个简短问题。")
                reply = self._clarify_location_task(
                    state, message, args.get("answer_fields")
                )
                state.followup_counts[topic] = state.followup_counts.get(topic, 0) + 1
                return reply
            if "answer_fields" in args:
                raise ProposalError("answer_fields只适用于当前部位定位任务。")
            state.followup_counts[topic] = state.followup_counts.get(topic, 0) + 1
            state.active_question_is_clarification = True
            state.active_question = next(
                q.id for q in self.content.questions if q.fact_key == topic
            )
            state.phase = Phase.HISTORY
            display_message = message
            if topic in DETAIL_FACT_KEYS:
                display_message = _strip_denied_items(display_message, state.facts)
            if topic == "emergency_screen":
                display_message = self._ensure_screening_list(display_message)
            return AgentReply(
                display_message, state.phase, question_id=state.active_question
            )
        if tool == "show_self_check":
            if state.self_check_conversation and state.self_check_conversation["task"][
                "id"
            ] == args.get("task_id"):
                frame_id = state.self_check_conversation["task"]["id"]
                if (
                    self._self_check_suspend_count(
                        state,
                        frame_id,
                        state.self_check_conversation["task"].get(
                            "interaction_id"
                        ),
                    )
                    >= SELF_CHECK_SUSPEND_CAP
                ):
                    self._clear_task_conversation(state, "suspend_cap_reached")
                    state.pending_self_checks = [
                        item
                        for item in state.pending_self_checks
                        if item["task_id"] != frame_id
                    ]
                    self.events.append(
                        Event(
                            state.session_id,
                            "self_check.suspend_cap_reached",
                            {"task_id": frame_id},
                            turn_id=turn_id,
                        )
                    )
                    return self._plan_next(state)
                return self._resume_task_conversation(state)
            if state.self_check_conversation:
                raise ProposalError("先接续原图示任务或澄清病史，另一图示请求可以登记为待办。")
            task = next(
                (
                    t
                    for t in self.content.applicable_tasks(
                        str(state.get("chief_complaint", ""))
                    )
                    if args.get("task_id") == t["id"]
                ),
                None,
            )
            if task is None or task["result_key"] in state.facts:
                raise ProposalError("任务未注册、不适用或已经尝试")
            blocked = self._self_check_block_reason(state)
            if blocked:
                raise ProposalError(blocked)
            return self._offer_task(
                state, str(state.get("chief_complaint", "")), task["id"]
            )
        if tool == "review_summary":
            if state.self_check_conversation:
                raise ProposalError("原图示任务尚未处理。可show_self_check接续，由患者完成或跳过；也可先澄清病史。")
            abstain_reason = args.get("abstain_reason")
            if abstain_reason is not None and (
                not isinstance(abstain_reason, str)
                or not abstain_reason.strip()
                or len(abstain_reason.strip()) > 200
            ):
                raise ProposalError("abstain_reason 须为 1 到 200 字的字符串，说明主诉通路为何不适用探明")
            context = self._context(state, text)
            unexplored = [
                entry
                for entry in context.get("pathway_exploration", [])
                if not entry["explored"]
            ]
            # The gate guards the first close decision only: re-summarizing
            # a case that already reached the summary/closed stage (e.g. a
            # fact correction replay) is not a silent close.
            already_summarized = state.phase in {
                Phase.SUMMARY,
                Phase.CLOSED,
            } or bool(state.conclusion)
            if unexplored and not already_summarized and not abstain_reason:
                raise self._unexplored_pathway_error(unexplored)
            missing = context["missing_required"]
            if unexplored and (already_summarized or abstain_reason):
                # The abstention (or the earlier close, when re-summarizing)
                # also excuses the unexplored pathways' own detail fields
                # from the required-fields gate — the stated reason is
                # precisely "this pathway's details will not be probed".
                # Every other unattempted field still blocks as before.
                excused = {
                    fact_key
                    for entry in unexplored
                    for fact_key in entry["detail_fields"]
                }
                missing = [key for key in missing if key not in excused]
            if missing:
                # J-01: "尚未尝试" must not mislead the model into
                # re-asking a field that was asked but never landed
                # (e.g. a refused field whose recording was rejected).
                # The prefix stays verbatim for existing assertions.
                labelled = [
                    (
                        f"{key}（已提问但未落账）"
                        if self._questions_by_fact[key].id
                        in state.asked_questions
                        else key
                    )
                    for key in missing
                ]
                raise ProposalError("必要字段尚未尝试：" + ",".join(labelled))
            # Batch C BURDEN (G-07): completeness check before the first
            # summary draft. A recorded detail fact can still lack the
            # chief complaint's key items (constipation without straining
            # effort or weight change); those are asked first instead of
            # reaching the draft for the patient to correct. Abstention
            # does not excuse this gate: it excuses unexplored pathways,
            # not missing sub-items of a recorded detail fact.
            gaps = self._completeness_gaps(state)
            if gaps:
                raise ProposalError(
                    "摘要确认前完整性检查未通过：主诉关键项尚未记录："
                    + "、".join(gaps)
                    + "。请先追问这些项再出摘要首版，不得依赖患者主动更正。"
                )
            if unexplored and not already_summarized and abstain_reason:
                self.events.append(
                    Event(
                        state.session_id,
                        "review.abstained",
                        {
                            "pathways": [entry["pathway"] for entry in unexplored],
                            "detail_fields": sorted(
                                {
                                    fact_key
                                    for entry in unexplored
                                    for fact_key in entry["detail_fields"]
                                }
                            ),
                            "reason": abstain_reason.strip(),
                        },
                        turn_id=turn_id,
                    )
                )
            reply = super()._finish(state)
            state.phase = Phase.SUMMARY
            state.closed_reason = None
            state.active_question = None
            reply.phase = Phase.SUMMARY
            reply.message = "我把本次情况整理在下面。请核对是否准确；需要改动的地方可以直接告诉我。"
            if state.pending_self_checks:
                reply.message += "你提出的图示请求尚未执行，未完成的筛查不能当作阴性。"
            reply.quick_replies = [
                QuickReply("确认摘要并结束本次问诊", "confirm_summary"),
                QuickReply("我需要更正", "correct_summary"),
            ]
            return reply
        raise ProposalError("工具未注册，不能执行：" + str(tool))

    def _deliver(self, state: CaseState, reply: AgentReply, turn_id: str) -> AgentReply:
        # The resume affordance is not offered past the suspend cap:
        # without the button, the stale-action check also rejects a
        # fabricated resume choice for the capped frame.
        if (
            state.self_check_conversation
            and reply.kind == "question"
            and self._self_check_suspend_count(
                state,
                state.self_check_conversation["task"]["id"],
                state.self_check_conversation["task"].get("interaction_id"),
            )
            < SELF_CHECK_SUSPEND_CAP
        ):
            frame = state.self_check_conversation
            choice = f"resume_self_check:{frame['id']}"
            if not any(item.value == choice for item in reply.quick_replies):
                reply.quick_replies.append(
                    QuickReply("继续" + frame["task"]["title"], choice)
                )
        if reply.kind == "service_error":
            reply.quick_replies = [QuickReply("重试这一轮", "retry_turn")]
        elif reply.kind != "subject_change":
            state.pending_input = None
        self._record_reply(state, reply, turn_id, "受约束工具执行后的患者可见动作")
        return reply

    def correct_fact(
        self,
        session_id: str,
        fact_key: str,
        new_text: str,
        expected_fact_id: Optional[str] = None,
    ) -> AgentReply:
        # Explicit UI correction remains available; it does not use ungrounded model values.
        state = self.state(session_id)
        with self._session_locks[session_id]:
            self._interrupt_or_reject(state, new_text)
            if state.calculation_feedback:
                state.calculation_feedback["processing"] = False
            urgent_reply = self._correction_emergency(state, new_text)
            if urgent_reply:
                return urgent_reply
            if state.phase == Phase.CLOSED and state.closed_reason != "emergency_route":
                # Same guard as the base engine (ConsultationEngine
                # .correct_fact): closed cases are read-only history.
                # Without it this override silently rewound CLOSED to
                # HISTORY below, forking the case from its conclusion
                # sheet and orphaning its follow-up plan on the next
                # persisted restart. emergency_route stays exempt: a
                # correction there re-runs triage and the route itself
                # can never be retracted by an edit.
                raise ValueError("会谈已结束，记录不能再更正")
            self._check_fact_version(state, fact_key, expected_fact_id)
            previous_question, previous_task, previous_phase = (
                state.active_question,
                state.active_task,
                state.phase,
            )
            was_clarification = state.active_question_is_clarification
            question = next(
                (q for q in self.content.questions if q.fact_key == fact_key), None
            )
            if question is None or fact_key == "emergency_screen":
                raise ValueError("字段不可直接编辑")
            result = self.interpreter.interpret(question, new_text)
            proposals = validate_proposals(
                self.content,
                state,
                new_text,
                [
                    {
                        "key": fact_key,
                        "value": result.value
                        if result.assertion == Assertion.PRESENT
                        else None,
                        "assertion": result.assertion.value,
                        "evidence": new_text,
                        "correction": True,
                    }
                ],
            )
            turn_id = str(uuid4())
            self._execute(
                state,
                {
                    "tool": "record_facts",
                    "arguments": {
                        "facts": [
                            {**p, "assertion": p["assertion"].value} for p in proposals
                        ]
                    },
                },
                new_text,
                turn_id,
            )
            self.events.append(
                Event(
                    session_id,
                    "fact.correction_requested",
                    {"fact_key": fact_key, "new_text": new_text},
                    turn_id=turn_id,
                )
            )
            state.transcript.append({"role": "user", "content": new_text})
            state.active_task = None
            state.active_question = None
            state.phase = Phase.HISTORY
            if state.risk.level == RiskLevel.EMERGENCY:
                action = self._emergency_clarification_action(state, new_text, ())
                if action == "clarify":
                    return self._deliver(
                        state,
                        self._emergency_clarification_reply(state, new_text, turn_id),
                        turn_id,
                    )
                if action == "confirmed":
                    return self._deliver(
                        state,
                        self._emergency_reply(
                            state, state.risk.triggers, include_clarification=True
                        ),
                        turn_id,
                    )
                reply = self._emergency_reply(state, state.risk.triggers)
                return self._deliver(state, reply, turn_id)
            if state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    return self._deliver(state, gated, turn_id)
            pending_reply = self._offer_pending_self_check(state)
            if pending_reply:
                return self._deliver(state, pending_reply, turn_id)
            if previous_task:
                resumed = self._offer_task(
                    state, str(state.get("chief_complaint", "")), previous_task
                )
                if resumed:
                    return self._deliver(state, resumed, turn_id)
            if (
                previous_phase in {Phase.SUMMARY, Phase.CLOSED}
                and not self._context(state, new_text)["missing_required"]
            ):
                reply = self._execute(
                    state,
                    {"tool": "review_summary", "arguments": {}},
                    new_text,
                    turn_id,
                )
                return self._deliver(state, reply, turn_id)
            if previous_question:
                previous = next(
                    (q for q in self.content.questions if q.id == previous_question),
                    None,
                )
                if (
                    previous
                    and (
                        previous.fact_key not in state.facts
                        or (was_clarification and previous.fact_key != fact_key)
                    )
                    and self.content.question_applies_in_state(previous, state)
                ):
                    reply = self._ask(state, previous, "更正已由控制器写入，继续此前未回答的问题")
                    state.active_question_is_clarification = was_clarification
                    return self._deliver(state, reply, turn_id)
            return self._deliver(state, self._plan_next(state), turn_id)
