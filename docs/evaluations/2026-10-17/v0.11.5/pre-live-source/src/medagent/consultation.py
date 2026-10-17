from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from threading import Lock, RLock, Condition
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    ObservationQuality,
    Phase,
    QuickReply,
    RiskLevel,
    RiskAssessment,
    Source,
)
from .events import Event, EventStore
from .interpreter import Interpretation, Interpreter, ObservationInputError
from .knowledge import ContentRepository, Question
from .self_checks import SelfCheckService
from .triage import TriageService
from .reasoner import QuestionRanker
from . import __version__
from .presentation import calculation_presentation
from .task_intents import task_intent
from .patient_calculations import request_draft, confirmed_parameters
from .observations import ObservationMixin


FACT_LABELS = {
    "consult_for": "咨询对象",
    "age_years": "年龄",
    "chief_complaint": "主要问题",
    "emergency_screen": "急症筛查",
    "onset": "开始与起病方式",
    "trajectory": "变化趋势",
    "severity": "影响程度",
    "associated_symptoms": "伴随不适",
    "resp_breathing": "呼吸情况",
    "resp_cough_details": "咳嗽细节",
    "resp_intake": "饮水进食与排尿",
    "temperature_c": "体温",
    "symptom_location": "不适部位（主观定位）",
    "medications_taken": "已做处理/用药",
    "allergies": "过敏信息",
    "relevant_history": "相关病史",
    "patient_goal": "期望与担心",
}

VALUE_LABELS = {
    "self": "本人",
    "child": "孩子",
    "other_adult": "其他家人",
    "worsening": "加重",
    "improving": "减轻",
    "stable": "变化不大",
}


@dataclass(frozen=True)
class UserInput:
    text: str
    choice_value: Optional[str] = None
    expected_action_id: Optional[str] = None
    calculation: Optional[Dict[str, Any]] = None


class StaleActionError(RuntimeError):
    pass


class SubjectBoundaryError(ValueError):
    pass


class ConsultationBusyError(RuntimeError):
    pass


