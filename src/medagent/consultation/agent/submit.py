"""Mixin: submit for AdaptiveConsultationEngine."""
from typing import NamedTuple, Optional
from uuid import uuid4
from ...core.domain import RiskAssessment
from ...core.domain import (
    AgentReply,
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskLevel,
    Source,
)
from ...core.events import Event
from ...infra.agent_tools import validate_proposals
from ...shared_types import StaleActionError, UserInput
from ..vocabulary import is_summary_confirmation_text


class _PreparedTurn(NamedTuple):
    """What this turn says, before any of it has been written down.

    ``text`` is the finalized turn text: the retry text, the adaptive answer and
    the location input each get a chance to replace it, in that order.  The two
    dicts are the answers those replacements came from.  They travel with the
    text rather than being recomputed because the events written below record
    the selected option and the structured input, and the risk assessment reads
    the answer's ``risk_tags``.
    """

    text: str
    adaptive: Optional[dict]
    location: Optional[dict]


class AdaptiveConsultationEngineSubmit:
    def _submit(self, state: CaseState, user_input: UserInput) -> AgentReply:
        if user_input.choice_value == "request_emergency_help":
            return self._requested_emergency_help(state, user_input.text)

        prepared = self._prepare_turn(state, user_input)
        turn_id = self._record_user_turn(state, prepared)
        risk = self._assess_turn_risk(state, prepared)
        reply = self._emergency_turn(state, prepared, user_input, turn_id, risk)
        if reply is not None:
            return reply
        reply = self._control_turn(state, prepared, user_input, turn_id)
        if reply is not None:
            return reply
        reply = self._pending_work_turn(state, prepared, user_input, turn_id)
        if reply is not None:
            return reply
        return self._answer_turn(state, prepared, user_input, turn_id)

    def _prepare_turn(
        self, state: CaseState, user_input: UserInput
    ) -> _PreparedTurn:
        """Decide what this turn says, and run the five control-action checks.

        The stale-action guard comes first: an answer to a question the page has
        already replaced must not be read against the new one.  The text then
        passes through three replacements -- retry text, adaptive answer,
        location input -- and each is allowed to win over the previous one, in
        that order.

        The five ``_check_*_action`` calls write to ``state``: they are checks
        that record a decision, not questions that return an answer.  They must
        keep running here, before the turn is written down, and in this order,
        which is the order they have always run in.
        """
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
        return _PreparedTurn(text, adaptive_answer, prepared_location)

    def _record_user_turn(self, state: CaseState, prepared: _PreparedTurn) -> str:
        """Write the turn down: the event, the transcript, then the worry.

        The event goes first because every later event of this turn carries the
        same ``turn_id``, and the analysis that reads them back expects the
        user's message to be the first of the group.
        """
        turn_id = str(uuid4())
        self.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {
                    "text": prepared.text,
                    **(
                        {
                            "adaptive_answer": {
                                "selected": prepared.adaptive["selected"],
                                "kind": prepared.adaptive["kind"],
                                "risk_tags": prepared.adaptive["risk_tags"],
                            }
                        }
                        if prepared.adaptive
                        else {}
                    ),
                    **(
                        {
                            "structured_input": prepared.location[
                                "structured_input"
                            ]
                        }
                        if prepared.location
                        else {}
                    ),
                },
                turn_id=turn_id,
            )
        )
        state.transcript.append({"role": "user", "content": prepared.text})
        # Batch B worry capture: the adaptive engine does not share the
        # deterministic engine's _begin_turn, so its own _submit calls
        # the same shared helper here, on the finalized turn text, with
        # the same first-wins / never-patient_goal semantics.
        self._capture_worry(state, prepared.text, turn_id)
        return turn_id

    def _assess_turn_risk(
        self, state: CaseState, prepared: _PreparedTurn
    ) -> RiskAssessment:
        """Triage the turn, before any model call.

        A case already routed to emergency keeps the assessment that routed it:
        ``state.risk`` is the record of why it closed, so a later turn's text
        does not overwrite it.  The assessment is stored rather than only
        returned, because every stage below reads ``state.risk``.
        """
        risk = self.triage.assess(
            state,
            prepared.text,
            prepared.adaptive["risk_tags"] if prepared.adaptive else (),
        )
        if state.closed_reason == "emergency_route":
            risk = state.risk
        state.risk = risk
        return risk

    def _emergency_turn(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
        risk: RiskAssessment,
    ) -> Optional[AgentReply]:
        """Handle the emergency clarification, then dispatch this turn's own.

        Two different events meet here.  The first is the patient answering a
        clarification this engine asked: a confirmation re-checks the same
        signal, a denial is recorded so a later paraphrase of it is not asked
        again.  The second is an emergency produced by this turn's text.  They
        share this function because they share the risk assessment, and because
        the denial has to be written down before the dispatch decides whether to
        ask anything.

        Returns a reply to deliver, or ``None`` to carry on with the normal flow.
        """
        if user_input.choice_value == "confirm_emergency_clarification":
            confirmed = self._confirm_emergency_clarification(state, turn_id)
            if confirmed is not None:
                return self._deliver(state, confirmed, turn_id)
        if user_input.choice_value == "deny_emergency_clarification":
            self._apply_emergency_denial(state, prepared.text, turn_id)
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
            return self._dispatch_emergency_clarification(
                state,
                prepared.text,
                prepared.adaptive["risk_tags"] if prepared.adaptive else (),
                risk.triggers,
                turn_id,
            )
        return None

    def _dispatch_emergency_clarification(
        self,
        state: CaseState,
        text: str,
        risk_tags,
        triggers,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """Ask, confirm or answer, based on what this turn's danger signal is.

        ``None`` means the signal was already denied once: risk is stood down
        and the caller carries on with the normal flow rather than re-asking a
        question the patient has answered.  Everything else is a reply.

        ``triggers`` is passed in rather than read from ``state.risk`` because
        the stand-down above rewrites ``state.risk``, and the emergency reply
        must still quote the signals that were actually found.
        """
        action = self._emergency_clarification_action(state, text, risk_tags)
        if action == "clarify" and self._emergency_clarification_denied_before(
            state, text
        ):
            # C-10: this danger-signal group was already denied once; do not
            # re-ask the whole clarification round on a paraphrase. Fall through
            # to normal consultation (never to the emergency reply below: the
            # signal is the same not-yet-confirmed one). Risk must be stood down
            # exactly as the clarification reply does it, or the publication gate
            # would block the normal question and close the case as an emergency
            # instead.
            state.risk = self._deferred_risk(state)
            return None
        if action == "clarify":
            return self._deliver(
                state,
                self._emergency_clarification_reply(state, text, turn_id),
                turn_id,
            )
        if action == "confirmed":
            return self._deliver(
                state,
                self._emergency_reply(state, triggers, include_clarification=True),
                turn_id,
            )
        return self._deliver(
            state, self._emergency_reply(state, triggers), turn_id
        )

    def _apply_emergency_denial(
        self, state: CaseState, text: str, turn_id: str
    ) -> None:
        """Record that the patient denied a clarification, and which signals.

        P5 (C-07/C-08): the denial negates the danger signals, not the
        chief-complaint statement itself.  Remember the clarified turn's text,
        scoped to this denial turn's input, so the model round that follows may
        quote it for chief facts that never landed (see ``_evidence_sources`` in
        agent_tools).  The scope key (``for_input``) keeps the exception inert on
        every later turn: the current-turn evidence rule is otherwise untouched.
        """
        pending = self._emergency_clarification_pending(state)
        if pending is None:
            return
        denied_text = pending.get("text")
        if isinstance(denied_text, str) and denied_text.strip():
            denied_labels = pending.get("labels")
            state.runtime_snapshot["emergency_clarification_denied"] = {
                "text": denied_text,
                "for_input": text,
                "labels": list(denied_labels)
                if isinstance(denied_labels, list)
                else [],
            }
            # C-10: accumulate denied label sets (the dict above keeps only the
            # latest denial). Later paraphrases are checked against every denied
            # group, not just the most recent one.
            history = state.runtime_snapshot.get(
                "emergency_clarification_denied_labels"
            )
            if not isinstance(history, list):
                history = []
                state.runtime_snapshot["emergency_clarification_denied_labels"] = (
                    history
                )
            labels = sorted(
                set(denied_labels) if isinstance(denied_labels, list) else set()
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

    def _control_turn(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """The controls that can take over a turn before any answer is composed.

        Ordered from "the case itself has moved" to "this control was pressed":
        a revision, a closed case, a suspended emergency, a subject change, a
        finished location answer, a task conversation.  The first that applies
        wins, so the order is load-bearing rather than alphabetical.

        The urgent gate here covers pending work only.  It cannot cover a fresh
        answer: P5 ④-4 removed that, because the closing round's answer is not
        recorded yet, so the gate would close on "asked" minimum facts while the
        answer was still in flight.  ``_run`` evaluates it after ``record_facts``.
        """
        revision_reply = self._handle_location_revision_action(
            state, user_input, prepared.text, turn_id
        )
        if revision_reply is not None:
            return self._deliver(state, revision_reply, turn_id)
        if state.phase == Phase.CLOSED:
            return self._deliver(
                state,
                AgentReply(
                    "这次会谈已结束。新问题可重新开始问诊。",
                    Phase.CLOSED,
                    kind="closed",
                ),
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
        subject_reply = self._subject_control(
            state, user_input, prepared.text, turn_id
        )
        if subject_reply is not None:
            return self._deliver(state, subject_reply, turn_id)
        if prepared.location:
            return self._finish_location_answer(state, prepared, turn_id)
        task_action = self._handle_task_conversation_action(
            state, user_input, turn_id
        )
        if isinstance(task_action, AgentReply):
            return self._deliver(state, task_action, turn_id)
        if task_action is not None:
            return self._run(state, task_action["text"], task_action["source_turn_id"])
        return None

    def _finish_location_answer(
        self, state: CaseState, prepared: _PreparedTurn, turn_id: str
    ) -> AgentReply:
        """Complete a structured location answer and run the model on it.

        A supplement attached to the answer is folded in first: the revision
        goes down, then the feedback text is what the model reads, so the
        patient's extra detail is not lost by recording only the map position.
        """
        revised = self._complete_location_revision(
            state, prepared.location, turn_id
        )
        if revised is not None:
            if (
                state.observation_feedback
                and state.observation_feedback["has_supplement"]
            ):
                return self._run(state, prepared.text, turn_id)
            return self._deliver(state, revised, turn_id)
        self._record_location_answer(state, prepared.location, turn_id)
        return self._run(state, prepared.text, turn_id)

    def _pending_work_turn(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """Resume work this turn is answering, before composing anything new.

        Three ways a turn can be a reply to something rather than a fresh
        statement: a calculation supplement, a retry of a calculation still
        processing, a retry of an observation that needs its text back.  The two
        feedback flags are then cleared, because a turn that did not act on them
        has moved on, and the summary confirmation is checked only once they are
        clear -- a pending supplement outranks the confirmation.

        Returns ``None`` when none of this applies.
        """
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
        reply = self._summary_confirmation_reply(
            state, prepared, user_input, turn_id
        )
        if reply is not None:
            return reply
        reply = self._observation_turn(state, prepared, user_input, turn_id)
        if reply is not None:
            return reply
        calculation_reply = self._handle_calculation_action(
            state, user_input, prepared.text, turn_id
        )
        if (
            user_input.choice_value in {"skip_calculation", "resume_after_calculation"}
            and state.calculation_feedback
        ):
            return self._process_calculation_followup(state)
        if calculation_reply is not None:
            return self._deliver(state, calculation_reply, turn_id)
        return None

    def _summary_confirmation_reply(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """Enter the conclusion flow when the patient confirms the summary.

        D-03/D-04: a confirmed summary must enter the conclusion flow and must
        not be re-suspended by a self-check step, which is why this runs before
        observation handling.  Pending feedback means the confirmation is not
        yet the whole turn -- the patient added something -- so this returns
        ``None`` and the fallback branch later in ``_submit`` picks it up once
        the feedback has been dealt with.
        """
        confirmed = (
            state.phase == Phase.SUMMARY
            and (
                user_input.choice_value == "confirm_summary"
                or is_summary_confirmation_text(prepared.text)
            )
            and not state.calculation_feedback
            and not state.observation_feedback
        )
        if not confirmed:
            return None
        return self._deliver(
            state, self._confirmed_summary_reply(state, turn_id), turn_id
        )

    def _observation_turn(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """Answer an observation, folding in whatever was added to it.

        A supplement defers the reply so the model reads the added text instead
        of only what was selected.  It cannot defer an urgent, emergency or
        closing reply: those are delivered as decided, and the supplement waits
        for the next turn.

        Returns ``None`` when this turn is not an observation answer.
        """
        observation_reply = self._handle_observation_action(
            state, user_input, prepared.text, turn_id
        )
        if observation_reply is None:
            return None
        if (
            state.observation_feedback
            and state.observation_feedback.get("has_supplement", True)
            and observation_reply.kind not in {"urgent", "emergency", "closed"}
        ):
            feedback = state.observation_feedback
            self._clear_calculation(state, "supplemental_patient_information", turn_id)
            state.pending_input = feedback["text"]
            return self._run(state, feedback["text"], feedback["source_turn_id"])
        return self._deliver(state, observation_reply, turn_id)

    def _answer_turn(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> AgentReply:
        """The ordinary turn: read the answer, then let the model plan.

        Everything above this point has either ended the turn or found nothing
        to act on.  What is left is a turn that carries information, so this is
        where the fixed emergency-screen option is written down, an already
        confirmed summary is honoured, and an open task is either answered or
        suspended -- and then the model plans from whatever landed.

        Always returns a reply: this is the floor of ``_submit``, so there is no
        ``None`` for a caller above to check.
        """
        self._sync_task_requests(state, prepared.text, turn_id)
        reply = self._answer_emergency_screen(state, prepared, user_input, turn_id)
        if reply is not None:
            return reply
        if state.phase == Phase.SUMMARY and (
            user_input.choice_value == "confirm_summary"
            or is_summary_confirmation_text(prepared.text)
        ):
            return self._deliver(
                state, self._confirmed_summary_reply(state, turn_id), turn_id
            )
        if state.active_task:
            from ...dialogue.task_conversation import is_observation_entry

            if is_observation_entry(
                self.content.tasks[state.active_task],
                prepared.text,
                user_input.choice_value,
            ):
                failure = self._try_task_result(
                    state, prepared.text, user_input.choice_value, turn_id
                )
                if failure:
                    return self._deliver(state, failure, turn_id)
            else:
                self._suspend_task_conversation(state, turn_id)
            state.risk = self.triage.assess(state, prepared.text)
            if state.risk.level == RiskLevel.URGENT:
                gated = self._urgent_or_defer(state, turn_id)
                if gated is not None:
                    return self._deliver(state, gated, turn_id)
        state.pending_input = prepared.text
        return self._run(state, prepared.text, turn_id)

    def _answer_emergency_screen(
        self,
        state: CaseState,
        prepared: _PreparedTurn,
        user_input: UserInput,
        turn_id: str,
    ) -> Optional[AgentReply]:
        """Write down a clicked yes/no/unknown, then re-triage what it means.

        The answer is recorded first and triaged second, because the screening
        fact is itself what the triage reads: a confirmed screen is an emergency
        even when the words around it say nothing alarming.

        ``None`` means the answer landed and nothing needs delivering yet -- the
        ordinary planning below this picks the turn up.
        """
        if not (
            state.active_question == "emergency_screen"
            and user_input.choice_value in {"yes", "no", "unknown"}
        ):
            return None
        self._record_emergency_screen_answer(
            state, user_input, prepared.text, turn_id
        )
        state.active_question = None
        state.risk = self.triage.assess(state, prepared.text)
        if state.risk.level == RiskLevel.EMERGENCY:
            reply = self._dispatch_emergency_clarification(
                state, prepared.text, (), state.risk.triggers, turn_id
            )
            # A denial already on record stands the risk down and returns None;
            # the fast path below still applies to that turn.
            if reply is not None:
                return reply
        # Only canonical option-only answers take the zero-model fast path.
        # Mixed free text must still reach the planner for fact extraction.
        if prepared.text.strip(" 。！!，,") in {
            "都没有",
            "这些危险情况都没有",
            "没有上述情况",
            "没有危险信号",
        }:
            pending_reply = self._offer_pending_self_check(state)
            if pending_reply:
                return self._deliver(state, pending_reply, turn_id)
        return None

    def _record_emergency_screen_answer(
        self, state: CaseState, user_input: UserInput, text: str, turn_id: str
    ) -> None:
        """Record the clicked screen option as controller-owned evidence.

        A clicked fixed option is not an LLM interpretation: the interpreter is
        only asked to map the option onto an assertion, and the raw urgent text
        has already passed the priority gate.  The caregiver/patient distinction
        is kept because the fact's source decides how it is quoted later.
        """
        interpreted = self.interpreter.interpret(
            self.content.question("emergency_screen"), text, user_input.choice_value
        )
        assertion = interpreted.assertion.value
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

    def correct_fact(
        self,
        session_id: str,
        fact_key: str,
        new_text: str,
        expected_fact_id: Optional[str] = None,
    ) -> AgentReply:
        """Correct one recorded fact from the interface.

        The correction is written as a new record with ``correction=True``, so
        the original stays in the history and the audit trail shows what was
        said before.  The patient's own words are the source: the model is not
        asked to interpret them, and an ungrounded model value is never
        accepted here.

        A closed case is read-only -- except one routed to emergency, where the
        route was decided by the text that caused it and an edit cannot
        retract it.  Without that exemption this override would silently
        rewind CLOSED to HISTORY, forking the case away from its conclusion
        sheet and orphaning its follow-up plan.

        The case is re-triaged afterwards, and an emergency found by the
        correction goes through the same dispatch as any other emergency turn.
        """
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
                # The same dispatch the submit path uses.  A correction that
                # re-triages into an emergency has to ask, confirm and answer
                # exactly like any other emergency turn -- three copies of that
                # decision was three chances for the correction path to drift
                # away from the one patients actually walk.
                reply = self._dispatch_emergency_clarification(
                    state, new_text, (), state.risk.triggers, turn_id
                )
                if reply is not None:
                    return reply
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
