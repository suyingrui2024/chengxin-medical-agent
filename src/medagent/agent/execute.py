"""Mixin: execute for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEngineExecute:
    def _execute(
        self, state: CaseState, command: Dict[str, Any], text: str, turn_id: str
    ):
        validate_command(command)
        tool, args = command["tool"], command["arguments"]
        if tool == "change_subject":
            evidence = args["evidence"]
            if (
                not isinstance(evidence, str)
                or not 1 <= len(evidence) <= 300
                or evidence not in text
            ):
                raise ProposalError("换人需引用本轮明确对象的连续原话。")
            requested = subject_reference(evidence)
            current = subject_profile(state)
            if not requested or not state.facts:
                raise ProposalError("尚未确定当前对象时先澄清或记录，不必另建问诊。")
            if not explicit_switch(text, current) and (
                not current
                or not current["reference"]
                or compatible(current["reference"], requested)
            ):
                raise ProposalError("这可能是当前对象的称谓补充。先澄清，不把普通家族史当作换人。")
            return self._subject_change_reply(
                state,
                {
                    "category": "self"
                    if requested["attributes"][0] == "self"
                    else "proxy",
                    "label": requested["label"],
                },
                text,
                turn_id,
            )
        if tool == "repeat_self_check":
            from ..observations import repeat_intent

            evidence = args["evidence"]
            if (
                state.observation_feedback
                and state.observation_feedback["task_id"] == args["task_id"]
            ):
                raise ProposalError("这次测量已处理，请先处理同轮补充信息，不要重复已完成的动作")
            if not isinstance(args["task_id"], str):
                raise ProposalError("图示任务ID必须是字符串")
            task = self._repeat_task(state, args["task_id"])
            if (
                not isinstance(evidence, str)
                or not 2 <= len(evidence) <= 300
                or evidence not in text
                or not repeat_intent(task, text)
                or not repeat_intent(task, evidence)
            ):
                raise ProposalError("重复观察必须引用本轮明确请求的连续原文")
            return self._request_repeat(state, task["id"], text, turn_id)
        if tool == "revise_self_check":
            self._request_location_revision(state, args, text, turn_id)
            return {
                "ok": True,
                "case": self._context(state, text),
                "next": "修改卡已建立；可用propose_self_check_result整理本轮明确更正，或show_self_check展示核对。原事实尚未修改。",
            }
        if tool == "request_self_check":
            task_id, evidence = args["task_id"], args["evidence"]
            task = next(
                (
                    t
                    for t in self.content.applicable_tasks(
                        str(state.get("chief_complaint", ""))
                    )
                    if t["id"] == task_id and t["result_key"] not in state.facts
                ),
                None,
            )
            if (
                task is None
                or not isinstance(evidence, str)
                or not 2 <= len(evidence) <= 300
                or evidence not in text
            ):
                raise ProposalError("请求必须针对已注册适用任务，并引用本轮明确请求的连续原文")
            if not any(
                item["task_id"] == task_id for item in state.pending_self_checks
            ):
                request = {"task_id": task_id, "evidence": evidence, "turn_id": turn_id}
                state.pending_self_checks.append(request)
                self.events.append(
                    Event(
                        state.session_id,
                        "self_check.requested",
                        dict(request),
                        turn_id=turn_id,
                    )
                )
            return {"ok": True, "case": self._context(state, text)}
        if tool == "run_capability":
            if not isinstance(args["id"], str) or not isinstance(args["input"], dict):
                raise ProposalError("能力 ID 或输入格式无效")
            return self.request_calculation(
                state, args["id"], args["input"], args["evidence"], text, turn_id
            )
        if tool == "spawn_subagent":
            # Decision-layer kind whitelist (healthcheck P1-A-D2): only
            # the two model-proposed kinds may be spawned from here.
            # Rejected before the coordinator is called and before the
            # per-round spawn budget is consumed, so the model can
            # retry in the same round with a legal kind.
            task_kind = args.get("task_kind")
            if not isinstance(task_kind, str) or task_kind not in MODEL_SPAWN_TASK_KINDS:
                return {
                    "ok": False,
                    "error": (
                        "task_kind 只能是 evidence_lookup 或 second_opinion；"
                        f"「{task_kind}」不能由模型提议（控制器专属种类如 "
                        "case_digest 不在模型可提议范围内），"
                        "请改用 evidence_lookup 或 second_opinion 重提"
                    ),
                }
            coordinator = self.subagent_coordinator
            if coordinator is None:
                return {"ok": False, "status": "unavailable"}
            if self._round_spawn_counts.get(state.session_id, 0) >= 1:
                return {"ok": False, "error": "每轮至多派生一次子智能体"}
            self._round_spawn_counts[state.session_id] = (
                self._round_spawn_counts.get(state.session_id, 0) + 1
            )
            turn = self._active_turns.get(state.session_id)
            result = coordinator.run_spawn(
                state,
                args,
                session_id=state.session_id,
                cancelled=turn.cancelled if turn is not None else None,
            )
            if self._subagent_mode_for(args.get("task_kind")) != "advisory":
                if isinstance(result, dict) and result.get("ok"):
                    # Shadow: the run executed and is recorded as events
                    # only; nothing a subagent produced enters the context.
                    return {
                        "ok": True,
                        "spawn": {
                            "status": "shadow_recorded",
                            "spawn_id": result.get("spawn_id"),
                        },
                    }
                # off / disabled / over the daily fuse / unavailable: the
                # spawn did not run; report its real status, not shadow.
                return {
                    "ok": False,
                    "status": (
                        result.get("status")
                        if isinstance(result, dict)
                        else None
                    )
                    or "unavailable",
                }
            if isinstance(result, dict):
                # Design §5.7.2: a blinded second opinion whose structured
                # tendency disagrees with the case's current risk group is
                # surfaced to the main model as a flag, never to the patient.
                tendency_groups = {
                    "self_care": "routine",
                    "routine_visit": "routine",
                    "urgent_today": "urgent",
                    "emergency": "emergency",
                }
                current = (
                    state.risk.level.value
                    if getattr(state, "risk", None) is not None
                    else None
                )
                for branch in result.get("branches") or []:
                    if branch.get("task_kind") != "second_opinion" or (
                        branch.get("status") not in {"completed", "partial"}
                    ):
                        continue
                    tendency = branch.get("disposition_tendency")
                    if (
                        tendency in tendency_groups
                        and current
                        and tendency_groups[tendency] != current
                    ):
                        result = {
                            **result,
                            "second_opinion_disagreement": True,
                            "second_opinion_tendency": tendency,
                        }
                        # The flag above is per-turn model feedback; this
                        # event is the durable record the metrics layer
                        # aggregates disagreement rates from. Same firing
                        # condition, no change to what is fed back.
                        self.events.append(
                            Event(
                                state.session_id,
                                "subagent.disagreement",
                                {
                                    "task_kind": "second_opinion",
                                    "tendency": tendency,
                                    "risk_group": current,
                                },
                                turn_id=turn_id,
                            )
                        )
                        break
            return {"ok": True, "spawn": result}
        if tool == "propose_self_check_result":
            return self._propose_task_result(state, args, text, turn_id)
        if tool == "record_facts":
            rejected = []
            # J-01: a refusal proposal (unknown + a refusal quote from
            # this turn) bypasses the unknown-evidence validator, which
            # only accepts "不知道"-style quotes and would otherwise
            # deadlock the summary gate. The refusal still lands as a
            # recorded UNKNOWN fact with the verbatim quote — no value
            # is invented, and the field leaves missing_required exactly
            # like any other recorded field. Age and sex are treated
            # identically: the gate is satisfied by recording, not by
            # demoting either field to non-blocking.
            declined_keys = set()
            declined_proposals = []
            kept_facts = None
            raw_facts = args.get("facts") if isinstance(args, dict) else None
            if isinstance(raw_facts, list):
                kept_facts = []
                for item in raw_facts:
                    if isinstance(item, dict) and self._is_declined_proposal(
                        state, text, item
                    ):
                        declined_keys.add(item["key"])
                        declined_proposals.append(
                            {
                                "key": item["key"],
                                "value": None,
                                "assertion": Assertion.UNKNOWN,
                                "evidence": item.get("evidence"),
                                "correction": bool(item.get("correction")),
                            }
                        )
                    else:
                        kept_facts.append(item)
            if kept_facts is not None and not kept_facts and declined_proposals:
                proposals = []
            else:
                try:
                    proposals = validate_proposals(
                        self.content,
                        state,
                        text,
                        kept_facts if kept_facts is not None else args.get("facts"),
                    )
                except ProposalError as exc:
                    # Partial recording (wave-2 P2): items that passed validation
                    # land now, and only the failures go back to the model with
                    # per-item reasons and repair guidance — the model re-proposes
                    # just the fixed items instead of re-sending (and risking)
                    # the whole batch. All-or-nothing callers are unaffected:
                    # correct_fact pre-validates its single item before calling,
                    # and the controller's direct emergency-screen recording
                    # bypasses this tool entirely. A batch where nothing passed
                    # keeps the old atomic behaviour and raises unchanged.
                    accepted = getattr(exc, "accepted", None)
                    if not exc.verdicts or not accepted:
                        raise
                    proposals = accepted
                    rejected = [v for v in exc.verdicts if not v["ok"]]
            proposals = list(proposals) + declined_proposals
            # J-03 trajectory evidence guard (host side): steady,
            # not-improving wording must not book as worsening. The item
            # is rejected with repair guidance (record stable, or clarify
            # whether it truly worsened) instead of landing; the urgent
            # single-clue gate is the backstop if a worsening value ever
            # lands through another path.
            guarded_proposals = []
            for proposal in proposals:
                if (
                    proposal["key"] == "trajectory"
                    and proposal["value"] == "worsening"
                    and is_steady_not_improving_evidence(
                        proposal.get("evidence") or ""
                    )
                ):
                    rejected.append(
                        {
                            "key": "trajectory",
                            "ok": False,
                            "reason": (
                                "证据表述为平稳未好转，不得记为加重"
                            ),
                            "guidance": (
                                "请按变化不大/平稳落账，或先澄清是否确实加重后再提议。"
                            ),
                        }
                    )
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.trajectory_guard",
                            {
                                "key": "trajectory",
                                "evidence": proposal.get("evidence"),
                            },
                            turn_id=turn_id,
                        )
                    )
                elif (
                    proposal["key"] == "trajectory"
                    and proposal["value"] == "stable"
                    and any(
                        phrase in (proposal.get("evidence") or "")
                        for phrase in FLUCTUATION_PHRASES
                    )
                ):
                    # Batch C BURDEN (D-02): fluctuation wording
                    # ("此起彼伏，旧的消了新的又起") is waxing and
                    # waning, not steadiness. A fluctuation answer
                    # must not be forced onto the nearest option:
                    # reject with guidance to record the
                    # intermittent option (E2E-P2-5: the old
                    # "record unknown" guidance was itself
                    # unbookable — unknown requires an uncertainty
                    # quote, while "一阵一阵的，中间有缓解" is a
                    # definite statement) or clarify with an open
                    # question instead.
                    rejected.append(
                        {
                            "key": "trajectory",
                            "ok": False,
                            "reason": (
                                "证据表述为波动交替，不得记为变化不大"
                            ),
                            "guidance": (
                                "请记为一阵一阵的（intermittent），或用开放式追问澄清总体"
                                "趋势后再提议，不得把波动描述强行映射为稳定。"
                            ),
                        }
                    )
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.trajectory_fluctuation_guard",
                            {
                                "key": "trajectory",
                                "evidence": proposal.get("evidence"),
                            },
                            turn_id=turn_id,
                        )
                    )
                else:
                    guarded_proposals.append(proposal)
            proposals = guarded_proposals
            # Batch C BURDEN (O-06): an answer given while the screening
            # question is active IS the screening answer. When the model
            # files it elsewhere (associated_symptoms) instead, the host
            # files the denial as the screening fact itself, so the
            # screen is not left unanswered to be re-asked verbatim.
            # This mirrors the deterministic engine, which interprets
            # any reply against the active question; only denial replies
            # are backstopped here (affirmations are triage-owned).
            active_screen = next(
                (
                    q
                    for q in self.content.questions
                    if q.id == state.active_question
                ),
                None,
            )
            if (
                active_screen is not None
                and active_screen.fact_key == "emergency_screen"
                and not any(
                    item["key"] == "emergency_screen" for item in proposals
                )
                and (
                    "都没有" in text
                    or text.strip().startswith(("没有", "无", "都没", "否认"))
                )
            ):
                proposals.append(
                    {
                        "key": "emergency_screen",
                        "value": None,
                        "assertion": Assertion.ABSENT,
                        "evidence": text,
                        "correction": False,
                    }
                )
            if not proposals and rejected:
                return {
                    "ok": False,
                    "partial": True,
                    "recorded": [],
                    "rejected": [
                        {
                            "key": verdict["key"],
                            "reason": verdict["reason"],
                            "guidance": verdict["guidance"],
                        }
                        for verdict in rejected
                    ],
                    "next": (
                        "已通过项已写入，不要重复提议；只需修正 rejected 中的"
                        "字段后重新提议 record_facts（只含修正项）。"
                    ),
                    "case": self._context(state, text),
                }
            self._guard_calculation_followup_updates(state, proposals)
            # No mutation happens until the accepted set has validated per
            # item and been dependency-checked as a set (_prepare_fact_updates
            # still raises on a subject boundary before anything is recorded).
            incoming_subject = next(
                (p["value"] for p in proposals if p["key"] == "consult_for"),
                state.get("consult_for"),
            )
            self._prepare_fact_updates(state, proposals, turn_id)
            active_before = next(
                (q for q in self.content.questions if q.id == state.active_question),
                None,
            )
            for proposal in proposals:
                source = (
                    Source.CAREGIVER
                    if incoming_subject in {"child", "other_adult"}
                    else Source.PATIENT
                )
                # Batch C BURDEN (O-12), same merge as the deterministic
                # engine's _record_fact: a non-correction writeback must
                # not drop earlier denial/unknown segments. (In practice
                # validated non-correction proposals cannot target an
                # already-recorded key, so this is a backstop; explicit
                # corrections replace, as in correct_fact.)
                value = proposal["value"]
                if not proposal.get("correction"):
                    previous = state.facts.get(proposal["key"])
                    if previous is not None and isinstance(value, str):
                        previous_text = (
                            previous.value
                            if isinstance(previous.value, str)
                            else previous.evidence
                        )
                        value = merge_preserving_denials(previous_text, value)
                fact = Fact(
                    key=proposal["key"],
                    value=value,
                    assertion=proposal["assertion"],
                    source=source,
                    turn_id=turn_id,
                    evidence=proposal["evidence"],
                )
                state.record(fact)
                self.events.append(
                    Event(
                        state.session_id,
                        "fact.recorded",
                        fact.to_dict(),
                        turn_id=turn_id,
                    )
                )
                self._track_incidental(state, proposal, active_before)
                if state.closed_reason != "emergency_route":
                    state.closed_reason = None
            for key in declined_keys:
                if key in state.facts:
                    self.events.append(
                        Event(
                            state.session_id,
                            "fact.declined",
                            {"fact_key": key},
                            turn_id=turn_id,
                        )
                    )
            self._sync_task_requests(state, text, turn_id)
            active = next(
                (q for q in self.content.questions if q.id == state.active_question),
                None,
            )
            if active and any(item["key"] == active.fact_key for item in proposals):
                state.active_question = None
                state.active_question_is_clarification = False
            state.risk = self.triage.assess(state, text)
            self.events.append(
                Event(
                    state.session_id,
                    "triage.assessed",
                    state.risk.to_dict(),
                    turn_id=turn_id,
                )
            )
            if rejected:
                return {
                    "ok": False,
                    "partial": True,
                    "recorded": [p["key"] for p in proposals],
                    "rejected": [
                        {
                            "key": verdict["key"],
                            "reason": verdict["reason"],
                            "guidance": verdict["guidance"],
                        }
                        for verdict in rejected
                    ],
                    "next": (
                        "已通过项已写入，不要重复提议；只需修正 rejected 中的"
                        "字段后重新提议 record_facts（只含修正项）。"
                    ),
                    "case": self._context(state, text),
                }
            return {"ok": True, "case": self._context(state, text)}
        if tool in {"inspect_case", "list_questions"}:
            return {"ok": True, "case": self._context(state, text)}
        if tool == "ask_question":
            if (
                state.pending_self_checks
                and not state.self_check_conversation
                and not self._self_check_block_reason(state)
            ):
                raise ProposalError("患者请求的图示任务已满足条件，应先处理待办")
            question = self.content.question(args["question_id"])
            if question.fact_key in state.facts:
                raise ProposalError("已记录这个字段；需要核实时使用 clarify")
            if not self.content.question_applies_in_state(question, state):
                raise ProposalError("问题不适用于当前主诉")
            if not self._fact_gates_pass(state, question):
                raise ProposalError("该问题已被当前已知事实排除（如性别或年龄范围不适用）")
            # Hypothesis-order check (P0): the model may phrase the
            # question, but what to ask next is the ranker's call. The
            # existing 6-step self-correction loop re-proposes on this
            # error. Exempt: clarification rounds (the clarify tool has
            # its own branch below; here a clarification round is
            # flagged by active_question_is_clarification), follow-ups
            # of the active question, and pending emergency
            # clarifications. The pick must come from the same validated
            # candidate set, otherwise the check passes through rather
            # than risk a proposal loop the model cannot exit.
            ranker = getattr(self, "ranker", None)
            if ranker is not None and not state.active_question_is_clarification:
                chief = str(state.get("chief_complaint", "") or "")
                candidates = self._candidate_questions(state, chief)
                candidate_ids = {item.id for item in candidates}
                pick = ranker.choose(state, candidates)
                if (
                    pick is not None
                    and pick in candidate_ids
                    and args["question_id"] in candidate_ids
                    and args["question_id"] != state.active_question
                    and self._emergency_clarification_pending(state) is None
                    and args["question_id"] != pick
                ):
                    raise ProposalError(
                        f"按假设排序应先问 {pick}，当前提议 {args['question_id']} 暂缓"
                    )
            return self._ask(state, question, "根据本轮证据与尚未处理的缺口选择下一问")
        if tool == "ask_patient":
            if state.self_check_conversation:
                raise ProposalError("图示交互进行中；先用固定问题澄清或接续当前图示")
            if (
                state.pending_self_checks
                and not state.self_check_conversation
                and not self._self_check_block_reason(state)
            ):
                raise ProposalError("患者请求的图示任务已满足条件，应先处理待办")
            spec = validate_adaptive_question(
                args,
                allowed_topics={question.fact_key for question in self.content.questions},
            )
            # B route: a model-written question must carry its own bridging
            # sentence plus a "why ask" half-sentence. Enforced only when an
            # anchorable fact exists (an opening round has nothing to anchor
            # on, and forcing it would loop the self-correction); the host
            # A-route bridge is the fallback and never stacks on a question
            # that already self-bridges (SELF_BRIDGED_OPENERS).
            if has_anchorable_fact(state.facts):
                has_bridge, has_why = model_question_bridging_status(spec["question"])
                if not (has_bridge and has_why):
                    raise ProposalError(MODEL_BRIDGING_REPAIR_HINT)
            question = next(
                item
                for item in self.content.questions
                if item.fact_key == spec["topic"]
            )
            # Topic legality is the registered field set, not the dynamic
            # candidate list: a colloquial chief complaint may never have
            # candidated the field that matters most (live-eval cases 3/6),
            # and an already-recorded field may still need a targeted
            # follow-up — its answer then lands via correction=true, which
            # the context flags (active_question_field_already_recorded).
            # Fact-dependent exclusions still apply: a field excluded by
            # known facts (e.g. pregnancy once sex is male) is not askable.
            if not self._fact_gates_pass(state, question):
                raise ProposalError(
                    "该字段已被当前已知事实排除（如性别或年龄范围不适用），请改问其他主题"
                )
            # Batch C BURDEN (D-01/P-02): a topic whose fixed question
            # was already asked AND whose fact is already recorded
            # (including unknown) must not be re-asked as a fresh model
            # question — that is the verbatim-repeat loop ("刚才说过了").
            # Targeted follow-ups on merely recorded, never-asked topics
            # stay allowed (pinned by
            # test_recorded_topic_allows_targeted_follow_up), and a
            # genuine re-ask stays possible through clarify, which is
            # capped at two per topic.
            if (
                question.id in state.asked_questions
                and spec["topic"] in state.facts
            ):
                raise ProposalError(
                    "该主题已经问过且已有记录，不得原样重问；"
                    "如需新信息请用 clarify，并先说明还不够区分什么，"
                    "追问所得的否认项须落账进同一事实键。"
                )
            state.phase = question.phase
            state.active_question = question.id
            state.active_question_is_clarification = False
            state.active_task = None
            if question.id not in state.asked_questions:
                state.asked_questions.append(question.id)
            # J-01: a model-defined decline option must reach the patient
            # view as a quick reply, not only inside adaptive_question
            # (the live-eval patient view reads quick_replies). Only
            # decline-like, single-mode, detail-free options are mirrored:
            # mirroring every option would duplicate the question pane's
            # adaptive choices in the composer; the decline button is the
            # one that must never be unreachable.
            decline_replies = [
                QuickReply(item["label"], f"adaptive_single:{item['id']}")
                for item in spec["options"]
                if spec["selection_mode"] == "single"
                and not item.get("requires_detail")
                and any(
                    phrase in item["label"]
                    for phrase in ("不想说", "不说", "拒绝", "不方便")
                )
            ]
            # Batch C BURDEN render layer (Y-10/O-06/K-04): the delivered
            # text, not just the decision, is what the patient reads.
            # Detail composites drop already-denied items; a screening
            # question gains the registered item list; and a
            # multi-option question whose text points at "以下这些" but
            # lists no option must list its options in the text, so the
            # option set is visible somewhere (quick_replies or text)
            # instead of dangling as K-04's empty quick_replies did.
            display_question = spec["question"]
            if spec["topic"] in DETAIL_FACT_KEYS:
                display_question = _strip_denied_items(
                    display_question, state.facts
                )
            if spec["topic"] == "emergency_screen":
                display_question = self._ensure_screening_list(display_question)
            labels = [
                item["label"] for item in spec["options"] if item.get("label")
            ]
            if (
                labels
                and any(word in display_question for word in ("以下", "下列"))
                and not any(label in display_question for label in labels)
            ):
                display_question = (
                    display_question + "（选项：" + "、".join(labels) + "）"
                )
            return AgentReply(
                display_question,
                question.phase,
                kind="question",
                question_id=question.id,
                adaptive_question=spec,
                quick_replies=decline_replies,
                developer_trace={
                    "reason": "模型根据当前病例生成可变数量的受控患者选项",
                    "content_version": self.content.versions["questions"],
                },
            )
        if tool == "clarify":
            topic, message = args.get("topic"), args.get("message")
            fields = {q.fact_key for q in self.content.questions}
            frame = state.self_check_conversation
            location_topic = bool(
                frame
                and frame["task"]["id"] == "body_location"
                and topic == frame["task"]["result_key"]
            )
            if (
                (topic not in fields and not location_topic)
                or not isinstance(message, str)
                or not message.strip()
                or not 2 <= len(message) <= 160
            ):
                raise ProposalError("澄清需要注册主题和 2 到 160 字的简短问题")
            if state.followup_counts.get(topic, 0) >= 2:
                raise ProposalError("该主题已澄清两次；请保留未知或改问其他主题")
            prohibited = ("毫克", "mg", "服用", "确诊", "可以排除", "用力按", "屏住呼吸")
            if any(p in message for p in prohibited):
                raise ProposalError("澄清不能生成处方、排除性结论或自查动作")
            # Batch C BURDEN (O-06): a completed screen is not re-asked
            # verbatim. A supplement for uncovered items stays possible,
            # but it must say so ("补问"/"新增") and ask only the new
            # items — the render layer below still attaches the list.
            if (
                topic == "emergency_screen"
                and topic in state.facts
                and not any(word in message for word in ("补问", "新增"))
            ):
                raise ProposalError(
                    "急症筛查已有记录，不得原样重问；"
                    "补问未覆盖项目时请说明是补问并只问新增项目。"
                )
            # B route, same discipline as ask_patient. The body-location
            # task path is exempt: its messages are short task prompts
            # (<=80 chars) where a bridging sentence would break the format.
            if not location_topic and has_anchorable_fact(state.facts):
                has_bridge, has_why = model_question_bridging_status(message)
                if not (has_bridge and has_why):
                    raise ProposalError(MODEL_BRIDGING_REPAIR_HINT)
            if location_topic:
                if len(message) > 80:
                    raise ProposalError("定位补问最多80字，保持一个简短问题。")
                reply = self._clarify_location_task(
                    state, message, args.get("answer_fields")
                )
                state.followup_counts[topic] = state.followup_counts.get(topic, 0) + 1
                return reply
            if "answer_fields" in args:
                raise ProposalError("answer_fields只适用于当前部位定位任务。")
            state.followup_counts[topic] = state.followup_counts.get(topic, 0) + 1
            state.active_question_is_clarification = True
            state.active_question = next(
                q.id for q in self.content.questions if q.fact_key == topic
            )
            state.phase = Phase.HISTORY
            display_message = message
            if topic in DETAIL_FACT_KEYS:
                display_message = _strip_denied_items(display_message, state.facts)
            if topic == "emergency_screen":
                display_message = self._ensure_screening_list(display_message)
            return AgentReply(
                display_message, state.phase, question_id=state.active_question
            )
        if tool == "show_self_check":
            if state.self_check_conversation and state.self_check_conversation["task"][
                "id"
            ] == args.get("task_id"):
                frame_id = state.self_check_conversation["task"]["id"]
                if (
                    self._self_check_suspend_count(
                        state,
                        frame_id,
                        state.self_check_conversation["task"].get(
                            "interaction_id"
                        ),
                    )
                    >= SELF_CHECK_SUSPEND_CAP
                ):
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
                            turn_id=turn_id,
                        )
                    )
                    return self._plan_next(state)
                return self._resume_task_conversation(state)
            if state.self_check_conversation:
                raise ProposalError("先接续原图示任务或澄清病史，另一图示请求可以登记为待办。")
            task = next(
                (
                    t
                    for t in self.content.applicable_tasks(
                        str(state.get("chief_complaint", ""))
                    )
                    if args.get("task_id") == t["id"]
                ),
                None,
            )
            if task is None or task["result_key"] in state.facts:
                raise ProposalError("任务未注册、不适用或已经尝试")
            blocked = self._self_check_block_reason(state)
            if blocked:
                raise ProposalError(blocked)
            return self._offer_task(
                state, str(state.get("chief_complaint", "")), task["id"]
            )
        if tool == "review_summary":
            if state.self_check_conversation:
                raise ProposalError("原图示任务尚未处理。可show_self_check接续，由患者完成或跳过；也可先澄清病史。")
            abstain_reason = args.get("abstain_reason")
            if abstain_reason is not None and (
                not isinstance(abstain_reason, str)
                or not abstain_reason.strip()
                or len(abstain_reason.strip()) > 200
            ):
                raise ProposalError("abstain_reason 须为 1 到 200 字的字符串，说明主诉通路为何不适用探明")
            context = self._context(state, text)
            unexplored = [
                entry
                for entry in context.get("pathway_exploration", [])
                if not entry["explored"]
            ]
            # The gate guards the first close decision only: re-summarizing
            # a case that already reached the summary/closed stage (e.g. a
            # fact correction replay) is not a silent close.
            already_summarized = state.phase in {
                Phase.SUMMARY,
                Phase.CLOSED,
            } or bool(state.conclusion)
            if unexplored and not already_summarized and not abstain_reason:
                raise self._unexplored_pathway_error(unexplored)
            missing = context["missing_required"]
            if unexplored and (already_summarized or abstain_reason):
                # The abstention (or the earlier close, when re-summarizing)
                # also excuses the unexplored pathways' own detail fields
                # from the required-fields gate — the stated reason is
                # precisely "this pathway's details will not be probed".
                # Every other unattempted field still blocks as before.
                excused = {
                    fact_key
                    for entry in unexplored
                    for fact_key in entry["detail_fields"]
                }
                missing = [key for key in missing if key not in excused]
            if missing:
                # J-01: "尚未尝试" must not mislead the model into
                # re-asking a field that was asked but never landed
                # (e.g. a refused field whose recording was rejected).
                # The prefix stays verbatim for existing assertions.
                labelled = [
                    (
                        f"{key}（已提问但未落账）"
                        if self._questions_by_fact[key].id
                        in state.asked_questions
                        else key
                    )
                    for key in missing
                ]
                raise ProposalError("必要字段尚未尝试：" + ",".join(labelled))
            # Batch C BURDEN (G-07): completeness check before the first
            # summary draft. A recorded detail fact can still lack the
            # chief complaint's key items (constipation without straining
            # effort or weight change); those are asked first instead of
            # reaching the draft for the patient to correct. Abstention
            # does not excuse this gate: it excuses unexplored pathways,
            # not missing sub-items of a recorded detail fact.
            gaps = self._completeness_gaps(state)
            if gaps:
                raise ProposalError(
                    "摘要确认前完整性检查未通过：主诉关键项尚未记录："
                    + "、".join(gaps)
                    + "。请先追问这些项再出摘要首版，不得依赖患者主动更正。"
                )
            if unexplored and not already_summarized and abstain_reason:
                self.events.append(
                    Event(
                        state.session_id,
                        "review.abstained",
                        {
                            "pathways": [entry["pathway"] for entry in unexplored],
                            "detail_fields": sorted(
                                {
                                    fact_key
                                    for entry in unexplored
                                    for fact_key in entry["detail_fields"]
                                }
                            ),
                            "reason": abstain_reason.strip(),
                        },
                        turn_id=turn_id,
                    )
                )
            reply = super()._finish(state)
            state.phase = Phase.SUMMARY
            state.closed_reason = None
            state.active_question = None
            reply.phase = Phase.SUMMARY
            reply.message = "我把本次情况整理在下面。请核对是否准确；需要改动的地方可以直接告诉我。"
            if state.pending_self_checks:
                reply.message += "你提出的图示请求尚未执行，未完成的筛查不能当作阴性。"
            reply.quick_replies = [
                QuickReply("确认摘要并结束本次问诊", "confirm_summary"),
                QuickReply("我需要更正", "correct_summary"),
            ]
            return reply
        raise ProposalError("工具未注册，不能执行：" + str(tool))
