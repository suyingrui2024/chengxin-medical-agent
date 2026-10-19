"""Bounded medical ReAct controller shared by the DSH and compatible transports."""
from __future__ import annotations

import json
import hashlib
from copy import deepcopy
from importlib import resources
from typing import Any, Dict, Optional
from uuid import uuid4

from .agent_tools import (
    ProposalError,
    TOOL_DEFINITIONS,
    validate_proposals,
    validate_command,
)
from .backends import DecisionBackend
from .consultation import (
    ConsultationEngine,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
    ConsultationBusyError,
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


class AdaptiveConsultationEngine(ConsultationEngine):
    """LLM proposes actions; evidence/type/eligibility gates own every effect."""

    def __init__(self, *args, backend: DecisionBackend, **kwargs):
        super().__init__(*args, **kwargs)
        self.backend = backend
        self._active_turns = {}
        self._decision_waiter = DecisionWaiter()
        self.prompt = (
            resources.files("medagent.content")
            .joinpath("agent_prompt.txt")
            .read_text(encoding="utf-8")
        )
        # Immutable per-engine catalog precedes ALL case-specific data. This is
        # provider KV-prefix reuse, not a cache of patient answers or decisions.
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
            "content_versions": self.content.versions,
        }
        self.system_prompt = (
            self.prompt
            + "\n\n以下是本会谈固定的工具与内容目录（JSON）。动态输入中的候选范围优先。\n"
            + self._json(self.static_context)
        )
        self.prefix_sha256 = hashlib.sha256(self.system_prompt.encode()).hexdigest()

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

    def create_session(self):
        state = self._create_state()
        state.active_question = "chief_complaint"
        state.asked_questions = ["chief_complaint"]
        reply = self._ask(
            state, self.content.question("chief_complaint"), "先听取主诉，自然叙述后再补关键缺口"
        )
        reply.message = "你好，这次是你自己还是替家人咨询？请先说说最困扰的问题，以及大约从什么时候开始。"
        state.transcript.append({"role": "assistant", "content": reply.message})
        self._record_reply(state, reply, "session_start", "开放式开场")
        self.events.append(
            Event(
                state.session_id,
                "agent.started",
                {
                    "backend": self.backend.name,
                    "reply": reply.to_dict(),
                    "policy_version": "medical-tools-v0.11.16",
                    "prompt_sha256": self.prefix_sha256,
                    "prompt_layout": "static-catalog-prefix/v1",
                },
            )
        )
        return state, reply

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
            try:
                return self._submit(state, user_input)
            finally:
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
                        {"structured_input": prepared_location["structured_input"]}
                        if prepared_location
                        else {}
                    ),
                },
                turn_id=turn_id,
            )
        )
        state.transcript.append({"role": "user", "content": text})

        # Emergency evaluation precedes model invocation, including closed contexts.
        risk = self.triage.assess(state, text)
        if state.closed_reason == "emergency_route":
            risk = state.risk
        state.risk = risk
        if risk.level == RiskLevel.EMERGENCY:
            return self._deliver(
                state, self._emergency_reply(state, risk.triggers), turn_id
            )
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
            return self._deliver(state, self._urgent(state), turn_id)
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
                return self._deliver(
                    state, self._emergency_reply(state, state.risk.triggers), turn_id
                )
            # Only canonical option-only answers take the zero-model fast path.
            # Mixed free text must still reach the planner for fact extraction.
            if text.strip(" 。！!，,") in {"都没有", "这些危险情况都没有", "没有上述情况", "没有危险信号"}:
                pending_reply = self._offer_pending_self_check(state)
                if pending_reply:
                    return self._deliver(state, pending_reply, turn_id)
        if (
            state.phase == Phase.SUMMARY
            and user_input.choice_value == "confirm_summary"
        ):
            state.phase = Phase.CLOSED
            state.closed_reason = "confirmed_by_user"
            state.active_question = None
            return self._deliver(
                state,
                AgentReply(
                    "你已确认本次摘要，会谈现已结束。可以把它提供给接诊医生；情况有变化时请重新评估。",
                    Phase.CLOSED,
                    kind="closed",
                    summary=self._summary_payload(
                        state, review_status="confirmed_by_user"
                    ),
                ),
                turn_id,
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
                return self._deliver(state, self._urgent(state), turn_id)
        state.pending_input = text
        return self._run(state, text, turn_id)

    def _context(self, state: CaseState, text: str) -> Dict[str, Any]:
        chief = str(state.get("chief_complaint", ""))
        candidates = [
            q
            for q in self.content.questions
            if q.fact_key not in state.facts and self.content.question_applies(q, chief)
        ]
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
            "missing_required": [
                q.fact_key for q in candidates if q.id not in {"patient_goal"}
            ],
            "questions": [{"id": q.id, "field": q.fact_key} for q in candidates],
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
        }

    def _messages(self, state, text):
        return [
            {"role": "system", "content": self.system_prompt},
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
        messages = self._messages(state, text)
        writes = 0
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
                if command["tool"] == "record_facts":
                    if writes >= 2:
                        raise ProposalError("本轮写入次数已达上限，请提问或核对摘要")
                answering_goal = state.active_question == "patient_goal"
                result = self._execute(state, command, text, turn_id)
                if command["tool"] == "record_facts":
                    writes += 1
                    goal_answered = answering_goal and any(
                        item["key"] == "patient_goal"
                        for item in command["arguments"]["facts"]
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
                return self._deliver(
                    state, self._emergency_reply(state, state.risk.triggers), turn_id
                )
            if state.risk.level == RiskLevel.URGENT:
                return self._deliver(state, self._urgent(state), turn_id)
            if (
                goal_answered
                and not state.pending_self_checks
                and not self._context(state, text)["questions"]
            ):
                # All applicable fields, including the final patient-goal answer,
                # are recorded. A projection of known facts needs no extra LLM
                # request; the same summary gate and confirmation still apply.
                reply = self._execute(
                    state, {"tool": "review_summary", "arguments": {}}, text, turn_id
                )
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
        if tool == "propose_self_check_result":
            return self._propose_task_result(state, args, text, turn_id)
        if tool == "record_facts":
            proposals = validate_proposals(self.content, state, text, args.get("facts"))
            self._guard_calculation_followup_updates(state, proposals)
            # No mutation happens until the entire batch has validated.
            incoming_subject = next(
                (p["value"] for p in proposals if p["key"] == "consult_for"),
                state.get("consult_for"),
            )
            self._prepare_fact_updates(state, proposals, turn_id)
            for proposal in proposals:
                source = (
                    Source.CAREGIVER
                    if incoming_subject in {"child", "other_adult"}
                    else Source.PATIENT
                )
                fact = Fact(
                    key=proposal["key"],
                    value=proposal["value"],
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
                if state.closed_reason != "emergency_route":
                    state.closed_reason = None
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
            if not self.content.question_applies(
                question, str(state.get("chief_complaint", ""))
            ):
                raise ProposalError("问题不适用于当前主诉")
            return self._ask(state, question, "根据本轮证据与尚未处理的缺口选择下一问")
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
            return AgentReply(message, state.phase, question_id=state.active_question)
        if tool == "show_self_check":
            if state.self_check_conversation and state.self_check_conversation["task"][
                "id"
            ] == args.get("task_id"):
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
            context = self._context(state, text)
            if context["missing_required"]:
                raise ProposalError("必要字段尚未尝试：" + ",".join(context["missing_required"]))
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
        if state.self_check_conversation and reply.kind == "question":
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
        state.transcript.append({"role": "assistant", "content": reply.message})
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
                reply = self._emergency_reply(state, state.risk.triggers)
                return self._deliver(state, reply, turn_id)
            if state.risk.level == RiskLevel.URGENT:
                return self._deliver(state, self._urgent(state), turn_id)
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
                    and self.content.question_applies(
                        previous, str(state.get("chief_complaint", ""))
                    )
                ):
                    reply = self._ask(state, previous, "更正已由控制器写入，继续此前未回答的问题")
                    state.active_question_is_clarification = was_clarification
                    return self._deliver(state, reply, turn_id)
            return self._deliver(state, self._plan_next(state), turn_id)
