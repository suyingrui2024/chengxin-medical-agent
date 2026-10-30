"""Mixin: emergency for ConsultationEngine."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


class ConsultationEngineEmergency:
    def _emergency_clarification_pending(self, state: CaseState) -> Optional[Dict[str, Any]]:
        pending = state.runtime_snapshot.get("emergency_clarification")
        return pending if isinstance(pending, dict) else None

    def _emergency_clarification_action(
        self, state: CaseState, text: str, risk_tags=()
    ) -> str:
        """Scan-layer clarification gate (W1) for the emergency path.

        Returns "clarify" when — and only when — this turn's emergency
        signal consists solely of ``requires_clarification`` hits (bare
        location x signal co-occurrence, no definite hit, no confirmed
        screening, no direct emergency risk tag, no emergency carryover);
        "confirmed" when a pending clarification is answered by another
        affirmative co-occurrence of the same signal; "none" otherwise.
        ``scan_text``/``assess`` deliberately still contain combo labels,
        so this gate must read ``scan_detailed`` — without it, C-01/C-04
        behaviour is unchanged.
        """
        if state.closed_reason == "emergency_route":
            return "none"
        screen = state.facts.get("emergency_screen")
        if (
            screen is not None
            and screen.assertion == Assertion.PRESENT
            and screen.value is True
        ):
            return "none"
        if set(risk_tags or ()) & DIRECT_EMERGENCY_TAGS:
            return "none"
        try:
            hits = self.triage.scan_detailed(text)
        except Exception:
            return "none"
        if not hits:
            return "none"
        if any(not hit.requires_clarification for hit in hits):
            return "none"
        pending = self._emergency_clarification_pending(state)
        if pending and set(pending.get("labels") or ()) & {
            hit.label for hit in hits
        }:
            return "confirmed"
        return "clarify"

    def _emergency_clarification_reply(
        self, state: CaseState, text: str, turn_id: Optional[str] = None
    ) -> AgentReply:
        try:
            hits = self.triage.scan_detailed(text)
        except Exception:
            hits = []
        # Same publication-gate reason as _urgent_or_defer: a
        # clarification question cannot be delivered while risk is
        # EMERGENCY, so the not-yet-confirmed escalation is stood down
        # (facts and the pending clarification record are untouched).
        state.risk = self._deferred_risk(state)
        labels = sorted({hit.label for hit in hits})
        state.runtime_snapshot["emergency_clarification"] = {
            "text": text,
            "labels": labels,
            "evidence": [hit.evidence for hit in hits],
        }
        self.events.append(
            Event(
                state.session_id,
                "emergency.clarification_asked",
                {"labels": labels},
                turn_id=turn_id,
            )
        )
        return AgentReply(
            "想再确认一下你提到的情况，才能判断是否需要立即急救："
            "这种不适具体是怎样的感觉（刺痛、压榨样闷痛还是其他感觉）？"
            "有没有放射到手臂、背部或下颌？有没有出汗、呼吸困难？"
            "是持续不缓解，还是间断出现、每次持续多久？"
            "如果确实有这种情况可以直接确认；如果没有，也可以直接否认。",
            state.phase,
            kind="clarification",
            quick_replies=[
                QuickReply("是的，确实有这种情况", "confirm_emergency_clarification"),
                QuickReply("没有，我没有这种情况", "deny_emergency_clarification"),
            ],
            developer_trace={
                "reason": "急症信号仅为位置词与信号词裸共现，先鉴别澄清，患者确认后才走急症路径"
            },
        )

    def _confirm_emergency_clarification(
        self, state: CaseState, turn_id: Optional[str] = None
    ) -> Optional[AgentReply]:
        """Close as emergency after the patient confirmed clarification.

        The stored labels are re-rendered through the scan layer's
        helper with ``include_clarification=True`` — that flag is set
        only here, after confirmation, never on the first co-occurrence
        turn.
        """
        pending = self._emergency_clarification_pending(state)
        if not pending:
            return None
        labels = list(pending.get("labels") or [])
        state.runtime_snapshot.pop("emergency_clarification", None)
        self.events.append(
            Event(
                state.session_id,
                "emergency.clarification_confirmed",
                {"labels": labels},
                turn_id=turn_id,
            )
        )
        state.risk = RiskAssessment(RiskLevel.EMERGENCY, labels)
        return self._emergency_reply(state, labels, include_clarification=True)

    def _emergency_scan_hits(self, state: CaseState):
        """Scan hits over every user turn so far, de-duplicated.

        The emergency reply renders from hits, not from the raw trigger
        strings, so a replayed close (closed-session guard) still renders
        the original turn's signals instead of the latest message's.
        """
        texts = []
        for event in self.events.for_session(state.session_id):
            if event.event_type == "turn.user_message":
                text = event.payload.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        if not texts:
            texts = [
                item["content"]
                for item in state.transcript
                if item.get("role") == "user"
            ]
        hits = []
        seen = set()
        for text in texts:
            try:
                scanned = self.triage.scan_detailed(text)
            except Exception:
                continue
            for hit in scanned:
                key = (hit.flag_id, hit.label, hit.evidence)
                if key not in seen:
                    seen.add(key)
                    hits.append(hit)
        return hits

    def _confirmed_emergency_labels(
        self, state: CaseState, triggers, *, include_clarification=False
    ) -> List[str]:
        """Render triggers through the scan layer's helper (W1).

        Scan-derived labels are governed by the helper: clarification
        hits drop unless explicitly confirmed, denied evidence drops.
        Labels the scanner cannot produce (screening confirmation,
        risk-tag selection, user-requested help) are not scan labels and
        pass through untouched. Empty-set semantics (W1 caveat): an
        empty result is a rendering fact only — it must never be read
        as "no emergency" when another route already fired, so callers
        still close; they just name no signal.
        """
        hits = self._emergency_scan_hits(state)
        try:
            rendered = self.triage.confirmed_trigger_labels(
                hits, state, include_clarification=include_clarification
            )
        except Exception:
            rendered = []
        definition_labels = {item.label for item in load_definitions()}
        for trigger in triggers or []:
            if trigger not in definition_labels and trigger not in rendered:
                rendered.append(trigger)
        return rendered

    def _emergency_reply_with_clarification_memory(
        self, state: CaseState, triggers
    ) -> AgentReply:
        """Emergency reply that does not filter out an already-confirmed
        clarification signal on replay.

        A confirmed clarification pops its pending record, so a later
        replayed close (e.g. after a fact restoration on an
        emergency-closed session) would otherwise render through
        ``confirmed_trigger_labels`` with ``include_clarification=False``
        and drop the combo label to an empty trigger list. A prior
        ``emergency.clarification_confirmed`` event for one of the
        current triggers is the standing proof of confirmation, so the
        replay renders with the flag on.
        """
        confirmed = set()
        for event in self.events.for_session(state.session_id):
            if event.event_type == "emergency.clarification_confirmed":
                confirmed.update(event.payload.get("labels") or [])
        include = bool(confirmed & set(triggers or []))
        return self._emergency_reply(
            state, triggers, include_clarification=include
        )

    def _confirmed_facts_summary(self, state: CaseState) -> List[Dict[str, Any]]:
        """Affirmed facts for the emergency/urgent handoff record.

        Only PRESENT facts, never mapped signal names alone; the chief
        complaint falls back to the first user turn when no fact has
        landed yet (early emergency close, e.g. P-11/R-07/T-15).
        """
        items = []
        for key, fact in state.facts.items():
            if fact.assertion != Assertion.PRESENT or key == "emergency_screen":
                continue
            label = FACT_LABELS.get(key)
            if label is None:
                continue
            value = fact.value
            display = VALUE_LABELS.get(str(value), value)
            if key == "age_years" and isinstance(value, (int, float)):
                display = (
                    f"{round(value * 12)} 个月" if value < 2 else f"{value} 岁"
                )
            items.append(
                {
                    "key": key,
                    "label": label,
                    "value": display,
                    "evidence": fact.evidence or "",
                }
            )
        if not items:
            first_user = next(
                (
                    item["content"]
                    for item in state.transcript
                    if item.get("role") == "user"
                ),
                None,
            )
            if first_user:
                # P-21: an early emergency close has no recorded chief
                # fact, so the fallback echoes the whole first turn —
                # including its consult_for identity sentence ("我是替
                # 孩子咨询的。"). The chief echo must be only the
                # complaint itself, so a leading identity sentence is
                # dropped from both value and evidence (evidence is
                # what _close_facts_text prefers).
                chief_text = self._strip_identity_prefix(first_user)
                items.append(
                    {
                        "key": "chief_complaint",
                        "label": "主要问题",
                        "value": chief_text,
                        "evidence": chief_text,
                    }
                )
        return items[:8]

    @staticmethod
    def _strip_identity_prefix(text: str) -> str:
        """Drop a leading consult_for identity sentence, if present.

        Only the first sentence is examined, only when more text
        follows, and only when that sentence is itself an identity
        clause: it starts with 我, names 自己 / 替 / 为, and ends
        with 咨询 / 咨询的 / 咨询一下. A real statement that merely
        mentions consulting ("我自己咨询过医生") does not end that
        way and passes through untouched — this is echo hygiene, not
        parsing of the complaint."""
        for separator in ("。", "．", "，"):
            first, sep, rest = text.partition(separator)
            if not sep or not rest.strip():
                continue
            segment = first.strip()
            if (
                segment.startswith("我")
                and ("自己" in segment or "替" in segment or "为" in segment)
                and (
                    segment.endswith("咨询")
                    or segment.endswith("咨询的")
                    or segment.endswith("咨询一下")
                )
            ):
                return rest.strip()
        return text

    def _emergency_reply(
        self, state: CaseState, triggers: List[str], requested_help=False, *, include_clarification=False
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
        original = list(dict.fromkeys(triggers or []))
        confirmed_facts = self._confirmed_facts_summary(state)
        facts_text = self._close_facts_text(confirmed_facts)
        routing_text = self._close_routing_text(state, confirmed_facts)
        if requested_help:
            message = (
                self._CLOSE_SOOTHING
                + "你已选择优先求助。本次问诊已停止。"
                "请现在拨打 120，或让身边的人协助呼叫急救；不要自己驾车。"
            )
            if facts_text:
                message += f"已确认的情况：{facts_text}。"
            if routing_text:
                message += routing_text
            message += "不要继续等待本次对话。"
            summary = {
                "care_route": "立即急救",
                "triggers": original,
                "confirmed_facts": confirmed_facts,
            }
        else:
            # emergency_explanation already names the signals, gives the
            # 120 instruction and adds waiting guidance (stay still, do
            # not drive yourself, have someone accompany you); keep the
            # standing "do not keep waiting for this chat" as the tail
            # instead of stacking a second full instruction sentence.
            from ..conclusion import emergency_explanation

            rendered = self._confirmed_emergency_labels(
                state, original, include_clarification=include_clarification
            )
            if rendered != original:
                self.events.append(
                    Event(
                        state.session_id,
                        "emergency.triggers_filtered",
                        {"original": original, "rendered": rendered},
                    )
                )
            message = self._CLOSE_SOOTHING + emergency_explanation(rendered)
            if facts_text:
                message += f"已确认的情况：{facts_text}。"
            if routing_text:
                message += routing_text
            message += "不要继续等待本次对话。"
            summary = {
                "care_route": "立即急救",
                "triggers": rendered,
                "confirmed_facts": confirmed_facts,
            }
        return AgentReply(
            message,
            Phase.CLOSED,
            kind="emergency",
            summary=summary,
            developer_trace={"reason": "确定性急症规则优先于后续问诊"},
        )

    def _urgent(self, state: CaseState) -> AgentReply:
        state.subject_resume = state.subject_candidate = None
        self._clear_calculation(state, "urgent")
        self._clear_observation(state, "urgent")
        self._clear_task_conversation(state, "urgent")
        reply = self._finish(state, risk_override=deepcopy(state.risk))
        reply.kind = "urgent"
        triggers = "、".join(state.risk.triggers)
        # Batch C close copy: soothing + direct destination answer +
        # facts, on top of the batch-A promise rule. The
        # "下面是已采集的信息" promise must not stand without the
        # information itself: list the collected facts inline when there
        # are any (with the "已采集的信息：" lead-in, which is only
        # ever used together with the list), and drop any such clause
        # entirely when there are none (the structured summary still
        # rides on the reply). The destination answer is keyed only to
        # the urgent_today level via _close_direct_sentence; no
        # department field is read here (C12 residual).
        items = self._confirmed_facts_summary(state)
        facts_text = self._close_facts_text(items)
        plan = state.follow_up or reply.follow_up or {}
        direct = self._close_direct_sentence(plan.get("due_in_hours") or 24)
        head = (
            self._CLOSE_SOOTHING
            + "目前有需要尽快就医评估的线索（"
            + triggers
            + "）。"
            + direct
            + "建议今天联系医疗机构；"
        )
        if items:
            reply.message = (
                head
                + "已采集的信息："
                + facts_text
                + "，不必等问诊补齐再求助。"
            )
        else:
            reply.message = head + "不必等问诊补齐再求助。"
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
