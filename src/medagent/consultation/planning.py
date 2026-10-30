"""Mixin: planning for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEnginePlanning:
    def _try_task_result(self, state, text, choice_value, turn_id):
        # D-03: free-text "跳过" (with or without punctuation) counts as
        # one skip attempt, exactly like tapping the skip option.
        if choice_value is None and is_self_check_skip_text(text):
            choice_value = "skip"
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
        from ..measurement_reports import temperature_report

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
            from ..domain import utc_now

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
        # Package A (modular refactor): filtering + phase selection run
        # through the shared question_ordering scaffold; the same-phase
        # pick runs through the strategy mapped from the injected
        # ranker (HypothesisRanker -> HypothesisOrdering with the same
        # season_override, OrderedQuestionRanker -> CatalogOrdering,
        # anything else -> delegating shim). Filter predicate, phase
        # order, BACKGROUND offer + suspend-cap wrapping, the
        # ranker-id-mismatch fallback below, and the G-12(d) / _finish
        # tail are all unchanged; only the call sites moved.
        candidates = filter_candidates(
            self.content.questions,
            state,
            applies=self.content.question_applies_in_state,
            gates=lambda question, st: question_fact_gates_pass(
                question, st.facts
            ),
        )
        # Parity with the adaptive engine: non-blocking questions (sex,
        # reported_temperature) stay askable while any blocking field is
        # outstanding, but they never gate the summary/close on their own.
        if any(question.blocking for question in candidates):
            same_phase = earliest_phase_candidates(candidates)
            if same_phase[0].phase == Phase.BACKGROUND:
                offered = self._offer_task(state, chief)
                if offered:
                    return self._attach_suspend_cap_notice(state, offered)
            selected_id = ordering_for_ranker(self.ranker).choose(
                state, same_phase
            )
            selected = next(
                (item for item in same_phase if item.id == selected_id), same_phase[0]
            )
            return self._ask(
                state, selected, reason=f"{selected.fact_key} 是当前阶段尚未获得的关键字段"
            )

        offered = self._offer_task(state, chief)
        if offered:
            return self._attach_suspend_cap_notice(state, offered)
        # G-12(d) (round 3): an UNDETERMINED close must not swallow
        # still-owed discriminator questions. The gate sits strictly
        # at the close decision — normal blocking planning still owns
        # the question order while any blocking candidate remains —
        # and the eventual close still flows through _finish and the
        # _record_reply close rendering (including the W1 urgent
        # walkthrough), untouched.
        discriminator = self._undetermined_close_discriminator(state)
        if discriminator is not None:
            question, reask = discriminator
            self.events.append(
                Event(
                    state.session_id,
                    "close.undetermined_discriminator_asked",
                    {"question_id": question.id, "reask": reask},
                )
            )
            return self._attach_suspend_cap_notice(
                state,
                self._ask(
                    state,
                    question,
                    reason=(
                        "风险尚未确定，关闭前先追问鉴别点"
                        if not reask
                        else "风险尚未确定，关闭前再确认一次未问清的鉴别点"
                    ),
                ),
            )
        return self._attach_suspend_cap_notice(state, self._finish(state))

    def _undetermined_close_discriminator(self, state: CaseState):
        """G-12(d): one discriminator question still owed before an
        UNDETERMINED close, as ``(question, reask)`` — or None.

        Fires only when a fresh triage assessment (the same
        assessment _finish would use) is UNDETERMINED. No
        disposition, risk mapping, or threshold is touched: a new
        answer simply flows through the engine's normal reassessment.

        Both layers are restricted to existing catalog questions (no
        invented question, no catalog edit):
        (a) never-asked discriminators, in order — emergency_screen
        (a never-screened UNDETERMINED), gi_details (bleeding
        frequency/detail free text), trajectory (trend, the closest
        catalog proxy for still-ongoing/recurrent), dizzy_details
        (dizziness episode duration, only when the chief complaint
        makes it applicable);
        (b) once-per-question re-asks of UNKNOWN-answered
        discriminators — emergency_screen, then trajectory —
        guarded by a session event so a repeated unknown answer
        cannot loop the close forever. A PRESENT-answered detail
        (gi_details in G4, which already carries "twice/once") is
        never re-asked: repeating an answered question is its own
        defect class. Severity is deliberately not a discriminator
        here: it is not in the G-12 opinion's list (amount,
        frequency, ongoing/recurrent, dizziness-palpitation
        persistence).
        """
        if self.triage.assess(state).level != RiskLevel.UNDETERMINED:
            return None
        by_id = {item.id: item for item in self.content.questions}
        for question_id in (
            "emergency_screen",
            "gi_details",
            "trajectory",
            "dizzy_details",
        ):
            question = by_id.get(question_id)
            if question is None:
                continue
            if question.id in state.asked_questions:
                continue
            if question.fact_key in state.facts:
                continue
            if not self.content.question_applies_in_state(question, state):
                continue
            if not question_fact_gates_pass(question, state.facts):
                continue
            return question, False
        reasked = {
            (event.payload or {}).get("question_id")
            for event in self.events.for_session(state.session_id)
            if event.event_type == "close.undetermined_discriminator_asked"
            and (event.payload or {}).get("reask") is True
        }
        for question_id in ("emergency_screen", "trajectory"):
            question = by_id.get(question_id)
            if question is None or question.id not in state.asked_questions:
                continue
            if question.id in reasked:
                continue
            fact = state.facts.get(question.fact_key)
            if fact is None or fact.assertion != Assertion.UNKNOWN:
                continue
            if not self.content.question_applies_in_state(question, state):
                continue
            return question, True
        return None

    @staticmethod
    def _phase_rank(phase: Phase) -> int:
        return planning_rank(phase)

    def _self_check_suspend_count(
        self,
        state: CaseState,
        task_id: str,
        interaction_id: Optional[str] = None,
    ) -> int:
        """Consecutive suspensions of one task occurrence since its last
        progress event.

        Progress is a successful resume, a completion, a new task offer,
        or a clear of the same occurrence (an event without an
        interaction_id is treated as matching, since it cannot be shown
        to belong to a different occurrence). Only a task occurrence
        that keeps being suspended without any such progress is capped.
        """
        count = 0
        for event in self.events.for_session(state.session_id):
            payload = event.payload or {}
            if payload.get("task_id") != task_id:
                continue
            event_interaction = payload.get("interaction_id")
            if (
                interaction_id is not None
                and event_interaction is not None
                and event_interaction != interaction_id
            ):
                continue
            if event.event_type == "self_check.conversation_suspended":
                count += 1
            elif event.event_type in {
                "self_check.conversation_resumed",
                "self_check.completed",
                "self_check.offered",
                "self_check.conversation_cleared",
                "self_check.result_confirmed",
            }:
                count = 0
        return count

    def _attach_suspend_cap_notice(self, state: CaseState, reply) -> Any:
        """Prepend the cap notice to the next patient-facing reply once.

        A ``self_check.suspend_cap_noticed`` event marks the notice as
        delivered, so later replies in the session do not repeat it; a
        second cap later in the session gets its own notice.
        """
        capped = False
        noticed = False
        for event in self.events.for_session(state.session_id):
            if event.event_type == "self_check.suspend_cap_reached":
                capped = True
                noticed = False
            elif event.event_type == "self_check.suspend_cap_noticed":
                noticed = True
        if not capped or noticed or not reply.message:
            return reply
        if reply.message.startswith(SUSPEND_CAP_NOTICE):
            return reply
        reply.message = SUSPEND_CAP_NOTICE + reply.message
        self.events.append(
            Event(
                state.session_id,
                "self_check.suspend_cap_noticed",
                {},
            )
        )
        return reply

    def _offer_task(
        self, state: CaseState, chief: str, task_id: Optional[str] = None
    ) -> Optional[AgentReply]:
        if state.self_check_conversation:
            frame_id = state.self_check_conversation["task"]["id"]
            # D-04: a suspended task must not hang across turns forever.
            # At the cap it is cleared and planning resumes (the caller
            # falls through to the next question or the finish), instead
            # of re-offering the same suspended step verbatim.
            if self._self_check_suspend_count(
                state,
                frame_id,
                state.self_check_conversation["task"].get("interaction_id"),
            ) >= SELF_CHECK_SUSPEND_CAP:
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
                    )
                )
                return None
            if task_id is None or task_id == frame_id:
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
        return self._attach_suspend_cap_notice(
            state,
            AgentReply(
                prompt,
                question.phase,
                kind="question",
                question_id=question.id,
                quick_replies=question.options,
                developer_trace={
                    "reason": reason,
                    "content_version": self.content.versions["questions"],
                },
            ),
        )

    def _urgent_gate_fact_satisfied(self, state: CaseState, key: str) -> bool:
        """A minimum-set fact counts as satisfied when it is recorded,
        was already asked, or cannot be asked for this case (inapplicable
        or excluded by known facts). The last two keep the gate from
        deadlocking on a question the planner can never pose.
        """
        if key in state.facts:
            return True
        question = next(
            (item for item in self.content.questions if item.fact_key == key),
            None,
        )
        if question is None:
            return True
        if question.id in state.asked_questions:
            return True
        if not self.content.question_applies_in_state(question, state):
            return True
        return not question_fact_gates_pass(question, state.facts)

    def _urgent_gate_missing(self, state: CaseState) -> List[str]:
        """Minimum facts still missing before a single-clue urgent close.

        Empty when the urgent decision does not rest solely on the two
        single-clue triggers (severity score / worsening trajectory), so
        temperature, infant and orofacial urgent triggers — and every
        deterministic emergency — are never delayed by this gate.
        """
        risk = state.risk
        if risk is None or risk.level != RiskLevel.URGENT:
            return []
        triggers = list(risk.triggers or [])
        if not triggers or any(
            trigger not in URGENT_SINGLE_CLUE_TRIGGERS for trigger in triggers
        ):
            return []
        return [
            key
            for key in URGENT_MINIMUM_FACTS
            if not self._urgent_gate_fact_satisfied(state, key)
        ]

    def _deferred_risk(self, state: CaseState) -> RiskAssessment:
        """Risk level while an urgent/emergency decision is deferred.

        The publication gate only delivers controller-owned replies
        while risk is URGENT/EMERGENCY, so "keep asking" is impossible
        unless the not-yet-established escalation is stood down: the
        single-clue triggers are held in the deferral/clarification
        event, the underlying facts stay recorded, and the next
        assessment re-derives URGENT/EMERGENCY the moment the gate
        passes (or the patient confirms). The level mirrors triage
        without the deferred triggers: ROUTINE once screening is
        negative, UNDETERMINED otherwise. This never touches the
        deterministic emergency route itself.
        """
        if state.assertion("emergency_screen") == Assertion.ABSENT:
            return RiskAssessment(RiskLevel.ROUTINE, [])
        return RiskAssessment(RiskLevel.UNDETERMINED, [])

    def _urgent_or_defer(
        self, state: CaseState, turn_id: Optional[str] = None
    ) -> Optional[AgentReply]:
        """Urgent reply when the gate passes; None (defer, keep asking)
        when a single-clue urgent close would cut the session short."""
        missing = self._urgent_gate_missing(state)
        if not missing:
            return self._urgent(state)
        self.events.append(
            Event(
                state.session_id,
                "triage.urgent_deferred",
                {"missing": missing, "triggers": list(state.risk.triggers)},
                turn_id=turn_id,
            )
        )
        state.risk = self._deferred_risk(state)
        return None
