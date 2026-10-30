"""Mixin: run for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEngineRun:
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
                from ..subagents import watermark

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
                if (
                    action == "clarify"
                    and self._emergency_clarification_denied_before(state, text)
                ):
                    # C-10: same group already denied; suppress the
                    # repeat clarification. Record_facts re-assesses
                    # risk from this same text, so this post-record
                    # site is where a suppressed pre-model turn would
                    # otherwise re-trigger; stand risk down and fall
                    # through to the normal next question.
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
