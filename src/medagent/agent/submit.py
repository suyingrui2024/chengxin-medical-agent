"""Mixin: submit for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEngineSubmit:
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
                    denied_labels = pending.get("labels")
                    state.runtime_snapshot[
                        "emergency_clarification_denied"
                    ] = {
                        "text": denied_text,
                        "for_input": text,
                        "labels": list(denied_labels)
                        if isinstance(denied_labels, list)
                        else [],
                    }
                    # C-10: accumulate denied label sets (the dict
                    # above keeps only the latest denial). Later
                    # paraphrases are checked against every denied
                    # group, not just the most recent one.
                    history = state.runtime_snapshot.get(
                        "emergency_clarification_denied_labels"
                    )
                    if not isinstance(history, list):
                        history = []
                        state.runtime_snapshot[
                            "emergency_clarification_denied_labels"
                        ] = history
                    labels = sorted(
                        set(denied_labels)
                        if isinstance(denied_labels, list)
                        else set()
                    )
                    if labels not in history:
                        history.append(labels)
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
            if (
                action == "clarify"
                and self._emergency_clarification_denied_before(state, text)
            ):
                # C-10: this danger-signal group was already denied
                # once; do not re-ask the whole clarification round on
                # a paraphrase. Fall through to normal consultation
                # (never to the emergency reply below: the signal is
                # the same not-yet-confirmed one). Risk must be stood
                # down exactly as the clarification reply does it, or
                # the publication gate would block the normal question
                # and close the case as an emergency instead.
                state.risk = self._deferred_risk(state)
            elif action == "clarify":
                return self._deliver(
                    state,
                    self._emergency_clarification_reply(state, text, turn_id),
                    turn_id,
                )
            elif action == "confirmed":
                return self._deliver(
                    state,
                    self._emergency_reply(
                        state, risk.triggers, include_clarification=True
                    ),
                    turn_id,
                )
            else:
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
                if (
                    action == "clarify"
                    and self._emergency_clarification_denied_before(state, text)
                ):
                    # C-10: same group already denied; suppress the
                    # repeat clarification and continue normal flow
                    # (risk stood down as in the clarification reply,
                    # so the next question stays deliverable).
                    state.risk = self._deferred_risk(state)
                elif action == "clarify":
                    return self._deliver(
                        state,
                        self._emergency_clarification_reply(state, text, turn_id),
                        turn_id,
                    )
                elif action == "confirmed":
                    return self._deliver(
                        state,
                        self._emergency_reply(
                            state, state.risk.triggers, include_clarification=True
                        ),
                        turn_id,
                    )
                else:
                    return self._deliver(
                        state,
                        self._emergency_reply(state, state.risk.triggers),
                        turn_id,
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
            from ..task_conversation import is_observation_entry

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
                if (
                    action == "clarify"
                    and self._emergency_clarification_denied_before(state, new_text)
                ):
                    # C-10: same group already denied; suppress the
                    # repeat clarification and continue the normal
                    # correction flow (risk stood down as in the
                    # clarification reply).
                    state.risk = self._deferred_risk(state)
                elif action == "clarify":
                    return self._deliver(
                        state,
                        self._emergency_clarification_reply(state, new_text, turn_id),
                        turn_id,
                    )
                elif action == "confirmed":
                    return self._deliver(
                        state,
                        self._emergency_reply(
                            state, state.risk.triggers, include_clarification=True
                        ),
                        turn_id,
                    )
                else:
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
