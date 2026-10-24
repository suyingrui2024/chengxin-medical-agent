from __future__ import annotations

import os
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
from .publication import ReplyPublicationGate
from .state_transitions import commit_phase
from .interpreter import Interpretation, Interpreter, ObservationInputError
from .knowledge import (
    ADULT_AGE_PROMPT,
    ContentRepository,
    Question,
    question_fact_gates_pass,
)
from .self_checks import SelfCheckService
from .triage import TriageService
from .turn_context import TurnContext
from .planner import RulePlanner
from .session_store import SessionStore
from .phase_graph import planning_rank
from .followups import RETRIAGE_OUTCOMES, apply_outcome, build_follow_up_plan
from .reasoner import QuestionRanker
from . import __version__
from .bridging import build_bridging_sentence
from .presentation import calculation_presentation
from .task_intents import task_intent
from .patient_calculations import request_draft, confirmed_parameters
from .observations import ObservationMixin
from .task_conversation import TaskConversationMixin
from .calculation_followup import CalculationFollowupMixin
from .consultation_subject import (
    subject_profile,
    proposed_profile,
    compatible,
    explicit_switch,
    conflicting_reference,
)


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
    observation: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None


class StaleActionError(RuntimeError):
    pass


class SubjectBoundaryError(ValueError):
    def __init__(self, message, requested=None):
        super().__init__(message)
        self.requested = requested


class ConsultationBusyError(RuntimeError):
    pass


