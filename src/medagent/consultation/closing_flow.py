"""Mixin: closing flow for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEngineClosing:
    @staticmethod
    def _close_facts_text(items: List[Dict[str, Any]]) -> str:
        """Render confirmed facts with patient evidence preferred.

        ``_confirmed_facts_summary`` (batch A) already restricts to
        PRESENT facts; here the evidence quote wins over the mapped
        display value, so the patient sees their own words (e.g.
        "呼吸费劲、胸闷") rather than only the scanner's label
        (e.g. "严重呼吸困难").
        """
        parts = []
        for item in items:
            evidence = (item.get("evidence") or "").strip()
            text = evidence if evidence else str(item.get("value") or "").strip()
            # P-21: patient wording often ends with its own 。; the
            # emergency template adds another 。 after the joined
            # list, which used to render "叫不应。。". Strip one
            # layer of terminal punctuation here (the single shared
            # renderer for the emergency and urgent lists) so the
            # caller's terminator is the only one.
            text = text.rstrip("。！？.")
            if text:
                parts.append(f"{item.get('label')}：{text}")
        return "；".join(parts)

    def _close_direct_sentence(
        self, window_hours, disposition=None
    ) -> str:
        """Thin forwarder: body lives in closing._close_direct_sentence."""
        return closing._close_direct_sentence(window_hours, disposition=disposition)

    @staticmethod
    def _close_routing_text(state: CaseState, items: List[Dict[str, Any]]) -> str:
        """Dental-emergency routing (K-15), keyed to patient words only.

        A tooth/oral complaint in the confirmed facts or in a user
        turn earns the item's routing wording "口腔急诊/急诊科".
        Anything else earns no routing sentence here: the 120
        instruction (emergency) or the level direct answer (urgent)
        already carries the destination.
        """
        texts = []
        for item in items:
            texts.append(str(item.get("evidence") or ""))
            texts.append(str(item.get("value") or ""))
        texts.extend(
            item.get("content", "")
            for item in state.transcript
            if item.get("role") == "user"
        )
        joined = " ".join(texts)
        if any(word in joined for word in ("牙", "牙齿", "口腔")):
            return "接诊指向：口腔急诊/急诊科，到了先说明是牙外伤出血。"
        return ""

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
        declined: List[str] = []
        declined_details: List[Dict[str, Any]] = []
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
            # Batch B label fallback: this file's table, then the
            # handoff table (which carries keys this file lacks, e.g.
            # sex -> 性别), then the task label, then the raw key.
            # The handoff import is top-level because handoff imports
            # no medagent module, so no import cycle is possible.
            label = (
                FACT_LABELS.get(key)
                or HANDOFF_LABELS.get(key)
                or task.get("result_label")
                or task.get("title")
                or key
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
                # A refusal is not "unknown": it stays visible in the
                # unknown list for existing renderers, and is additionally
                # flagged as declined so new consumers do not mistake it
                # for a field the patient could not answer.
                if is_refusal_text(fact.evidence or ""):
                    declined.append(label)
                    declined_details.append({**detail, "declined": True})
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
            "declined": declined,
            "declined_details": declined_details,
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

    def _apply_group_bridge(self, state: CaseState, reply: AgentReply) -> None:
        """P1 Tier 1 group-bridge wiring (P2 owns this call site).

        Runs after the single-question bridge and, on a group switch,
        prepends the group bridge even when the single bridge already
        spliced: the two say different things (single-question承接 vs
        group intent), and the old "single applied => skip group" rule
        starved the group bridge, because the single anchor fires on
        almost every round. Design §3.2 requires a group bridge
        before every group of questions; the switch rule is what
        makes that hold without repeating the bridge on every
        question of the group. The last asked group lives in
        ``runtime_snapshot["last_question_group"]`` and is updated
        only when a bridge actually splices: "other" questions carry
        no bridge and do not reset it, so returning to a group after
        other business does not re-bridge, and a lint-dropped bridge
        does not poison the next question of that group. The group
        comes solely from bridging.question_group (P1's contract);
        a P3 warning question reaches it under its pinned id
        ``warning_<domain>``. lint_group_bridge False drops the whole
        sentence. Runs before the publication gate, like the single
        bridge, so the gate sees the final patient-visible text.
        Fail-open: any failure leaves the question untouched.
        """
        if reply.kind != "question" or not reply.question_id:
            return
        try:
            from ..bridging import (
                build_group_bridge,
                lint_group_bridge,
                question_group,
            )

            group = question_group(reply.question_id, state)
            bridge = build_group_bridge(group)
        except Exception:
            return
        if not bridge or not lint_group_bridge(bridge):
            return
        snapshot = state.runtime_snapshot
        if snapshot is None:
            snapshot = state.runtime_snapshot = {}
        if group == snapshot.get("last_question_group"):
            return
        reply.message = bridge + reply.message
        snapshot["last_question_group"] = group
        trace = dict(reply.developer_trace or {})
        trace["group_bridge"] = {"applied": True, "group": group}
        bridging = trace.get("bridging")
        if not (isinstance(bridging, dict) and bridging.get("applied")):
            trace["bridging"] = {"applied": True, "kind": f"group:{group}"}
        reply.developer_trace = trace

    def _record_reply(
        self, state: CaseState, reply: AgentReply, turn_id: str, reason: str
    ) -> None:
        self._apply_bridging(state, reply)
        self._apply_group_bridge(state, reply)
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
                # Body lives in closing.render_confirmed_close; the
                # transition / phase guard above stays here.
                closing.render_confirmed_close(state, reply)
                # P2 urgent close (mechanism ①): _urgent built its
                # message before the sheet existed, so the sheet's
                # worry / goal sections are appended here, at the
                # choke point where the sheet is attached — never in
                # _urgent itself. Same shared helper as the confirmed
                # close, plus the same per-part goal handling.
                if reply.kind == "urgent" and isinstance(
                    reply.conclusion, dict
                ):
                    # Urgent close rendering lives in
                    # closing.render_urgent_close (worry/goal +
                    # unaddressed parts + uncertainty + unconfirmed
                    # background, in that order); the visible-before-
                    # append capture and echo strip live there too.
                    closing.render_urgent_close(state, reply, reply.conclusion)
                # P3 scope disclosure: an off-pathway close says
                # plainly what this consultation did not cover.
                # Inside the transition block only, so a guard reply
                # on an already-closed case never repeats it.
                if state.closed_reason in (
                    "confirmed_by_user",
                    "urgent_route",
                ) and isinstance(reply.conclusion, dict):
                    closing.append_scope_note(state, reply)
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
                from ..task_interactions import task_with_interaction

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
        # Controller choke point for conclusion sheets. The vocab layer
        # (W2) already applies apply_neutral_fallback inside
        # compose_conclusion for direction-less, off-pathway sheets, so
        # every controller close routes through it here; do NOT re-apply
        # it after compose — that would overwrite the caller-supplied
        # return_now_if list compose restores after the fallback. The
        # neutral self-care group is the reviewed generic group (no
        # second neutral group is created; batch C overlap noted).
        try:
            from ..conclusion import compose_conclusion, sheet_mode

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

    @staticmethod
    def _sheet_worry_goal_text(sheet: Dict[str, Any], include_goal: bool = True) -> str:
        """Thin forwarder: body lives in closing._sheet_worry_goal_text."""
        return closing._sheet_worry_goal_text(sheet, include_goal=include_goal)

    @staticmethod
    def _strip_close_facts_echo(text: str) -> str:
        """Thin forwarder: body lives in closing._strip_close_facts_echo."""
        return closing._strip_close_facts_echo(text)

    def _append_sheet_worry_goal(
        self, reply: AgentReply, sheet: Dict[str, Any]
    ) -> None:
        """Thin forwarder: body lives in closing._append_sheet_worry_goal."""
        closing._append_sheet_worry_goal(reply, sheet)

    def _append_unaddressed_goals(
        self,
        state: CaseState,
        reply: AgentReply,
        sheet: Dict[str, Any],
        visible_text: str,
        include_exam_parts: bool = False,
    ) -> None:
        """Thin forwarder: body lives in closing._append_unaddressed_goals."""
        closing._append_unaddressed_goals(
            state, reply, sheet, visible_text, include_exam_parts=include_exam_parts
        )

    @staticmethod
    def _append_unconfirmed_background_note(
        state: CaseState, reply: AgentReply
    ) -> None:
        """Thin forwarder: body lives in closing._append_unconfirmed_background_note."""
        closing._append_unconfirmed_background_note(state, reply)

    @staticmethod
    def _append_uncertainty_note(
        reply: AgentReply, sheet: Dict[str, Any]
    ) -> None:
        """Thin forwarder: body lives in closing._append_uncertainty_note."""
        closing._append_uncertainty_note(reply, sheet)

    def _append_scope_note(self, state: CaseState, reply: AgentReply) -> None:
        """Thin forwarder: body lives in closing.append_scope_note."""
        closing.append_scope_note(state, reply)

    def _speak_close_line(self, state: CaseState, reply: AgentReply) -> None:
        """Thin forwarder: body lives in closing.render_confirmed_close."""
        closing.render_confirmed_close(state, reply)

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