class ConsultationEngine(ObservationMixin):
    """Stateful observe-plan-act loop with deterministic clinical guardrails."""

    def __init__(
        self,
        content: ContentRepository,
        triage: TriageService,
        self_checks: SelfCheckService,
        events: EventStore,
        ranker: QuestionRanker,
    ) -> None:
        self.content = content
        self.triage = triage
        self.self_checks = self_checks
        self.events = events
        self.ranker = ranker
        self.interpreter = Interpreter()
        self._sessions: Dict[str, CaseState] = {}
        self._session_locks: Dict[str, RLock] = {}
        self._session_conditions = {}
        self._latest_replies = {}
        self._help_requests = {}
        self._sessions_lock = Lock()

    def create_session(self) -> tuple[CaseState, AgentReply]:
        state = self._create_state()
        reply = self._ask(state, self.content.questions[0], reason="开始会谈并确认咨询对象")
        self._record_reply(state, reply, "session_start", "开始会谈并确认咨询对象")
        return state, reply

    def _create_state(self) -> CaseState:
        session_id = str(uuid4())
        state = CaseState(session_id=session_id)
        state.runtime_snapshot = {
            "engine_version": __version__,
            "composition": self.content.registry.snapshot(),
            "content_versions": self.content.versions,
            "transport": dict(getattr(self, "runtime_descriptor", {})),
        }
        with self._sessions_lock:
            self._sessions[session_id] = state
            self._session_locks[session_id] = RLock()
            self._session_conditions[session_id] = Condition(
                self._session_locks[session_id]
            )
        self.events.append(
            Event(
                session_id,
                "session.created",
                {
                    "content_versions": self.content.versions,
                    "runtime_snapshot": state.runtime_snapshot,
                },
            )
        )
        return state

    def state(self, session_id: str) -> CaseState:
        with self._sessions_lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            return self._sessions[session_id]

    def submit(self, session_id: str, user_input: UserInput) -> AgentReply:
        state = self.state(session_id)
        with self._session_locks[session_id]:
            return self._submit(state, user_input)

    def run_capability(
        self, state, identity, arguments, origin, turn_id, confirmation=None
    ):
        if state.phase == Phase.CLOSED or state.risk.level in {
            RiskLevel.EMERGENCY,
            RiskLevel.URGENT,
        }:
            raise ValueError("当前会谈已结束或需要优先求助，不能继续执行计算")
        if origin == "model_proposal":
            raise ValueError("模型提议必须先展示输入卡，经用户核对后才能计算")
        self.events.append(
            Event(
                state.session_id,
                "capability.requested",
                {"id": identity, "input": arguments},
                turn_id=turn_id,
            )
        )
        try:
            result = self.content.registry.execute(identity, arguments, origin)
        except Exception as exc:
            self.events.append(
                Event(
                    state.session_id,
                    "capability.failed",
                    {"id": identity, "error_type": type(exc).__name__},
                    turn_id=turn_id,
                )
            )
            raise ValueError(
                str(exc) if isinstance(exc, ValueError) else "能力执行失败，未写入结果"
            ) from exc
        result = {**result, "execution_id": str(uuid4())}
        if confirmation is not None:
            result["input_status"] = "confirmed"
            result["confirmation"] = deepcopy(confirmation)
        result["presentation"] = calculation_presentation(result)
        state.calculations.append(result)
        self.events.append(
            Event(state.session_id, "capability.completed", result, turn_id=turn_id)
        )
        return result

    def request_calculation(self, state, identity, arguments, evidence, text, turn_id):
        if (
            state.phase == Phase.CLOSED
            or state.risk.level in {RiskLevel.EMERGENCY, RiskLevel.URGENT}
            or state.active_task
        ):
            raise ValueError("当前应先处理求助或正在进行的图示任务")
        draft = request_draft(
            self.content.registry, identity, arguments, evidence, text, turn_id
        )
        previous = next(
            (
                event.payload["reply"]
                for event in reversed(self.events.for_session(state.session_id))
                if event.event_type == "turn.agent_reply" and "reply" in event.payload
            ),
            None,
        )
        state.calculation_resume = {
            "question_id": state.active_question,
            "clarification": state.active_question_is_clarification,
            "phase": state.phase.value,
            "reply": deepcopy(previous),
        }
        state.pending_calculation = draft
        self.events.append(
            Event(
                state.session_id,
                "capability.input_requested",
                deepcopy(draft),
                turn_id=turn_id,
            )
        )
        return self._calculation_input_reply(state)

    def _calculation_input_reply(self, state, feedback=""):
        draft = deepcopy(state.pending_calculation)
        draft["feedback"] = feedback
        return AgentReply(
            "核对这三个输入，再开始换算。",
            state.phase,
            kind="calculation_input",
            calculation_input=draft,
        )

    def _check_calculation_action(self, state, user_input):
        choice = user_input.choice_value
        if choice not in {
            "confirm_calculation",
            "skip_calculation",
            "resume_after_calculation",
        }:
            if user_input.calculation is not None:
                raise StaleActionError("计算参数需要对应当前输入卡，尚未提交")
            return
        if user_input.expected_action_id != f"{state.session_id}:{state.revision}":
            raise StaleActionError("输入卡已更新，请核对当前卡片后提交")
        payload = user_input.calculation
        if not isinstance(payload, dict):
            raise StaleActionError("未收到当前计算卡片的标识")
        if choice == "resume_after_calculation":
            if (
                set(payload) != {"execution_id"}
                or not state.calculation_resume
                or payload["execution_id"]
                != state.calculation_resume.get("execution_id")
            ):
                raise StaleActionError("这项计算已处理，请使用当前问诊页面")
        elif (
            set(payload) != {"proposal_id", "input"}
            or not state.pending_calculation
            or payload["proposal_id"] != state.pending_calculation["proposal_id"]
        ):
            raise StaleActionError("计算提议已失效，请使用当前输入卡")

    def _clear_calculation(self, state, reason, turn_id=None):
        if state.pending_calculation:
            self.events.append(
                Event(
                    state.session_id,
                    "capability.input_cancelled",
                    {
                        "proposal_id": state.pending_calculation["proposal_id"],
                        "reason": reason,
                    },
                    turn_id=turn_id,
                )
            )
        state.pending_calculation = None
        state.calculation_resume = None

    def _resume_calculation(self, state):
        resume = state.calculation_resume or {}
        state.pending_calculation = None
        state.calculation_resume = None
        old = resume.get("reply") or {}
        question = next(
            (
                q
                for q in self.content.questions
                if q.id == resume.get("question_id") and q.id == state.active_question
            ),
            None,
        )
        if question and (
            question.fact_key not in state.facts or resume.get("clarification")
        ):
            reply = self._ask(state, question, "计算交互结束，继续此前未回答的问题")
            if old.get("question_id") == question.id and old.get("kind") == "question":
                reply.message = old["message"]
                reply.quick_replies = [
                    QuickReply(**item) for item in old.get("quick_replies", [])
                ]
                state.active_question_is_clarification = bool(
                    resume.get("clarification")
                )
                if state.active_question_is_clarification:
                    state.phase = reply.phase = Phase(resume["phase"])
            return reply
        if (
            resume.get("phase") == "summary"
            and state.phase == Phase.SUMMARY
            and old.get("kind") == "summary"
        ):
            return self._finish(state)
        return None

    def _handle_calculation_action(self, state, user_input, text, turn_id):
        choice = user_input.choice_value
        if choice == "confirm_calculation":
            draft = state.pending_calculation
            try:
                values = confirmed_parameters(
                    self.content.registry, draft, user_input.calculation["input"]
                )
                result = self.run_capability(
                    state,
                    draft["capability_id"],
                    values,
                    "patient_confirmed_form",
                    turn_id,
                    confirmation={
                        "proposal_id": draft["proposal_id"],
                        "source_quote": draft["source_quote"],
                        "source_turn_id": draft["source_turn_id"],
                        "confirmation_turn_id": turn_id,
                        "original_input": deepcopy(draft["original_input"]),
                        "confirmed_input": deepcopy(values),
                    },
                )
            except (ValueError, TypeError):
                candidate = user_input.calculation.get("input")
                if isinstance(candidate, dict):
                    for key, value in candidate.items():
                        if (
                            key == "value"
                            and isinstance(value, str)
                            and len(value) <= 25
                        ):
                            state.pending_calculation["input"][key] = value
                        elif (
                            key in {"from_unit", "to_unit"}
                            and isinstance(value, str)
                            and value
                            in {
                                "kg",
                                "g",
                                "mg",
                                "L",
                                "mL",
                            }
                        ):
                            state.pending_calculation["input"][key] = value
                return self._calculation_input_reply(
                    state, "未完成计算。请检查数值、单位；质量与体积不能直接互换。"
                )
            state.pending_calculation = None
            state.calculation_resume["execution_id"] = result["execution_id"]
            return AgentReply(
                "换算已完成，参数已核对。实际测量质量仍未评价。",
                state.phase,
                kind="calculation_result",
                calculation_result=deepcopy(result),
            )
        if choice in {"skip_calculation", "resume_after_calculation"}:
            if choice == "skip_calculation":
                self.events.append(
                    Event(
                        state.session_id,
                        "capability.input_cancelled",
                        {
                            "proposal_id": state.pending_calculation["proposal_id"],
                            "reason": "patient_skipped",
                        },
                        turn_id=turn_id,
                    )
                )
            return self._resume_calculation(state)
        if state.pending_calculation or state.calculation_resume:
            self._clear_calculation(state, "new_patient_message", turn_id)
        return None

    def _check_fact_version(self, state, key, expected_fact_id):
        if expected_fact_id is not None and (
            key not in state.facts or state.facts[key].fact_id != expected_fact_id
        ):
            raise StaleActionError("这项记录已更新，请核对最新内容后再更正；你的输入尚未提交")

    def _prepare_fact_updates(self, state, updates, turn_id):
        """Apply shared dependency rules only after the whole batch validates.

        A confirmed consultation subject is a case boundary. Age corrections
        refer to the same person and retain their reported measurements.
        """
        for item in updates:
            if item["key"] == "consult_for" and "consult_for" in state.facts:
                previous = state.facts["consult_for"]
                if previous.assertion == Assertion.PRESENT and (
                    item["assertion"] != previous.assertion
                    or item["value"] != previous.value
                ):
                    raise SubjectBoundaryError("咨询对象已固定。更换咨询对象请开始新的问诊，以免混用不同人的病史")
        if updates and (state.pending_calculation or state.calculation_resume):
            self._clear_calculation(state, "case_updated", turn_id)
        if updates and state.observation_resume:
            self._clear_observation(state, "case_updated", turn_id)
        changed = {
            item["key"]
            for item in updates
            if item["key"] in state.facts
            and (
                state.facts[item["key"]].value != item["value"]
                or state.facts[item["key"]].assertion != item["assertion"]
            )
        }
        affected = {
            task["result_key"]
            for task in self.content.tasks.values()
            if changed.intersection(task.get("invalidate_on", ["chief_complaint"]))
        }
        if "chief_complaint" in changed:
            for request in state.pending_self_checks:
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.request_cancelled",
                        {**request, "because": "chief_complaint_corrected"},
                        turn_id=turn_id,
                    )
                )
            state.pending_self_checks.clear()
            affected |= {
                "emergency_screen",
                "onset",
                "trajectory",
                "severity",
                "associated_symptoms",
                "resp_breathing",
                "resp_cough_details",
                "resp_intake",
                "medications_taken",
                "patient_goal",
            }
        # Explicit replacement evidence in this batch belongs to the new state.
        affected -= {item["key"] for item in updates}
        for key in sorted(affected):
            old = state.facts.pop(key, None)
            if old:
                self.events.append(
                    Event(
                        state.session_id,
                        "fact.invalidated",
                        {
                            "fact_key": key,
                            "previous_fact_id": old.fact_id,
                            "because": "chief_complaint_corrected"
                            if "chief_complaint" in changed
                            else "task_dependency_corrected",
                        },
                        turn_id=turn_id,
                    )
                )
        if changed:
            if state.active_task:
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.cancelled",
                        {
                            "task_id": state.active_task,
                            "because": "facts_corrected",
                        },
                        turn_id=turn_id,
                    )
                )
            state.active_task = None
            state.active_question = None
            state.pending_input = None
            state.active_question_is_clarification = False
            for key in affected | changed:
                state.followup_counts.pop(key, None)

    def correct_fact(
        self,
        session_id: str,
        fact_key: str,
        new_text: str,
        expected_fact_id: Optional[str] = None,
    ) -> AgentReply:
        state = self.state(session_id)
        with self._session_locks[session_id]:
            urgent_reply = self._correction_emergency(state, new_text)
            if urgent_reply:
                return urgent_reply
            self._check_fact_version(state, fact_key, expected_fact_id)
            previous_task = state.active_task
            question = next(
                (item for item in self.content.questions if item.fact_key == fact_key),
                None,
            )
            if question is None or fact_key == "emergency_screen":
                raise ValueError("该字段不支持直接更正")
            turn_id = str(uuid4())
            result = self.interpreter.interpret(question, new_text)
            if question.input == "age" and (
                not isinstance(result.value, (int, float))
                or not 0 <= result.value <= 120
            ):
                raise ValueError("年龄请写成“8个月”或“32岁”这样的格式")
            self._prepare_fact_updates(
                state,
                [
                    {
                        "key": fact_key,
                        "value": result.value,
                        "assertion": result.assertion,
                    }
                ],
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
            self._record_fact(state, fact_key, result, turn_id, new_text)
            state.active_question = None
            state.active_task = None
            state.risk = self.triage.assess(state, new_text)
            if state.risk.level == RiskLevel.EMERGENCY:
                reply = self._emergency_reply(state, state.risk.triggers)
            elif state.risk.level == RiskLevel.URGENT:
                reply = self._urgent(state)
            else:
                state.closed_reason = None
                resumed = (
                    self._offer_task(
                        state, str(state.get("chief_complaint", "")), previous_task
                    )
                    if previous_task
                    else None
                )
                reply = resumed or self._plan_next(state)
            self._record_reply(state, reply, turn_id, f"用户更正字段 {fact_key} 后重新规划")
            return reply

    def _correction_emergency(self, state, text):
        triggers = self.triage.scan_text(text)
        if not triggers:
            return None
        turn_id = str(uuid4())
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {"text": text, "origin": "fact_correction"},
                turn_id=turn_id,
            )
        )
        state.transcript.append({"role": "user", "content": text})
        state.risk = self.triage.assess(state, text)
        reply = self._emergency_reply(state, state.risk.triggers)
        state.pending_input = None
        state.transcript.append({"role": "assistant", "content": reply.message})
        self._record_reply(state, reply, turn_id, "更正原文命中急症门，优先于字段和旧版本校验")
        return reply

    def _requested_emergency_help(self, state, text):
        """A user requests priority help; this does not invent a symptom fact."""
        if (
            state.closed_reason == "emergency_route"
            and self._help_requests.get(state.session_id) == text
        ):
            return deepcopy(self._latest_replies[state.session_id])
        turn_id = str(uuid4())
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {
                    "text": text,
                    "origin": "requested_emergency_help",
                    "choice_value": "request_emergency_help",
                },
                turn_id=turn_id,
            )
        )
        state.transcript.append({"role": "user", "content": text})
        state.risk = RiskAssessment(
            RiskLevel.EMERGENCY, sorted(set([*state.risk.triggers, "用户主动请求紧急帮助"]))
        )
        reply = self._emergency_reply(state, state.risk.triggers, requested_help=True)
        reply.developer_trace = {"reason": "用户主动请求紧急帮助，未新增症状事实"}
        state.pending_input = None
        state.transcript.append({"role": "assistant", "content": reply.message})
        self._record_reply(state, reply, turn_id, "用户主动请求优先求助")
        self._help_requests[state.session_id] = text
        return reply

    def _submit(self, state: CaseState, user_input: UserInput) -> AgentReply:
        if user_input.choice_value == "request_emergency_help":
            return self._requested_emergency_help(state, user_input.text)
        current_action = f"{state.session_id}:{state.revision}"
        if (
            user_input.expected_action_id
            and user_input.expected_action_id != current_action
            and not self.triage.scan_text(user_input.text)
        ):
            self.events.append(
                Event(
                    state.session_id,
                    "turn.rejected",
                    {
                        "reason": "stale_action",
                        "expected": user_input.expected_action_id,
                        "current": current_action,
                    },
                )
            )
            raise StaleActionError("页面上的问题已经变化，请按当前问题重新回答")
        turn_id = str(uuid4())
        text = user_input.text.strip()
        if not self.triage.scan_text(text):
            self._check_observation_action(state, user_input)
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {"text": text, "choice_value": user_input.choice_value},
                turn_id=turn_id,
            )
        )
        direct_triggers = self.triage.scan_text(text)
        if direct_triggers:
            state.risk = self.triage.assess(state, text)
            reply = self._emergency_reply(state, direct_triggers)
            self._record_reply(state, reply, turn_id, "原始输入命中确定性急症门")
            return reply

        if state.phase == Phase.CLOSED:
            reply = (
                self._emergency_reply(state, state.risk.triggers)
                if state.closed_reason == "emergency_route"
                else AgentReply(
                    "这次问诊已经结束。如情况有变化，请开始一次新的问诊；出现紧急情况请立即拨打 120。",
                    Phase.CLOSED,
                    kind="closed",
                )
            )
            self._record_reply(state, reply, turn_id, "会话已关闭")
            return reply

        observation_reply = self._handle_observation_action(
            state, user_input, text, turn_id
        )
        if observation_reply is not None:
            self._record_reply(state, observation_reply, turn_id, "重复观察及原问诊接续")
            return observation_reply

        if state.phase == Phase.SUMMARY:
            if user_input.choice_value == "confirm_summary":
                state.phase = Phase.CLOSED
                state.closed_reason = "confirmed_by_user"
                state.active_question = None
                reply = AgentReply(
                    "已核对本次摘要。可以回看记录；情况变化时请重新评估。", Phase.CLOSED, kind="closed"
                )
            else:
                reply = self._finish(state)
                reply.message = "请点击摘要或右侧记录中的“更正”，修改对应信息后再核对。"
            self._record_reply(state, reply, turn_id, "用户核对摘要")
            return reply

        if state.active_task:
            failure = self._try_task_result(
                state, text, user_input.choice_value, turn_id
            )
            if failure:
                self._record_reply(state, failure, turn_id, "图示反馈失败，保持当前任务")
                return failure
        elif state.active_question:
            question = self.content.question(state.active_question)
            result = self.interpreter.interpret(question, text, user_input.choice_value)
            if question.input == "age" and (
                not isinstance(result.value, (int, float))
                or not 0 <= result.value <= 120
            ):
                reply = AgentReply(
                    "我没能确认年龄。请写成“8个月”或“32岁”这样的格式。",
                    question.phase,
                    kind="clarification",
                    question_id=question.id,
                    developer_trace={"reason": "年龄格式或范围未通过确定性校验，原输入未写入年龄字段"},
                )
                self._record_reply(state, reply, turn_id, "年龄输入未通过校验")
                return reply
            self._record_fact(state, question.fact_key, result, turn_id, text)
            state.active_question = None
            state.active_question_is_clarification = False
        else:
            self._record_fact(
                state,
                "unsolicited_note",
                Interpretation(text, Assertion.PRESENT, Source.PATIENT),
                turn_id,
                text,
            )

        state.risk = self.triage.assess(state, text)
        self.events.append(
            Event(
                state.session_id,
                "triage.assessed",
                state.risk.to_dict(),
                turn_id=turn_id,
            )
        )
        if state.risk.level == RiskLevel.EMERGENCY:
            reply = self._emergency_reply(state, state.risk.triggers)
            self._record_reply(state, reply, turn_id, "结构化急症门触发")
            return reply

        if state.risk.level == RiskLevel.URGENT:
            reply = self._urgent(state)
            self._record_reply(state, reply, turn_id, "风险升级后优先求助")
            return reply

        reply = self._plan_next(state)
        self._record_reply(
            state,
            reply,
            turn_id,
            reply.developer_trace.get("reason", "") if reply.developer_trace else "",
        )
        return reply

    def _record_fact(
        self,
        state: CaseState,
        key: str,
        result: Interpretation,
        turn_id: str,
        evidence: Optional[str] = None,
    ) -> None:
        source = result.source
        if source == Source.PATIENT and state.get("consult_for") in {
            "child",
            "other_adult",
        }:
            source = Source.CAREGIVER
        fact = Fact(
            key=key,
            value=result.value,
            assertion=result.assertion,
            source=source,
            quality=result.quality,
            turn_id=turn_id,
            evidence=evidence,
        )
        state.record(fact)
        self.events.append(
            Event(state.session_id, "fact.recorded", fact.to_dict(), turn_id=turn_id)
        )

    def _try_task_result(self, state, text, choice_value, turn_id):
        try:
            decoded = self.self_checks.interpret(state.active_task, text, choice_value)
        except Exception as exc:
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.failed",
                    {"task_id": state.active_task, "error_type": type(exc).__name__},
                    turn_id=turn_id,
                )
            )
            task = self.content.tasks[state.active_task]
            message = (
                str(exc)
                if isinstance(exc, ObservationInputError)
                else "这一步的反馈暂时未能处理，结果没有写入。你可以重新填写，或选择跳过。"
            )
            return AgentReply(
                message,
                state.phase,
                kind="self_check",
                input_error=True,
                task=task,
                quick_replies=[QuickReply(**item) for item in task["options"]],
            )
        self._accept_task_result(
            state, state.active_task, text, choice_value, turn_id, decoded
        )
        return None

    def _accept_task_result(
        self,
        state: CaseState,
        task_id: str,
        text: str,
        choice_value: Optional[str],
        turn_id: str,
        decoded=None,
    ) -> None:
        task_result = decoded or self.self_checks.interpret(task_id, text, choice_value)
        result = task_result.interpretation
        resume = state.observation_resume
        previous = state.facts.get(task_result.fact_key)
        preserve = bool(
            resume
            and previous
            and previous.assertion == Assertion.PRESENT
            and result.assertion != Assertion.PRESENT
        )
        if not preserve:
            self._record_fact(state, task_result.fact_key, result, turn_id, text)
        if resume:
            from .domain import utc_now

            attempt = next(
                a for a in state.observation_attempts if a["id"] == resume["attempt_id"]
            )
            attempt.update(
                status="recorded"
                if result.assertion == Assertion.PRESENT
                else "skipped"
                if result.quality == ObservationQuality.NOT_DONE
                else "unconfirmed",
                finished_at=utc_now(),
                evidence=text,
                quality=result.quality.value,
                source=(
                    Source.CAREGIVER
                    if state.get("consult_for") in {"child", "other_adult"}
                    else result.source
                ).value,
            )
            if result.assertion == Assertion.PRESENT:
                attempt["fact_id"] = state.facts[task_result.fact_key].fact_id
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.repeat_completed",
                    deepcopy(attempt),
                    turn_id=turn_id,
                )
            )
        state.active_task = None
        self.events.append(
            Event(
                state.session_id,
                "self_check.completed",
                {
                    "task_id": task_id,
                    "quality": task_result.interpretation.quality.value,
                    "assertion": task_result.interpretation.assertion.value,
                    "previous_reading_preserved": preserve,
                },
                turn_id=turn_id,
            )
        )

    def _plan_next(self, state: CaseState) -> AgentReply:
        chief = str(state.get("chief_complaint", ""))
        candidates = [
            question
            for question in self.content.questions
            if question.fact_key not in state.facts
            and self.content.question_applies(question, chief)
        ]
        if candidates:
            earliest_phase = min(self._phase_rank(item.phase) for item in candidates)
            same_phase = [
                item
                for item in candidates
                if self._phase_rank(item.phase) == earliest_phase
            ]
            if same_phase[0].phase == Phase.BACKGROUND:
                offered = self._offer_task(state, chief)
                if offered:
                    return offered
            selected_id = self.ranker.choose(state, same_phase)
            selected = next(
                (item for item in same_phase if item.id == selected_id), same_phase[0]
            )
            return self._ask(
                state, selected, reason=f"{selected.fact_key} 是当前阶段尚未获得的关键字段"
            )

        offered = self._offer_task(state, chief)
        if offered:
            return offered
        return self._finish(state)

    @staticmethod
    def _phase_rank(phase: Phase) -> int:
        order = {
            Phase.OPENING: 0,
            Phase.EMERGENCY_SCREEN: 1,
            Phase.HISTORY: 2,
            Phase.BACKGROUND: 3,
        }
        return order.get(phase, 9)

    def _offer_task(
        self, state: CaseState, chief: str, task_id: Optional[str] = None
    ) -> Optional[AgentReply]:
        if self._self_check_block_reason(state):
            return None
        tasks = [
            t
            for t in self.content.applicable_tasks(chief)
            if t["result_key"] not in state.facts
            and (task_id is None or t["id"] == task_id)
        ]
        task = tasks[0] if tasks else None
        if task:
            requests = [
                item
                for item in state.pending_self_checks
                if item["task_id"] == task["id"]
            ]
            state.pending_self_checks = [
                item
                for item in state.pending_self_checks
                if item["task_id"] != task["id"]
            ]
            state.phase = Phase.OBSERVATION
            state.active_question = None
            state.active_question_is_clarification = False
            state.active_task = task["id"]
            self.events.append(
                Event(
                    state.session_id,
                    "self_check.offered",
                    {
                        "task_id": task["id"],
                        "content_version": self.content.versions["self_checks"],
                        "requested_by_user": bool(requests),
                        "requests": requests,
                    },
                )
            )
            return AgentReply(
                "为了把当前情况记录得更清楚，可以完成下面这一步。做不到或不想做都可以跳过。",
                state.phase,
                kind="self_check",
                task=task,
                quick_replies=[QuickReply(**item) for item in task["options"]],
                developer_trace={"reason": "主诉符合固定任务的适用条件；任务结果保留自述来源"},
            )
        return None

    def _offer_pending_self_check(self, state):
        if self._self_check_block_reason(state):
            return None
        for request in list(state.pending_self_checks):
            reply = self._offer_task(
                state, str(state.get("chief_complaint", "")), request["task_id"]
            )
            if reply:
                return reply
        return None

    def _sync_task_requests(self, state, text, turn_id):
        """Keep a bounded subset of explicit task requests independent of LLM recall.

        Matching records an intent only. All execution eligibility checks remain
        in _offer_task. Negative preferences are never measurements.
        """
        for task in self.content.applicable_tasks(
            str(state.get("chief_complaint", ""))
        ):
            key, identity = task["result_key"], task["id"]
            intent, evidence = task_intent(
                text, task.get("request_phrases", []), task.get("cancel_phrases", [])
            )
            cancellation = evidence if intent == "cancel" else None
            previous = state.facts.get(key)
            if cancellation:
                state.pending_self_checks = [
                    item
                    for item in state.pending_self_checks
                    if item["task_id"] != identity
                ]
                if state.active_task == identity:
                    state.active_task = None
                    state.phase = Phase.HISTORY
                # Preserve any existing reading. Declining a future action cannot
                # retract already reported evidence.
                if previous is None:
                    result = self.self_checks.interpret(identity, text, "skip")
                    self._record_fact(
                        state, key, result.interpretation, turn_id, cancellation
                    )
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.request_cancelled",
                        {
                            "task_id": identity,
                            "evidence": cancellation,
                            "because": "user_declined",
                        },
                        turn_id=turn_id,
                    )
                )
                continue
            evidence = evidence if intent == "request" else None
            if not evidence or state.active_task == identity:
                continue
            if previous is not None:
                if (
                    previous.assertion != Assertion.UNKNOWN
                    or previous.quality != ObservationQuality.NOT_DONE
                ):
                    continue
                state.facts.pop(key)
                self.events.append(
                    Event(
                        state.session_id,
                        "fact.invalidated",
                        {
                            "fact_key": key,
                            "previous_fact_id": previous.fact_id,
                            "because": "user_renewed_task_request",
                        },
                        turn_id=turn_id,
                    )
                )
            if not any(
                item["task_id"] == identity for item in state.pending_self_checks
            ):
                request = {
                    "task_id": identity,
                    "evidence": evidence,
                    "turn_id": turn_id,
                }
                state.pending_self_checks.append(request)
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.requested",
                        {**request, "origin": "declared_literal_request"},
                        turn_id=turn_id,
                    )
                )

    def _ask(self, state: CaseState, question: Question, reason: str) -> AgentReply:
        state.phase = question.phase
        state.active_question = question.id
        state.active_question_is_clarification = False
        state.active_task = None
        if question.id not in state.asked_questions:
            state.asked_questions.append(question.id)
        return AgentReply(
            question.prompt,
            question.phase,
            kind="question",
            question_id=question.id,
            quick_replies=question.options,
            developer_trace={
                "reason": reason,
                "content_version": self.content.versions["questions"],
            },
        )

    def _emergency_reply(
        self, state: CaseState, triggers: List[str], requested_help=False
    ) -> AgentReply:
        self._clear_calculation(state, "emergency")
        self._clear_observation(state, "emergency")
        state.phase = Phase.CLOSED
        state.closed_reason = "emergency_route"
        state.active_question = None
        state.active_question_is_clarification = False
        state.active_task = None
        trigger_text = "、".join(sorted(set(triggers)))
        introduction = (
            "你已选择优先求助。本次问诊已停止。"
            if requested_help
            else f"你描述的情况包含需要立即处理的危险信号（{trigger_text}）。"
        )
        return AgentReply(
            introduction + "请现在拨打 120，或让身边的人协助呼叫急救；不要自己驾车。不要继续等待本次对话。",
            Phase.CLOSED,
            kind="emergency",
            summary={"care_route": "立即急救", "triggers": sorted(set(triggers))},
            developer_trace={"reason": "确定性急症规则优先于后续问诊"},
        )

    def _urgent(self, state: CaseState) -> AgentReply:
        self._clear_calculation(state, "urgent")
        self._clear_observation(state, "urgent")
        reply = self._finish(state)
        reply.kind = "urgent"
        reply.message = (
            "目前有需要尽快就医评估的线索（"
            + "、".join(state.risk.triggers)
            + "）。建议今天联系医疗机构；下面是已采集的信息，不必等问诊补齐再求助。"
        )
        state.closed_reason = "urgent_route"
        return reply

    def _source_turns(self, state: CaseState) -> Dict[str, str]:
        source_turns = {}
        for event in self.events.for_session(state.session_id):
            if event.event_type == "turn.user_message":
                source_turns[event.turn_id] = event.payload.get("text")
            elif event.event_type == "fact.correction_requested":
                source_turns[event.turn_id] = event.payload.get("new_text")
        return source_turns

    @staticmethod
    def _fact_provenance(fact: Fact, source_turns: Dict[str, str]) -> Dict[str, Any]:
        detail = {
            "evidence": fact.evidence or "",
            "source": fact.source.value,
            "quality": fact.quality.value,
        }
        source_text = source_turns.get(fact.turn_id)
        if (
            fact.evidence
            and isinstance(source_text, str)
            and fact.evidence in source_text
            and source_text != fact.evidence
            and fact.source in {Source.PATIENT, Source.CAREGIVER}
        ):
            detail["context"] = {
                "scope": "source_turn",
                "turn_id": fact.turn_id,
                "text": source_text,
            }
        return detail

    def _finish(self, state: CaseState) -> AgentReply:
        state.phase = Phase.SUMMARY
        state.risk = self.triage.assess(state)
        known: List[Dict[str, Any]] = []
        unknown: List[str] = []
        unknown_details: List[Dict[str, Any]] = []
        source_turns = self._source_turns(state)
        for key, fact in state.facts.items():
            task = next(
                (
                    task
                    for task in self.content.tasks.values()
                    if task["result_key"] == key
                ),
                {},
            )
            label = FACT_LABELS.get(
                key, task.get("result_label", task.get("title", key))
            )
            detail = {
                "key": key,
                "label": label,
                **self._fact_provenance(fact, source_turns),
            }
            if fact.assertion == Assertion.UNKNOWN:
                unknown.append(label)
                # An unresolved field can still contain useful patient reports.
                # Preserve its actual evidence without promoting it to a known
                # value, splitting its meaning, or reusing superseded evidence.
                unknown_details.append(detail)
                continue
            if fact.assertion == Assertion.ABSENT:
                display = "患者否认上述危险信号" if key == "emergency_screen" else "否认/没有"
            else:
                display = VALUE_LABELS.get(str(fact.value), fact.value)
                if key == "age_years" and isinstance(display, (int, float)):
                    display = (
                        f"{round(display * 12)} 个月" if display < 2 else f"{display} 岁"
                    )
                if key == "temperature_c" and isinstance(display, (int, float)):
                    display = f"{display} ℃（腋温）"
            known.append({**detail, "value": display})

        if state.risk.level == RiskLevel.URGENT:
            route = "建议尽快线下评估；基于当前规则，优先考虑今天联系医疗机构。"
        else:
            route = "本次已完成结构化信息整理。系统没有据此作出诊断；请结合症状变化和专业医疗评估决定下一步。"
        summary = {
            "content_versions": self.content.versions,
            "known": known,
            "unknown": unknown,
            "unknown_details": unknown_details,
            "care_route": route,
            "risk_level": state.risk.level.value,
            "risk_triggers": state.risk.triggers,
            "return_now_if": ["意识异常或抽搐", "严重呼吸困难或嘴唇发紫", "持续压榨样胸痛", "大量或无法控制的出血"],
            "observations": [
                self._observation_series(state, task)
                for task in self.content.tasks.values()
                if task.get("repeatable") and task["result_key"] in state.facts
            ],
        }
        needs_confirmation = state.risk.level not in {
            RiskLevel.EMERGENCY,
            RiskLevel.URGENT,
        }
        state.phase = Phase.SUMMARY if needs_confirmation else Phase.CLOSED
        state.closed_reason = None if needs_confirmation else "urgent_route"
        state.active_question = f"summary:{uuid4()}" if needs_confirmation else None
        state.active_question_is_clarification = False
        state.active_task = None
        return AgentReply(
            "问诊信息已整理完成。请先核对摘要；其中“未知”不代表没有，测量结果也保留了来源和质量。",
            state.phase,
            kind="summary",
            action_id=state.active_question,
            quick_replies=[
                QuickReply("摘要准确", "confirm_summary"),
                QuickReply("我需要更正", "correct_summary"),
            ]
            if needs_confirmation
            else [],
            summary=summary,
            developer_trace={"reason": "必要通用字段与适用的定向字段均已处理"},
        )

    def _record_reply(
        self, state: CaseState, reply: AgentReply, turn_id: str, reason: str
    ) -> None:
        with self._session_conditions[state.session_id]:
            state.revision += 1
            reply.action_id = f"{state.session_id}:{state.revision}"
            self.events.append(
                Event(
                    state.session_id,
                    "turn.agent_reply",
                    {"reply": reply.to_dict(), "reason": reason},
                    turn_id=turn_id,
                )
            )
            self._latest_replies[state.session_id] = deepcopy(reply)
            self._session_conditions[state.session_id].notify_all()

    @staticmethod
    def _self_check_block_reason(state):
        if (
            state.risk.level in {RiskLevel.EMERGENCY, RiskLevel.URGENT}
            or state.phase == Phase.CLOSED
        ):
            return "当前应优先求助或会谈已结束"
        if (
            "emergency_screen" not in state.facts
            or state.assertion("emergency_screen") != Assertion.ABSENT
        ):
            return "自查之前需要明确急症筛查；未知不能当阴性"
        return None
