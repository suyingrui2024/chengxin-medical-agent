"""Mixin: core for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEngineCore:
    def __init__(
        self, *args, backend: DecisionBackend, subagent_coordinator=None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.backend = backend
        # Optional medagent.subagents.SubagentCoordinator; None keeps the
        # spawn_subagent tool unavailable (the server assembles and injects
        # it; other construction paths stay coordinator-free by default).
        self.subagent_coordinator = subagent_coordinator
        self._round_spawn_counts: Dict[str, int] = {}
        # session_id -> screening fact keys recorded only incidentally
        # (see SCREENING_FIELDS); advisory context signal, never persisted.
        self._incidental_records: Dict[str, set] = {}
        self._active_turns = {}
        self._decision_waiter = DecisionWaiter()
        # Pathway close-gate structures (live-eval case 3: a triggered
        # chief-complaint pathway whose details were never probed must not
        # be silently summarized away). Triggering reuses the reasoning
        # core's own pathway definitions and proposer — no second keyword
        # table. A pathway's detail facts are derived from the DSL: fact
        # keys cited by the pathway's hypotheses in support/refute
        # conditions that are collected by a chief-complaint-gated
        # question (the *_details-style fields). Generic background fields
        # (trajectory, severity, relevant_history…) are cited by many
        # pathways and never count as "the pathway was probed".
        self._pathways = load_pathways()
        self._pathway_proposer = RuleBasedProposer(
            pathways=self._pathways,
            question_fact_keys={q.id: q.fact_key for q in self.content.questions},
        )
        self._questions_by_fact = {}
        for question in self.content.questions:
            self._questions_by_fact.setdefault(question.fact_key, question)
        gated_fact_keys = {
            q.fact_key
            for q in self.content.questions
            if q.applies_when.get("chief_complaint_contains_any")
        }
        self._pathway_detail_facts: Dict[str, Any] = {}
        for pathway in self._pathways:
            cited = {
                condition["fact_key"]
                for hypothesis in pathway.get("hypotheses", [])
                for condition in [
                    *hypothesis.get("support", []),
                    *hypothesis.get("refute", []),
                ]
            }
            self._pathway_detail_facts[pathway["id"]] = sorted(
                fact_key
                for fact_key in cited
                if fact_key != "chief_complaint" and fact_key in gated_fact_keys
            )
        self.prompt = (
            resources.files("medagent.content")
            .joinpath("agent_prompt.txt")
            .read_text(encoding="utf-8")
        )
        # Immutable per-engine catalog precedes ALL case-specific data. This is
        # provider KV-prefix reuse, not a cache of patient answers or decisions.
        # The base catalog always carries the full tool list; which tools
        # are *advertised* to the model is resolved lazily at message-build
        # time (see _effective_static_context): production assembly injects
        # the subagent coordinator AFTER construction (server startup
        # setattr, Workspace._attach_subagents on settings apply), so a
        # tool list frozen here would advertise spawn_subagent exactly
        # never in the real product — and would keep advertising it if
        # the coordinator were later detached or switched off.
        self.static_context = {
            "tools": TOOL_DEFINITIONS,
            "fact_schema": [
                {
                    "key": q.fact_key,
                    "type": q.input,
                    "options": [o.value for o in q.options],
                    "value_contract": self._value_contract(q),
                }
                for q in self.content.questions
            ],
            "question_catalog": [
                {
                    "id": q.id,
                    "field": q.fact_key,
                    "prompt": q.prompt,
                    "applies_when": q.applies_when,
                }
                for q in self.content.questions
            ],
            "self_check_catalog": [
                {
                    key: task[key]
                    for key in (
                        "id",
                        "title",
                        "purpose",
                        "result_key",
                        "input_kind",
                        "applies_when",
                        "review_status",
                    )
                }
                | (
                    {"answer_contract": task["answer_contract"]}
                    if task.get("answer_contract")
                    else {}
                )
                for task in self.content.tasks.values()
            ],
            "available_capabilities": [
                _capability_advertisement(item)
                for item in self.content.registry.descriptions()
                if item.get("patient_form")
            ],
            # Shared once instead of repeated on every capability entry.
            "capability_note": (
                "调用 run_capability 时只提供 id 与 evidence，不要提供 input；"
                "量表答案与公式数值只能由患者在输入卡中填写。"
                "所有能力均为工程验证，未经临床审核，结果不构成诊断。"
            ),
            "adaptive_question_risk_tags": {
                name: list(cues) for name, cues in RISK_TAG_CUES.items()
            },
            "content_versions": self.content.versions,
        }
        self._prompt_variants: Dict[bool, tuple] = {}
        self.system_prompt, self.prefix_sha256 = self._current_prefix()

    def _subagent_mode_for(self, task_kind) -> str:
        """Effective subagent mode for one task kind, resolved live.

        Current coordinators resolve per-kind overrides through
        ``mode_for()`` (an override wins; kinds without one fall back
        to the coordinator's global mode). Older coordinators — and the
        test doubles written against them — carry only the global
        ``mode``, which is then the effective mode for every kind. No
        coordinator at all reads as "off".
        """
        coordinator = self.subagent_coordinator
        if coordinator is None:
            return "off"
        mode_for = getattr(coordinator, "mode_for", None)
        if callable(mode_for):
            return mode_for(task_kind)
        return getattr(coordinator, "mode", "off")

    def _spawn_advertised(self) -> bool:
        """Whether spawn_subagent may be advertised *right now*.

        Advertise spawn_subagent only when a coordinator is actually
        wired in and at least one *model-proposed* kind is enabled:
        evidence_lookup or second_opinion. case_digest is triggered by
        the controller and followup_batch by the server, so enabling
        only those must not put the tool in front of the model. Listing
        an unavailable tool invites proposals that can only fail closed
        at execution time (the _execute kind whitelist below).
        """
        return any(
            self._subagent_mode_for(kind) != "off"
            for kind in MODEL_SPAWN_TASK_KINDS
        )

    def _effective_static_context(self):
        """The catalog actually serialized into the system prompt.

        Identical to ``static_context`` except the tool list, which is
        resolved against the live coordinator (see the __init__ note):
        the base catalog is immutable content, the advertisement is not.
        """
        if self._spawn_advertised():
            return self.static_context
        context = dict(self.static_context)
        context["tools"] = [
            tool
            for tool in self.static_context["tools"]
            if tool.get("name") != "spawn_subagent"
        ]
        return context

    def _current_prefix(self):
        """(system_prompt, sha256) for the currently advertised catalog.

        Exactly two variants exist per engine (spawn advertised or not);
        each is built once and cached, so the prefix stays byte-stable
        within a variant — the property provider KV-prefix reuse relies
        on. ``system_prompt``/``prefix_sha256`` are refreshed to the live
        variant on every call, so the agent.started event record and
        offline evaluators reading those attributes see the prefix the
        model is actually being sent, not the construction-time one.
        """
        advertised = self._spawn_advertised()
        variant = self._prompt_variants.get(advertised)
        if variant is None:
            prompt = (
                self.prompt
                + "\n\n以下是本会谈固定的工具与内容目录（JSON）。动态输入中的候选范围优先。\n"
                + self._json(self._effective_static_context())
            )
            variant = (prompt, hashlib.sha256(prompt.encode()).hexdigest())
            self._prompt_variants[advertised] = variant
        self.system_prompt, self.prefix_sha256 = variant
        return variant

    @staticmethod
    def _json(value):
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @staticmethod
    def _value_contract(question):
        if question.input == "age":
            present = {
                "type": "number",
                "unit": "years",
                "minimum": 0,
                "maximum": 120,
                "examples": [32, 0.667],
            }
        elif question.input == "choice":
            present = {
                "type": "string",
                "enum": [o.value for o in question.options if o.value != "unknown"],
            }
        elif question.input == "yes_no_unknown":
            present = {"const": True}
        elif question.input == "severity":
            present = {"types": ["number", "string"], "numeric_range": [0, 10]}
        else:
            present = {"type": "string", "minLength": 1, "maxLength": 2000}
        return {"present": present, "absent_or_unknown_value": None}

    def create_session(self, request_id=None):
        trace_id = str(uuid4())
        state = self._create_state(trace_id, request_id)
        try:
            state.active_question = "chief_complaint"
            state.asked_questions = ["chief_complaint"]
            reply = self._ask(
                state, self.content.question("chief_complaint"), "先听取主诉，自然叙述后再补关键缺口"
            )
            reply.message = "你好，这次是你自己还是替家人咨询？请先说说最困扰的问题，以及大约从什么时候开始。"
            self._record_reply(state, reply, "session_start", "开放式开场")
            self.events.append(
                Event(
                    state.session_id,
                    "agent.started",
                    {
                        "backend": self.backend.name,
                        "reply": reply.to_dict(),
                        "policy_version": "medical-tools-v0.11.17",
                        "prompt_sha256": self._current_prefix()[1],
                        "prompt_layout": "static-catalog-prefix/v1",
                    },
                )
            )
        finally:
            self.events.clear_trace(state.session_id)
        return state, reply

    def _ask(self, state: CaseState, question: Question, reason: str) -> AgentReply:
        reply = super()._ask(state, question, reason)
        if question.fact_key == "age_years" and not self._subject_is_child(state):
            reply.message = self._ADULT_AGE_PROMPT
        # Batch C BURDEN (Y-10): a fixed composite prompt must not
        # re-list items the patient already denied. The filter runs
        # only on pathway-detail composites: the emergency-screen list
        # is a safety list and is never trimmed, whatever was denied.
        if question.fact_key in DETAIL_FACT_KEYS:
            reply.message = _strip_denied_items(reply.message, state.facts)
        return reply

    def _screening_list_text(self) -> Optional[str]:
        """The registered emergency-screen item list, from questions.json.

        The list is read from the registered prompt at render time, never
        restated from a second copy: O-06's list-less screening question
        ("以下任一情况" with no items) is repaired by appending exactly
        this registry text.
        """
        try:
            prompt = self.content.question("emergency_screen").prompt
        except Exception:
            return None
        if "：" in prompt:
            items = prompt.split("：", 1)[1].strip().rstrip("？?")
            return items or None
        return None

    def _ensure_screening_list(self, message: str) -> str:
        """Append the screening item list when the text names none of it."""
        items = self._screening_list_text()
        if not items:
            return message
        alternatives: List[str] = []
        for item in re.split(r"[、，,]", items):
            alternatives.extend(part for part in item.split("或") if len(part) >= 2)
        if any(alt in message for alt in alternatives):
            return message
        return message + f"（具体包括：{items}）"

    def _completeness_gaps(self, state: CaseState) -> List[str]:
        """Chief-complaint key items a recorded detail fact still lacks.

        Batch C BURDEN (G-07): a constipation case whose gi_details was
        recorded but never mentions straining effort or weight change
        must not reach its first summary draft on the patient's own
        correction. The check is a completeness gate, not a clinical
        threshold: it only names which recorded-detail sub-items are
        still missing so they can be asked first.
        """
        chief_texts = [
            text for text in self.content.chief_complaint_texts(state) if text
        ]
        chief = " ".join(chief_texts)
        if not any(
            term in chief
            for term in ("便秘", "排便困难", "大便干", "排便费力", "拉不出", "解不出")
        ):
            return []
        detail = state.facts.get("gi_details")
        if detail is None:
            return []
        text = ""
        if isinstance(detail.value, str):
            text += detail.value
        if detail.evidence:
            text += str(detail.evidence)
        gaps: List[str] = []
        if not any(term in text for term in ("费力", "费劲", "用力", "困难")):
            gaps.append("排便费力程度")
        if not any(
            term in text
            for term in ("体重", "瘦", "胖", "轻了", "重了", "没变", "不变", "下降", "增加")
        ):
            gaps.append("体重变化")
        return gaps

    @staticmethod
    def _subject_is_child(state: CaseState) -> bool:
        if state.get("consult_for") == "child":
            return True
        profile = subject_profile(state)
        reference = (profile or {}).get("reference") or {}
        attributes = reference.get("attributes") or []
        return bool(attributes) and attributes[0] == "child"

    def _interrupt_or_reject(self, state, text, explicit_help=False):
        active = self._active_turns.get(state.session_id)
        if active and not active.cancelled.is_set():
            if not explicit_help and not self.triage.scan_text(text):
                raise ConsultationBusyError("上一条信息正在处理，这条补充尚未保存。请稍后再提交。")
            active.cancelled.set()
            self.events.append(
                Event(
                    state.session_id,
                    "agent.turn_preempted",
                    {"reason": "emergency_input", "decision_turn": active.identity},
                    turn_id=active.turn_id or None,
                )
            )

    def submit(self, session_id, user_input):
        state = self.state(session_id)
        with self._session_locks[session_id]:
            self._interrupt_or_reject(
                state,
                self._location_input_text(user_input),
                user_input.choice_value == "request_emergency_help",
            )
            turn = DecisionTurn()
            self._active_turns[session_id] = turn
            trace_id = str(uuid4())
            decision_id = str(uuid4())
            self.events.bind_trace(
                session_id,
                trace_id,
                user_input.request_id,
                decision_id,
            )
            try:
                self._observe_knowledge(state, user_input)
                return self._submit(state, user_input)
            finally:
                self.events.clear_trace(session_id)
                if self._active_turns.get(session_id) is turn:
                    self._active_turns.pop(session_id, None)

    def _observe_knowledge(self, state: CaseState, user_input) -> None:
        """Record what the knowledge base recognised, without acting on it.

        This is an observation, not a decision input: nothing downstream
        reads ``kg.normalized``. It exists so that, once real transcripts
        have run through it, we can measure how often the graph saw a
        concept the pattern layer missed — and only then decide whether to
        wire it in further. Any failure here is swallowed: a missing or
        corrupt knowledge base must never cost a patient their turn.
        """
        try:
            text = getattr(user_input, "text", "") or ""
            matches = kg_runtime.observe(text)
            if matches:
                self.events.append(
                    Event(state.session_id, "kg.normalized", {"matches": matches})
                )
            # A clarification is only ever proposed when the triage layer
            # found nothing. Asking "胸口还是上腹" while a chest-pain flag
            # is already firing would put a question mark in front of an
            # emergency.
            flags = self.triage.scan_text(text) if text else []
            clarification = should_clarify(text, flags)
            if clarification:
                self.events.append(
                    Event(
                        state.session_id,
                        "kg.ambiguous",
                        {
                            "phrase": clarification.phrase,
                            "options": list(clarification.options),
                            "reason": clarification.reason,
                        },
                    )
                )
        except Exception:  # noqa: BLE001 - observation must never break a turn
            return

    def _emergency_clarification_denied_before(
        self, state: CaseState, text: str
    ) -> bool:
        """True when this turn's bare-signal group was already denied.

        C-10: a clarification round denied once must not be re-asked
        verbatim when the patient later paraphrases the same complaint.
        The gate itself lives in the base engine (consultation.py,
        untouched); this is the adaptive engine's pre-gate memory check
        at its own clarification call sites. Labels scope the memory to
        the same danger-signal group: a turn whose labels are a subset
        of a denied round's labels is the same group; a turn with a
        label outside every denied set is a new group and may still
        clarify. A denied record without labels (written before labels
        were stored) falls back to presence: a denial did happen, and
        the clarification template is a single fixed group.
        """
        snapshot = state.runtime_snapshot
        denied = snapshot.get("emergency_clarification_denied")
        label_sets: List[set] = []
        history = snapshot.get("emergency_clarification_denied_labels")
        if isinstance(history, list):
            label_sets.extend(
                set(entry) for entry in history if isinstance(entry, list)
            )
        if isinstance(denied, dict) and isinstance(
            denied.get("labels"), list
        ):
            label_sets.append(set(denied["labels"]))
        if not label_sets:
            return isinstance(denied, dict)
        try:
            current = {hit.label for hit in self.triage.scan_detailed(text)}
        except Exception:
            return True
        if not current:
            return False
        return any(current <= labels for labels in label_sets)

    def _confirmed_summary_reply(self, state, turn_id):
        """Shared user-confirmed summary close (button or free text)."""
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.active_question = None
        reply = AgentReply(
            "你已确认本次摘要，会谈现已结束。可以把它提供给接诊医生；情况有变化时请重新评估。",
            Phase.CLOSED,
            kind="closed",
            summary=self._summary_payload(
                state, review_status="confirmed_by_user"
            ),
        )
        # Parity with the deterministic pipeline's confirm handler:
        # a user-confirmed close also derives the follow-up plan.
        self._schedule_follow_up(state, reply, turn_id)
        return reply

    @staticmethod
    def _is_declined_proposal(state, text, item) -> bool:
        """Whether a record_facts item is a refusal, not an unknown.

        Narrow by construction: assertion unknown, value null, evidence a
        verbatim refusal quote from this turn, and the field not already
        recorded (unless an explicit correction). Anything else keeps
        the validator's existing unknown rules.
        """
        key = item.get("key")
        if not isinstance(key, str):
            return False
        if item.get("assertion") != "unknown":
            return False
        if item.get("value") is not None:
            return False
        evidence = item.get("evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return False
        if evidence not in text:
            return False
        if not is_refusal_text(evidence):
            return False
        if key in state.facts and not item.get("correction"):
            return False
        return True

    def _prepare_adaptive_answer(self, state, user_input, text):
        choice = user_input.choice_value
        if not choice or not choice.startswith("adaptive_"):
            return None
        latest = self._latest_replies.get(state.session_id)
        if not latest or not latest.adaptive_question:
            raise StaleActionError("动态问题已经更新，请按当前问题重新回答")
        try:
            return project_selected_answer(latest.adaptive_question, choice, text)
        except AdaptiveQuestionError as exc:
            raise StaleActionError(str(exc)) from exc

    @staticmethod
    def _fact_gates_pass(state: CaseState, question) -> bool:
        """Fact-dependent eligibility, evaluated at candidate time.

        Shared implementation: ``knowledge.question_fact_gates_pass``
        (the deterministic engine applies the same gates). The gate
        only fires on known facts — an unknown age keeps the question
        eligible.
        """
        return question_fact_gates_pass(question, state.facts)

    def _track_incidental(self, state: CaseState, proposal, active_before) -> None:
        """Flag screening facts that were only mentioned in passing.

        A screening field (SCREENING_FIELDS) recorded while a *different*
        question was active — or none — was volunteered, not screened. The
        fact still counts as recorded (it leaves missing_required), but the
        context keeps flagging it via ``incidentally_recorded`` so the model
        can still run the formal screen. Formally answering the field's own
        question, or a deliberate correction, clears the flag.
        """
        key = proposal["key"]
        if key not in SCREENING_FIELDS:
            return
        marks = self._incidental_records.setdefault(state.session_id, set())
        if proposal.get("correction") or (
            active_before is not None and active_before.fact_key == key
        ):
            marks.discard(key)
        else:
            marks.add(key)

    def _candidate_questions(self, state: CaseState, chief: str):
        return [
            q
            for q in self.content.questions
            if q.fact_key not in state.facts
            and self.content.question_applies_in_state(q, state)
            and self._fact_gates_pass(state, q)
        ]

    def _messages(self, state, text):
        return [
            {"role": "system", "content": self._current_prefix()[0]},
            {"role": "user", "content": self._json(self._context(state, text))},
        ]

    def _deliver(self, state: CaseState, reply: AgentReply, turn_id: str) -> AgentReply:
        # The resume affordance is not offered past the suspend cap:
        # without the button, the stale-action check also rejects a
        # fabricated resume choice for the capped frame.
        if (
            state.self_check_conversation
            and reply.kind == "question"
            and self._self_check_suspend_count(
                state,
                state.self_check_conversation["task"]["id"],
                state.self_check_conversation["task"].get("interaction_id"),
            )
            < SELF_CHECK_SUSPEND_CAP
        ):
            frame = state.self_check_conversation
            choice = f"resume_self_check:{frame['id']}"
            if not any(item.value == choice for item in reply.quick_replies):
                reply.quick_replies.append(
                    QuickReply("继续" + frame["task"]["title"], choice)
                )
        if reply.kind == "service_error":
            reply.quick_replies = [QuickReply("重试这一轮", "retry_turn")]
        elif reply.kind != "subject_change":
            state.pending_input = None
        self._record_reply(state, reply, turn_id, "受约束工具执行后的患者可见动作")
        return reply