class ConsultationEngine(
    TaskConversationMixin, ObservationMixin, CalculationFollowupMixin
):
    """Stateful observe-plan-act loop with deterministic clinical guardrails."""

    def __init__(
        self,
        content: ContentRepository,
        triage: TriageService,
        self_checks: SelfCheckService,
        events: EventStore,
        ranker: QuestionRanker,
        session_store: Optional[SessionStore] = None,
    ) -> None:
        self.content = content
        self.triage = triage
        self.self_checks = self_checks
        self.events = events
        self.ranker = ranker
        self.interpreter = Interpreter()
        self.publication_gate = ReplyPublicationGate()
        # Default planner: DifferentialPlanner (hypothesis-driven question
        # order), promoted 2026-10-23 after the A/B evaluation showed no
        # outcome regressions and earlier discriminating-question capture
        # on every covered pathway (scripts/evaluation/medical/
        # evaluate_planner_ab.py). MEDAGENT_PLANNER=rule is the rollback.
        if os.environ.get("MEDAGENT_PLANNER", "differential").strip().lower() == "rule":
            self.planner = RulePlanner(self)
        else:
            from .reasoning import DifferentialPlanner

            self.planner = DifferentialPlanner(self)
        self._turn_pipeline = (
            ("location_revision", self._h_location_revision),
            ("closed_session", self._h_closed_session),
            ("subject_control", self._h_subject_control),
            ("prepared_location", self._h_prepared_location),
            ("observation_action", self._h_observation_action),
            ("summary_review", self._h_summary_review),
            ("intake", self._h_intake),
            ("risk_escalation", self._h_risk_escalation),
            ("planning", self._h_planning),
        )
        self._sessions: Dict[str, CaseState] = {}
        self._session_locks: Dict[str, RLock] = {}
        self._session_conditions = {}
        self._latest_replies = {}
        self._help_requests = {}
        self._sessions_lock = Lock()
        # Durable store for closed sessions (follow-up plans must outlive
        # the process). Restored sessions re-enter as read-only history:
        # their facts, conclusion and follow-up plan keep working,
        # including outcome recording and re-triage linkage.
        self.session_store = session_store
        self._persist_failures = 0
        if session_store is not None:
            for restored in session_store.load_closed():
                self._sessions[restored.session_id] = restored
                self._session_locks[restored.session_id] = RLock()
                self._session_conditions[restored.session_id] = Condition(
                    self._session_locks[restored.session_id]
                )

    def _persist(self, state: CaseState) -> None:
        """Write-through snapshot; fail-open by design (a store error
        must never break a consultation)."""
        if self.session_store is None:
            return
        try:
            self.session_store.save(state)
        except Exception:
            self._persist_failures += 1

    def persist_state(self, state: CaseState) -> None:
        """Public write-through for callers that mutate a case outside
        the turn pipeline — server routes editing a closed case in
        place, post-conclusion enrichment writing back onto the sheet.
        Same fail-open semantics as _persist: durability for those
        mutations must not depend on a later turn happening to save."""
        self._persist(state)

    def create_session(
        self, request_id: Optional[str] = None
    ) -> tuple[CaseState, AgentReply]:
        trace_id = str(uuid4())
        state = self._create_state(trace_id, request_id)
        try:
            reply = self._ask(state, self.content.questions[0], reason="开始会谈并确认咨询对象")
            self._record_reply(state, reply, "session_start", "开始会谈并确认咨询对象")
        finally:
            self.events.clear_trace(state.session_id)
        return state, reply

    def _create_state(
        self, trace_id: Optional[str] = None, request_id: Optional[str] = None
    ) -> CaseState:
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
        if trace_id:
            self.events.bind_trace(session_id, trace_id, request_id)
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

    def _lock_for(self, session_id: str):
        """Fetch a session's lock atomically (a concurrent delete must
        surface as the same KeyError as an unknown session, never as a
        bare dict-index crash mid-request)."""
        with self._sessions_lock:
            if session_id not in self._session_locks:
                raise KeyError(session_id)
            return self._session_locks[session_id]

    def delete_session(self, session_id: str) -> None:
        """Forget one in-memory consultation and every controller-side reference."""
        with self._sessions_lock:
            if session_id not in self._sessions:
                raise KeyError(session_id)
            lock = self._session_locks[session_id]
        with lock:
            with self._sessions_lock:
                self._sessions.pop(session_id, None)
                self._session_locks.pop(session_id, None)
                self._session_conditions.pop(session_id, None)
                self._latest_replies.pop(session_id, None)
                self._help_requests.pop(session_id, None)
            if self.session_store is not None:
                try:
                    self.session_store.delete(session_id)
                except Exception:
                    self._persist_failures += 1

    def submit(self, session_id: str, user_input: UserInput) -> AgentReply:
        state = self.state(session_id)
        with self._lock_for(session_id):
            trace_id = str(uuid4())
            self.events.bind_trace(session_id, trace_id, user_input.request_id)
            try:
                return self._submit(state, user_input)
            finally:
                self.events.clear_trace(session_id)

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
        if state.calculation_feedback:
            raise ValueError("之前的计算请求仍有同轮补充待处理。先处理补充信息，不要重复已完成或已跳过的换算。")
        if (
            state.phase == Phase.CLOSED
            or state.risk.level in {RiskLevel.EMERGENCY, RiskLevel.URGENT}
            or state.active_task
            or state.self_check_conversation
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
        self._retain_calculation_followup(state, draft, text, turn_id)
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
        if choice and choice.startswith("process_calculation_supplement:"):
            if (
                user_input.expected_action_id != f"{state.session_id}:{state.revision}"
                or user_input.calculation is not None
                or not state.calculation_feedback
                or choice.split(":", 1)[1] != state.calculation_feedback["id"]
                or state.pending_calculation
                or state.calculation_resume
                or state.active_task
                or state.observation_feedback
            ):
                raise StaleActionError("补充入口已更新，请使用当前页面；原话未重复处理。")
            return
        if (
            choice == "confirm_summary"
            and state.calculation_feedback
            and not state.calculation_feedback["processing"]
        ):
            raise StaleActionError("还有同轮补充待处理，请先处理后再核对摘要。")
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
        if state.pending_calculation and state.calculation_feedback:
            state.calculation_feedback["outcome"] = "cancelled"
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
                reply.adaptive_question = deepcopy(old.get("adaptive_question"))
                # Same restore rule as the observation path: the restored
                # reply keeps its developer_trace (including the bridging
                # marker) so _record_reply does not prefix it a second time.
                reply.developer_trace = deepcopy(old.get("developer_trace"))
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
                                unit["value"]
                                for unit in state.pending_calculation["unit_options"]
                            }
                        ):
                            state.pending_calculation["input"][key] = value
                return self._calculation_input_reply(
                    state,
                    state.pending_calculation.get("feedback_hint", "未完成计算，请核对参数。"),
                )
            state.pending_calculation = None
            if state.calculation_feedback:
                state.calculation_feedback["outcome"] = "completed"
            state.calculation_resume["execution_id"] = result["execution_id"]
            return AgentReply(
                "换算已完成，参数已核对。实际测量质量仍未评价。",
                state.phase,
                kind="calculation_result",
                calculation_result=deepcopy(result),
            )
        if choice in {"skip_calculation", "resume_after_calculation"}:
            if choice == "skip_calculation":
                if state.calculation_feedback:
                    state.calculation_feedback["outcome"] = "skipped"
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
        current_subject = subject_profile(state)
        for item in updates:
            requested = (
                proposed_profile(item["value"], item.get("evidence"))
                if item["key"] == "consult_for"
                and item["assertion"] == Assertion.PRESENT
                else None
            )
            if item["key"] == "consult_for" and "consult_for" in state.facts:
                previous = state.facts["consult_for"]
                if previous.assertion == Assertion.PRESENT and (
                    item["assertion"] != previous.assertion
                    or item["value"] != previous.value
                ):
                    raise SubjectBoundaryError(
                        "咨询对象已固定。更换咨询对象请开始新的问诊，以免混用不同人的病史", requested
                    )
                if (
                    current_subject
                    and current_subject["reference"]
                    and requested
                    and requested["reference"]
                    and not compatible(
                        current_subject["reference"], requested["reference"]
                    )
                ):
                    raise SubjectBoundaryError("这次问诊的对象不同，请为另一位家人新建问诊。", requested)
        self._apply_fact_update_dependencies(state, updates, turn_id)

    @staticmethod
    def _subject_completion_fingerprint(state):
        return {
            "facts": {k: v.fact_id for k, v in state.facts.items()},
            "calculations": [v["execution_id"] for v in state.calculations],
            "observations": [
                (v["id"], v["status"]) for v in state.observation_attempts
            ],
        }

    def _subject_checkpoint(self, state):
        return {
            "reply": deepcopy(self._latest_replies.get(state.session_id)),
            "completion": self._subject_completion_fingerprint(state),
            "interaction": {
                key: deepcopy(getattr(state, key))
                for key in [
                    "phase",
                    "active_question",
                    "active_question_is_clarification",
                    "active_task",
                    "pending_input",
                    "pending_self_checks",
                    "pending_calculation",
                    "calculation_resume",
                    "observation_resume",
                    "self_check_conversation",
                    "calculation_feedback",
                    "observation_feedback",
                ]
            },
        }

    def _restore_subject_interaction(self, state, checkpoint, turn_id):
        if checkpoint["completion"] != self._subject_completion_fingerprint(state):
            return False
        old = checkpoint["interaction"]
        for request in state.pending_self_checks:
            if request not in old["pending_self_checks"]:
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.request_cancelled",
                        {**request, "because": "subject_boundary"},
                        turn_id=turn_id,
                    )
                )
        if old["pending_calculation"] and not state.pending_calculation:
            self.events.append(
                Event(
                    state.session_id,
                    "capability.input_restored",
                    {
                        "proposal_id": old["pending_calculation"]["proposal_id"],
                        "reason": "subject_boundary",
                    },
                    turn_id=turn_id,
                )
            )
        for key, value in old.items():
            setattr(state, key, deepcopy(value))
        return True

    def _subject_change_reply(self, state, requested, text, turn_id):
        if state.subject_resume is None:
            state.subject_resume = state.subject_candidate or self._subject_checkpoint(
                state
            )
        self._restore_subject_interaction(state, state.subject_resume, turn_id)
        current = subject_profile(state) or {"category": "unknown", "label": "当前就诊人"}
        requested = requested or {"category": "unknown", "label": "另一位就诊人"}
        data = {
            "current": {k: current[k] for k in ["category", "label"]},
            "requested": {k: requested[k] for k in ["category", "label"]},
            "source_text": text,
            "source_turn_id": turn_id,
        }
        self.events.append(
            Event(
                state.session_id,
                "subject.boundary_detected",
                deepcopy(data),
                turn_id=turn_id,
            )
        )
        return AgentReply(
            "请为另一位家人单独开始问诊，当前记录会保留。",
            state.phase,
            kind="subject_change",
            subject_change=data,
        )

    def _subject_control(self, state, user_input, text, turn_id):
        self._check_subject_action(state, user_input)
        if user_input.choice_value == "resume_current_subject":
            checkpoint = state.subject_resume
            state.subject_resume = None
            restored = self._restore_subject_interaction(state, checkpoint, turn_id)
            if restored:
                reply = deepcopy(checkpoint["reply"])
            else:
                reply = self._plan_next(state)
            self.events.append(
                Event(
                    state.session_id,
                    "subject.current_resumed",
                    {"restored_original_action": restored},
                    turn_id=turn_id,
                )
            )
            return reply
        state.subject_candidate = self._subject_checkpoint(state)
        requested = (
            explicit_switch(text, subject_profile(state)) if state.facts else None
        )
        if requested is None and state.active_question != "relevant_history":
            requested = conflicting_reference(text, subject_profile(state))
        if requested:
            return self._subject_change_reply(state, requested, text, turn_id)
        return None

    def _check_subject_action(self, state, user_input):
        if user_input.choice_value == "resume_current_subject" and (
            user_input.expected_action_id != f"{state.session_id}:{state.revision}"
            or state.subject_resume is None
            or getattr(self._latest_replies.get(state.session_id), "kind", None)
            != "subject_change"
        ):
            raise StaleActionError("就诊对象入口已更新，请使用当前页面。")

    def _apply_fact_update_dependencies(self, state, updates, turn_id):
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
        self._guard_calculation_followup_keys(state, affected)
        # Validate indirect removals before cancelling tasks or mutating facts.
        if updates and (state.pending_calculation or state.calculation_resume):
            self._clear_calculation(state, "case_updated", turn_id)
        if updates and state.observation_resume:
            self._clear_observation(state, "case_updated", turn_id)
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
            self._clear_task_conversation(state, "facts_corrected", turn_id)
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
        with self._lock_for(session_id):
            urgent_reply = self._correction_emergency(state, new_text)
            if urgent_reply:
                return urgent_reply
            if state.phase == Phase.CLOSED and state.closed_reason != "emergency_route":
                # Closed cases are read-only history (restored sessions
                # doubly so): reopening one here would fork it from its
                # conclusion sheet and, once persisted as non-CLOSED,
                # make its follow-up plan vanish on the next restart.
                # emergency_route is exempt: corrections there re-run
                # triage and land back on the emergency reply — the
                # route itself can never be retracted by an edit.
                raise ValueError("会谈已结束，记录不能再更正")
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

    def restore_fact(
        self,
        session_id: str,
        fact_key: str,
        expected_fact_id: Optional[str] = None,
    ) -> AgentReply:
        """Restore the value immediately preceding one UI correction.

        Restoration creates a new fact rather than mutating history, so the
        patient-visible record and the audit trail retain both the correction
        and the reversal.
        """
        state = self.state(session_id)
        with self._lock_for(session_id):
            if state.phase == Phase.CLOSED and state.closed_reason != "emergency_route":
                raise ValueError("会谈已结束，记录不能再更正")
            self._check_fact_version(state, fact_key, expected_fact_id)
            current = state.facts.get(fact_key)
            if current is None:
                raise ValueError("没有可撤销的更正记录")
            previous = next(
                (
                    fact
                    for fact in reversed(state.history)
                    if fact.key == fact_key and fact.fact_id != current.fact_id
                ),
                None,
            )
            if previous is None:
                raise ValueError("没有可撤销的更正记录")
            turn_id = str(uuid4())
            self._prepare_fact_updates(
                state,
                [
                    {
                        "key": fact_key,
                        "value": previous.value,
                        "assertion": previous.assertion,
                    }
                ],
                turn_id,
            )
            restored = Fact(
                key=fact_key,
                value=deepcopy(previous.value),
                assertion=previous.assertion,
                source=previous.source,
                quality=previous.quality,
                turn_id=turn_id,
                evidence=previous.evidence,
                structured_input=deepcopy(previous.structured_input),
            )
            state.record(restored)
            self.events.append(
                Event(
                    session_id,
                    "fact.restored",
                    {
                        "fact_key": fact_key,
                        "restored_from_fact_id": previous.fact_id,
                        "replaced_fact_id": current.fact_id,
                        "new_fact_id": restored.fact_id,
                    },
                    turn_id=turn_id,
                )
            )
            state.active_question = None
            state.active_task = None
            state.risk = self.triage.assess(state)
            if state.risk.level == RiskLevel.EMERGENCY:
                reply = self._emergency_reply(state, state.risk.triggers)
            elif state.risk.level == RiskLevel.URGENT:
                reply = self._urgent(state)
            else:
                state.closed_reason = None
                reply = self._plan_next(state)
            self._record_reply(state, reply, turn_id, f"用户撤销字段 {fact_key} 的更正")
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
        self._record_reply(state, reply, turn_id, "用户主动请求优先求助")
        self._help_requests[state.session_id] = text
        return reply

    # -- turn pipeline (design phase 2) --------------------------------
    # _submit orchestrates: emergency-help short-circuit, stale-action
    # guard, turn setup, the direct emergency gate, then an ordered
    # handler pipeline. Each handler returns a reply to short-circuit the
    # turn (with ctx.reply_reason set) or None to continue. Handler order
    # reproduces the historical branch order exactly.

    def _submit(self, state: CaseState, user_input: UserInput) -> AgentReply:
        if user_input.choice_value == "request_emergency_help":
            return self._requested_emergency_help(state, user_input.text)
        self._guard_stale_action(state, user_input)
        ctx = self._begin_turn(state, user_input)
        direct = self._direct_emergency(state, ctx)
        if direct is not None:
            return direct
        for name, handler in self._turn_pipeline:
            ctx.trace.append(name)
            reply = handler(state, user_input, ctx)
            if reply is not None:
                self._record_reply(state, reply, ctx.turn_id, ctx.reply_reason)
                return reply
        raise RuntimeError("turn pipeline exhausted without producing a reply")

    def _guard_stale_action(self, state: CaseState, user_input: UserInput) -> None:
        current_action = f"{state.session_id}:{state.revision}"
        if (
            user_input.expected_action_id
            and user_input.expected_action_id != current_action
            and not self.triage.scan_text(self._location_input_text(user_input))
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

    def _begin_turn(self, state: CaseState, user_input: UserInput) -> TurnContext:
        turn_id = str(uuid4())
        text = user_input.text.strip()
        urgent_text = self._location_input_text(user_input)
        if self.triage.scan_text(urgent_text):
            text = urgent_text
        prepared_location = None
        if not self.triage.scan_text(text):
            prepared_location = self._prepare_location_answer(state, user_input)
            if prepared_location:
                text = prepared_location["text"]
            self._check_subject_action(state, user_input)
            self._check_observation_action(state, user_input)
            self._check_location_revision_action(state, user_input)
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {"text": text, "choice_value": user_input.choice_value},
                turn_id=turn_id,
            )
        )
        return TurnContext(
            turn_id=turn_id, text=text, prepared_location=prepared_location
        )

    def _direct_emergency(self, state: CaseState, ctx: TurnContext):
        direct_triggers = self.triage.scan_text(ctx.text)
        if not direct_triggers:
            return None
        state.risk = self.triage.assess(state, ctx.text)
        reply = self._emergency_reply(state, direct_triggers)
        self._record_reply(state, reply, ctx.turn_id, "原始输入命中确定性急症门")
        return reply

    def _h_location_revision(self, state, user_input, ctx):
        revision_reply = self._handle_location_revision_action(
            state, user_input, ctx.text, ctx.turn_id
        )
        if revision_reply is not None:
            ctx.reply_reason = "定位修改与原问诊接续"
        return revision_reply

    def _h_closed_session(self, state, user_input, ctx):
        if state.phase != Phase.CLOSED:
            return None
        reply = (
            self._emergency_reply(state, state.risk.triggers)
            if state.closed_reason == "emergency_route"
            else AgentReply(
                "这次问诊已经结束。如情况有变化，请开始一次新的问诊；出现紧急情况请立即拨打 120。",
                Phase.CLOSED,
                kind="closed",
            )
        )
        ctx.reply_reason = "会话已关闭"
        return reply

    def _h_subject_control(self, state, user_input, ctx):
        subject_reply = self._subject_control(state, user_input, ctx.text, ctx.turn_id)
        if subject_reply is not None:
            ctx.reply_reason = "就诊对象边界与原问诊接续"
        return subject_reply

    def _h_prepared_location(self, state, user_input, ctx):
        if not ctx.prepared_location:
            return None
        revised = self._complete_location_revision(
            state, ctx.prepared_location, ctx.turn_id
        )
        if revised is None:
            self._record_location_answer(state, ctx.prepared_location, ctx.turn_id)
            revised = self._plan_next(state)
        ctx.reply_reason = "结构化定位答案已核对"
        return revised

    def _h_observation_action(self, state, user_input, ctx):
        observation_reply = self._handle_observation_action(
            state, user_input, ctx.text, ctx.turn_id
        )
        if observation_reply is not None:
            ctx.reply_reason = "重复观察及原问诊接续"
        return observation_reply

    def _h_summary_review(self, state, user_input, ctx):
        if state.phase != Phase.SUMMARY:
            return None
        if user_input.choice_value == "confirm_summary":
            state.phase = Phase.CLOSED
            state.closed_reason = "confirmed_by_user"
            state.active_question = None
            reply = AgentReply(
                "你已确认本次摘要，会谈现已结束。可以回看记录；情况变化时请重新评估。",
                Phase.CLOSED,
                kind="closed",
                summary=self._summary_payload(
                    state, review_status="confirmed_by_user"
                ),
            )
        else:
            reply = self._finish(state)
            reply.message = "请点击摘要或右侧记录中的“更正”，修改对应信息后再核对。"
        if user_input.choice_value == "confirm_summary":
            self._schedule_follow_up(state, reply, ctx.turn_id)
        ctx.reply_reason = "用户核对摘要"
        return reply

    def _h_intake(self, state, user_input, ctx):
        text, turn_id = ctx.text, ctx.turn_id
        if state.active_task:
            failure = self._try_task_result(
                state, text, user_input.choice_value, turn_id
            )
            if failure:
                ctx.reply_reason = "图示反馈失败，保持当前任务"
                return failure
        elif state.active_question:
            question = self.content.question(state.active_question)
            result = self.interpreter.interpret(question, text, user_input.choice_value)
            if question.input == "age" and (
                not isinstance(result.value, (int, float))
                or not 0 <= result.value <= 120
            ):
                ctx.reply_reason = "年龄输入未通过校验"
                return AgentReply(
                    "我没能确认年龄。请写成“8个月”或“32岁”这样的格式。",
                    question.phase,
                    kind="clarification",
                    question_id=question.id,
                    developer_trace={"reason": "年龄格式或范围未通过确定性校验，原输入未写入年龄字段"},
                )
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
        return None

    def _h_risk_escalation(self, state, user_input, ctx):
        text, turn_id = ctx.text, ctx.turn_id
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
            ctx.reply_reason = "结构化急症门触发"
            return self._emergency_reply(state, state.risk.triggers)
        if state.risk.level == RiskLevel.URGENT:
            ctx.reply_reason = "风险升级后优先求助"
            return self._urgent(state)
        return None

    def _h_planning(self, state, user_input, ctx):
        reply = self.planner.plan(state)
        ctx.reply_reason = (
            reply.developer_trace.get("reason", "") if reply.developer_trace else ""
        )
        return reply

    def _record_fact(
        self,
        state: CaseState,
        key: str,
        result: Interpretation,
        turn_id: str,
        evidence: Optional[str] = None,
        structured_input: Optional[Dict[str, Any]] = None,
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
            structured_input=deepcopy(structured_input),
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
        structured_input=None,
    ) -> None:
        task_result = decoded or self.self_checks.interpret(task_id, text, choice_value)
        result = task_result.interpretation
        from .measurement_reports import temperature_report

        report = (
            temperature_report(text)
            if self.content.tasks[task_id]["input_kind"] == "temperature"
            and choice_value not in {"skip", "unable", "not_available"}
            else None
        )
        resume = state.observation_resume
        previous = state.facts.get(task_result.fact_key)
        preserve = bool(
            resume
            and previous
            and previous.assertion == Assertion.PRESENT
            and result.assertion != Assertion.PRESENT
        )
        if not preserve:
            self._record_fact(
                state,
                task_result.fact_key,
                result,
                turn_id,
                report.evidence if report and report.has_supplement else text,
                structured_input,
            )
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
        # Completion is host-owned context even when the report has no extra
        # narrative. It is retained for a failed decision retry, not a new fact.
        state.observation_feedback = {
            "task_id": task_id,
            "text": text,
            "source_turn_id": turn_id,
            "assertion": result.assertion.value,
            "quality": result.quality.value,
            "forwarded": False,
            "has_supplement": bool(report and report.has_supplement),
            "completion": {
                "task_id": task_id,
                "result_key": task_result.fact_key,
                "fact_id": state.facts[task_result.fact_key].fact_id
                if not preserve
                else None,
                "source_turn_id": turn_id,
                "assertion": result.assertion.value,
                "quality": result.quality.value,
                "previous_reading_preserved": preserve,
            },
        }
        state.active_task = None
        self._clear_task_conversation(state, "result_recorded", turn_id)
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
            and self.content.question_applies_in_state(question, state)
            and question_fact_gates_pass(question, state.facts)
        ]
        # Parity with the adaptive engine: non-blocking questions (sex,
        # reported_temperature) stay askable while any blocking field is
        # outstanding, but they never gate the summary/close on their own.
        if any(question.blocking for question in candidates):
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
        return planning_rank(phase)

    def _offer_task(
        self, state: CaseState, chief: str, task_id: Optional[str] = None
    ) -> Optional[AgentReply]:
        if state.self_check_conversation:
            if (
                task_id is None
                or task_id == state.self_check_conversation["task"]["id"]
            ):
                return self._resume_task_conversation(state)
            return None
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
            if request.get("repeat"):
                return self._request_repeat(
                    state, request["task_id"], request["evidence"], request["turn_id"]
                )
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
                if (
                    state.self_check_conversation
                    and state.self_check_conversation["task"]["id"] == identity
                ):
                    self._clear_task_conversation(state, "user_declined", turn_id)
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

    @staticmethod
    def _subject_is_child(state: CaseState) -> bool:
        # Mirrors AdaptiveConsultationEngine._subject_is_child so both
        # engines adapt the age prompt for the same subjects.
        if state.get("consult_for") == "child":
            return True
        profile = subject_profile(state)
        reference = (profile or {}).get("reference") or {}
        attributes = reference.get("attributes") or []
        return bool(attributes) and attributes[0] == "child"

    def _ask(self, state: CaseState, question: Question, reason: str) -> AgentReply:
        state.phase = question.phase
        state.active_question = question.id
        state.active_question_is_clarification = False
        state.active_task = None
        if question.id not in state.asked_questions:
            state.asked_questions.append(question.id)
        prompt = question.prompt
        if question.fact_key == "age_years" and not self._subject_is_child(state):
            # Same adaptation as the adaptive engine: the catalog's
            # infant-months wording is only presented for child subjects.
            prompt = ADULT_AGE_PROMPT
        return AgentReply(
            prompt,
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
        state.subject_resume = state.subject_candidate = None
        self._clear_calculation(state, "emergency")
        self._clear_observation(state, "emergency")
        self._clear_task_conversation(state, "emergency")
        state.phase = Phase.CLOSED
        state.closed_reason = "emergency_route"
        state.active_question = None
        state.active_question_is_clarification = False
        state.active_task = None
        if requested_help:
            message = (
                "你已选择优先求助。本次问诊已停止。"
                "请现在拨打 120，或让身边的人协助呼叫急救；不要自己驾车。不要继续等待本次对话。"
            )
        else:
            # emergency_explanation already names the signals, gives the
            # 120 instruction and adds waiting guidance (stay still, do
            # not drive yourself, have someone accompany you); keep the
            # standing "do not keep waiting for this chat" as the tail
            # instead of stacking a second full instruction sentence.
            from .conclusion import emergency_explanation

            message = emergency_explanation(triggers) + "不要继续等待本次对话。"
        return AgentReply(
            message,
            Phase.CLOSED,
            kind="emergency",
            summary={"care_route": "立即急救", "triggers": sorted(set(triggers))},
            developer_trace={"reason": "确定性急症规则优先于后续问诊"},
        )

    def _urgent(self, state: CaseState) -> AgentReply:
        state.subject_resume = state.subject_candidate = None
        self._clear_calculation(state, "urgent")
        self._clear_observation(state, "urgent")
        self._clear_task_conversation(state, "urgent")
        reply = self._finish(state, risk_override=deepcopy(state.risk))
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
        if fact.key == "symptom_location":
            detail["fact_id"] = fact.fact_id
        if fact.structured_input is not None:
            detail["structured_input"] = deepcopy(fact.structured_input)
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

    def _summary_payload(
        self, state: CaseState, *, review_status: str
    ) -> Dict[str, Any]:
        """Build the patient-facing record with an explicit review outcome.

        A closed session is not necessarily a user-confirmed session: urgent
        routing closes automatically.  Keeping that distinction in the data
        lets every renderer show the same truthful status.
        """
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
                if key == "consult_for" and subject_profile(state):
                    display = subject_profile(state)["label"]
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
        return {
            "content_versions": self.content.versions,
            "known": known,
            "unknown": unknown,
            "unknown_details": unknown_details,
            # P2c: the deterministic engine has no _context to inject
            # into, so the archive reference rides the summary payload
            # as a read-only reference section. It never drives question
            # selection, skipping, or prefilling (facts are untouched).
            "profile_baseline": (
                dict(state.profile_baseline)
                if state.baseline_reference_enabled
                else {}
            ),
            "care_route": route,
            "risk_level": state.risk.level.value,
            "risk_triggers": state.risk.triggers,
            "review_status": review_status,
            "return_now_if": ["意识异常或抽搐", "严重呼吸困难或嘴唇发紫", "持续压榨样胸痛", "大量或无法控制的出血"],
            "observations": [
                self._observation_series(state, task)
                for task in self.content.tasks.values()
                if task.get("repeatable") and task["result_key"] in state.facts
            ],
        }

    def _finish(
        self,
        state: CaseState,
        *,
        risk_override: Optional[RiskAssessment] = None,
    ) -> AgentReply:
        state.phase = Phase.SUMMARY
        state.risk = risk_override or self.triage.assess(state)
        needs_confirmation = state.risk.level not in {
            RiskLevel.EMERGENCY,
            RiskLevel.URGENT,
        }
        state.phase = Phase.SUMMARY if needs_confirmation else Phase.CLOSED
        state.closed_reason = None if needs_confirmation else "urgent_route"
        state.active_question = f"summary:{uuid4()}" if needs_confirmation else None
        state.active_question_is_clarification = False
        state.active_task = None
        summary = self._summary_payload(
            state,
            review_status=(
                "pending_review" if needs_confirmation else "closed_unconfirmed"
            ),
        )
        reply = AgentReply(
            (
                "问诊信息已整理完成。请先核对摘要；其中“未知”不代表没有，测量结果也保留了来源和质量。"
                if needs_confirmation
                else "本次信息已整理。系统已提示尽快线下评估；当前记录未经你核对确认。"
            ),
            state.phase,
            kind="summary",
            action_id=state.active_question,
            quick_replies=[
                QuickReply("确认摘要并结束本次问诊", "confirm_summary"),
                QuickReply("我需要更正", "correct_summary"),
            ]
            if needs_confirmation
            else [],
            summary=summary,
            developer_trace={"reason": "必要通用字段与适用的定向字段均已处理"},
        )
        if state.closed_reason == "urgent_route":
            self._schedule_follow_up(state, reply)
        return reply

    def _schedule_follow_up(self, state, reply=None, turn_id=None):
        """Design phase 4: derive, persist and announce a follow-up plan."""
        if state.follow_up:
            # Idempotent: the urgent path schedules at summary time and
            # the confirm path calls again — rebuilding would drift the
            # due clock and wipe an outcome recorded in between.
            if reply is not None:
                reply.follow_up = dict(state.follow_up)
            return state.follow_up
        plan = build_follow_up_plan(state)
        if plan is None:
            return None
        state.follow_up = plan
        self.events.append(
            Event(state.session_id, "followup.scheduled", dict(plan), turn_id=turn_id)
        )
        if reply is not None:
            reply.follow_up = dict(plan)
        return plan

    def record_follow_up_outcome(
        self, session_id: str, outcome: str, note: str = ""
    ) -> Dict[str, Any]:
        """Record a follow-up outcome; worsening reopens triage as a new
        session linked to this one (design phase 4 longitudinal loop)."""
        state = self.state(session_id)
        with self._lock_for(session_id):
            plan = state.follow_up
            if not plan:
                raise ValueError("该会话没有随访计划，无法记录结局")
            apply_outcome(plan, outcome, note)
            self.events.append(
                Event(
                    session_id,
                    "followup.outcome_recorded",
                    {"outcome": outcome, "note": note},
                )
            )
            result: Dict[str, Any] = {
                "outcome": outcome,
                "requires_retriage": outcome in RETRIAGE_OUTCOMES,
                "new_session_id": None,
            }
            if outcome in RETRIAGE_OUTCOMES:
                new_state, _opening = self.create_session()
                new_state.runtime_snapshot["follow_up_of"] = session_id
                # Longitudinal prior: the re-triage session carries a
                # compact snapshot of the case it grew out of, so the
                # patient, the banner and the clinician handoff all see
                # what was concluded last time and what changed since.
                prior_disposition = (state.conclusion or {}).get("disposition") or {}
                new_state.runtime_snapshot["prior_case"] = {
                    "session_id": session_id,
                    "chief_complaint": str(state.get("chief_complaint", "")),
                    "closed_reason": state.closed_reason,
                    "disposition_level": prior_disposition.get("level"),
                    "disposition_text": prior_disposition.get("text"),
                    "outcome": outcome,
                    "outcome_note": note,
                    "outcome_recorded_at": plan.get("outcome_recorded_at"),
                }
                plan["retriage_session_id"] = new_state.session_id
                result["new_session_id"] = new_state.session_id
                self.events.append(
                    Event(
                        session_id,
                        "followup.retriage_session_created",
                        {"new_session_id": new_state.session_id, "outcome": outcome},
                    )
                )
            # The outcome mutated this (possibly restored) closed case;
            # write it back so the recorded outcome itself is durable.
            self._persist(state)
            return result

    def _apply_bridging(self, state: CaseState, reply: AgentReply) -> None:
        """Prefix a host-side deterministic bridging sentence to questions.

        Single insertion point for both engines: runs before the publication
        gate so the gate sees the final patient-visible text, and a blocked
        reply's fallback replaces the whole message (bridging included).
        The developer_trace marker prevents a second pass over the same
        reply object from stacking a second sentence.
        """
        if reply.kind != "question":
            return
        trace = dict(reply.developer_trace or {})
        if isinstance(trace.get("bridging"), dict) and trace["bridging"].get("applied"):
            return
        fact_key = None
        if reply.question_id:
            try:
                fact_key = self.content.question(reply.question_id).fact_key
            except Exception:
                fact_key = None
        prior = [
            turn.get("content", "")
            for turn in state.transcript
            if turn.get("role") == "assistant"
        ]
        sentence, kind = build_bridging_sentence(
            state.facts, fact_key, reply.message, prior, len(prior)
        )
        if sentence is None:
            trace["bridging"] = {"applied": False, "reason": kind}
        else:
            reply.message = sentence + reply.message
            trace["bridging"] = {"applied": True, "kind": kind}
        reply.developer_trace = trace

    def _record_reply(
        self, state: CaseState, reply: AgentReply, turn_id: str, reason: str
    ) -> None:
        self._apply_bridging(state, reply)
        decision = self.publication_gate.validate(state, reply)
        if not decision.allowed:
            if state.risk.level == RiskLevel.EMERGENCY:
                state.phase = Phase.CLOSED
                state.closed_reason = "emergency_route"
                state.active_question = None
                state.active_question_is_clarification = False
                state.active_task = None
            fallback = self.publication_gate.fallback(state, decision)
            for field_name in AgentReply.__dataclass_fields__:
                setattr(reply, field_name, getattr(fallback, field_name))
        trace = dict(reply.developer_trace or {})
        trace["publication"] = {
            "status": "allowed" if decision.allowed else "blocked",
            "rule_ids": list(decision.rule_ids),
        }
        reply.developer_trace = trace
        self.events.append(
            Event(
                state.session_id,
                "reply.publication_allowed" if decision.allowed else "reply.publication_blocked",
                {"rule_ids": list(decision.rule_ids)},
                turn_id=turn_id,
            )
        )
        transition_id = commit_phase(self.events, state, reason, turn_id)
        if transition_id:
            trace["transition_id"] = transition_id
            reply.developer_trace = trace
            if reply.phase == Phase.CLOSED:
                # Compose the sheet only on the turn that actually closes
                # the session (same criterion as the spoken close line
                # below). Guard replies on an already-closed case also
                # carry phase CLOSED; re-composing for them bumped the
                # sheet revision on every message and silently overwrote
                # post-close enrichment such as further_reading.
                if decision.allowed and state.closed_reason in (
                    "confirmed_by_user",
                    "urgent_route",
                ):
                    self._attach_conclusion(state, reply, turn_id)
                # Only the reply that actually closes the session in this
                # turn speaks the disposition; the already-closed guard
                # reply (no transition) must not repeat it every turn.
                self._speak_close_line(state, reply)
        state.subject_candidate = None
        if reply.kind != "subject_change":
            if reply.kind != "service_error":
                state.subject_resume = None
            self._project_calculation_followup(state, reply)
            self._project_observation_feedback(state, reply)
        with self._session_conditions[state.session_id]:
            state.revision += 1
            reply.action_id = f"{state.session_id}:{state.revision}"
            reply.location_revision_action = self._location_revision_entry(state)
            self._remember_location_revision_question(state, reply)
            if reply.task:
                from .task_interactions import task_with_interaction

                reply.task = task_with_interaction(
                    reply.task,
                    state.session_id,
                    reply.action_id,
                    self.events.for_session(state.session_id),
                )
            self.events.append(
                Event(
                    state.session_id,
                    "turn.agent_reply",
                    {"reply": reply.to_dict(), "reason": reason},
                    turn_id=turn_id,
                )
            )
            state.transcript.append({"role": "assistant", "content": reply.message})
            self._latest_replies[state.session_id] = deepcopy(reply)
            self._session_conditions[state.session_id].notify_all()
        self._persist(state)

    def _attach_conclusion(self, state: CaseState, reply: AgentReply, turn_id: str) -> None:
        """Compose the conclusion sheet at close (design: 结论单 §5).

        Single choke point: _record_reply serves both engines and runs
        before the turn.agent_reply event serializes the reply, so the
        stored snapshot used for replay carries the sheet (invariant I5).
        Fail-open (I3): any composer error leaves the close untouched.
        """
        try:
            from .conclusion import compose_conclusion, sheet_mode

            mode = sheet_mode()
            if mode == "off":
                return
            allergies = None
            if state.baseline_reference_enabled and isinstance(
                state.profile_baseline, dict
            ):
                allergies = state.profile_baseline.get("allergies")
            sheet = compose_conclusion(
                state, summary=reply.summary, baseline_allergies=allergies
            )
            if sheet is None:
                return
            state.conclusion = sheet
            self.events.append(
                Event(
                    state.session_id,
                    "conclusion.generated",
                    {
                        "tier": sheet["tier"],
                        "revision": sheet["revision"],
                        "directions_abstained": sheet["directions_abstained"],
                        "directions_visible": sheet["directions_visible"],
                        "content_versions": sheet["content_versions"],
                    },
                    turn_id=turn_id,
                )
            )
            if mode == "on":
                reply.conclusion = deepcopy(sheet)
        except Exception as exc:
            # Fail-open for the patient, but never silent: a missing
            # conclusion sheet must be attributable from the event log.
            try:
                self.events.append(
                    Event(
                        state.session_id,
                        "conclusion.failed",
                        {"error": type(exc).__name__},
                        turn_id=turn_id,
                    )
                )
            except Exception:
                pass
            return

    def _speak_close_line(self, state: CaseState, reply: AgentReply) -> None:
        """Say the disposition out loud on a user-confirmed close.

        The conclusion sheet renders as a card, but the closing chat
        message itself used to be pure boilerplate, so a patient reading
        only the chat stream never heard the disposition. The sheet only
        exists after _attach_conclusion runs inside _record_reply, so the
        line is attached at that choke point rather than in the close
        handlers (both engines share it). Fail-open: no attached sheet
        (sheet mode off/shadow, or a composer failure) leaves the
        boilerplate untouched.
        """
        if (
            reply.kind != "closed"
            or state.closed_reason != "confirmed_by_user"
            or reply.conclusion is None
        ):
            return
        try:
            from .conclusion import spoken_close_line

            line = spoken_close_line(reply.conclusion)
        except Exception:
            return
        if line:
            reply.message = line + reply.message

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
