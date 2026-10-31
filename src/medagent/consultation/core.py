"""Mixin: core for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEngineCore:
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
            from ..reasoning import DifferentialPlanner

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
