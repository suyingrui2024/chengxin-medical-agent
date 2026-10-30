"""Mixin: subject for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEngineSubject:
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
            # An explicit correction replaces the fact: merging here
            # would resurrect the very denial the patient corrected away.
            self._record_fact(state, fact_key, result, turn_id, new_text, merge=False)
            state.active_question = None
            state.active_task = None
            state.risk = self.triage.assess(state, new_text)
            if state.risk.level == RiskLevel.EMERGENCY:
                action = self._emergency_clarification_action(state, new_text, ())
                if action == "clarify":
                    reply = self._emergency_clarification_reply(
                        state, new_text, turn_id
                    )
                elif action == "confirmed":
                    reply = self._emergency_reply(
                        state, state.risk.triggers, include_clarification=True
                    )
                else:
                    reply = self._emergency_reply(state, state.risk.triggers)
            elif state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    reply = gated
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
                # A restoration replays the emergency route, so it must
                # run the same clarification gate as a fresh turn: a
                # bare co-occurrence signal is clarified, not closed on,
                # unless the route was already issued (an issued route
                # is never retracted by editing a field) or the patient
                # already confirmed it (then the confirmed close replays
                # with its signal intact, never filtered to empty).
                texts = [
                    event.payload.get("text")
                    for event in self.events.for_session(session_id)
                    if event.event_type == "turn.user_message"
                    and isinstance(event.payload.get("text"), str)
                    and event.payload.get("text")
                ]
                if not texts:
                    texts = [
                        item["content"]
                        for item in state.transcript
                        if item.get("role") == "user" and item.get("content")
                    ]
                signal_text = texts[-1] if texts else (restored.evidence or "")
                action = self._emergency_clarification_action(
                    state, signal_text, ()
                )
                if action == "confirmed":
                    reply = self._confirm_emergency_clarification(
                        state, turn_id
                    ) or self._emergency_reply_with_clarification_memory(
                        state, state.risk.triggers
                    )
                elif (
                    action == "clarify"
                    and state.closed_reason != "emergency_route"
                ):
                    reply = self._emergency_clarification_reply(
                        state, signal_text, turn_id
                    )
                else:
                    reply = self._emergency_reply_with_clarification_memory(
                        state, state.risk.triggers
                    )
            elif state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    reply = gated
                else:
                    state.closed_reason = None
                    reply = self._plan_next(state)
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
        action = self._emergency_clarification_action(state, text, ())
        if action == "clarify":
            reply = self._emergency_clarification_reply(state, text, turn_id)
            state.pending_input = None
            self._record_reply(state, reply, turn_id, "更正原文仅为裸共现，先鉴别澄清")
            return reply
        if action == "confirmed":
            reply = self._emergency_reply(
                state, state.risk.triggers, include_clarification=True
            )
        else:
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

    def _record_fact(
        self,
        state: CaseState,
        key: str,
        result: Interpretation,
        turn_id: str,
        evidence: Optional[str] = None,
        structured_input: Optional[Dict[str, Any]] = None,
        merge: bool = True,
    ) -> None:
        source = result.source
        if source == Source.PATIENT and state.get("consult_for") in {
            "child",
            "other_adult",
        }:
            source = Source.CAREGIVER
        value = result.value
        preserved: List[str] = []
        if merge:
            previous = state.facts.get(key)
            if previous is not None and isinstance(value, str):
                previous_text = (
                    previous.value
                    if isinstance(previous.value, str)
                    else previous.evidence
                )
                merged = merge_preserving_denials(previous_text, value)
                if merged != value:
                    preserved = _preserved_segments(previous_text, value)
                    value = merged
        fact = Fact(
            key=key,
            value=value,
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
        if preserved:
            self.events.append(
                Event(
                    state.session_id,
                    "fact.writeback_merged",
                    {"fact_key": key, "preserved": preserved},
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
