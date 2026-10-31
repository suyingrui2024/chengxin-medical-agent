"""Mixin: branch for SubagentCoordinator."""
from ._preamble import *  # noqa: F401,F403


class CoordinatorBranch:
    def _run_spawn(
        self,
        state: CaseState,
        proposal: Dict[str, Any],
        session_id: str,
        cancelled,
        view_extras: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        kind = proposal.get("task_kind") if isinstance(proposal, dict) else None
        effective_mode = self.mode_for(kind)
        if effective_mode == "off":
            return {"ok": False, "status": "disabled"}
        if not self._backend_available():
            return {"ok": False, "status": "unavailable"}
        if self._daily_usage() >= self.daily_token_cap:
            self.events.append(
                Event(
                    session_id,
                    "budget.exceeded",
                    {
                        "scope": "daily_tokens",
                        "cap": self.daily_token_cap,
                        "mode": effective_mode,
                    },
                )
            )
            return {"ok": False, "status": "daily_budget_exceeded"}
        if not isinstance(proposal, dict):
            return {"ok": False, "status": "invalid_proposal"}
        self.events.append(
            Event(
                session_id,
                "subagent.proposed",
                {"proposal": proposal, "mode": effective_mode},
            )
        )
        if kind not in KINDS:
            self.events.append(
                Event(
                    session_id,
                    "subagent.denied",
                    {"reason": "unknown_task_kind", "task_kind": kind},
                )
            )
            return {"ok": False, "status": "invalid_proposal"}
        raw_branches = proposal.get("branches")
        if not isinstance(raw_branches, list) or not raw_branches:
            return {"ok": False, "status": "invalid_proposal"}
        spawn_id = str(uuid4())
        mark = watermark(state)
        case_ref = hashlib.sha256(f"{session_id}:{spawn_id}".encode()).hexdigest()[
            :12
        ]
        workspace = ReasoningWorkspace.from_dict(state.reasoning)
        allowed_keys = _known_fact_keys(state)

        prepared = []
        for index, raw_branch in enumerate(raw_branches[:MAX_BRANCHES]):
            if not isinstance(raw_branch, dict):
                continue
            prepared.append(
                self._prepare_branch(
                    state,
                    proposal,
                    raw_branch,
                    index,
                    kind,
                    allowed_keys,
                    workspace,
                    case_ref,
                    view_extras,
                )
            )
        if not prepared:
            return {"ok": False, "status": "invalid_proposal"}
        if kind != "followup_batch":
            # Single-spawn aggregate envelope (design §5.5): all branches of
            # one in-consultation spawn share a 48k-token / 45s envelope,
            # first-come-first-served. Branches past the envelope settle as
            # budget_exhausted without running. followup_batch keeps its own
            # per-case + batch envelope instead.
            remaining = SPAWN_ENVELOPE_TOKENS
            for branch in prepared:
                budget = branch["budget"]
                budget["deadline_s"] = min(
                    budget["deadline_s"], SPAWN_ENVELOPE_DEADLINE_S
                )
                if remaining <= 0:
                    branch["force_status"] = "budget_exhausted"
                    continue
                budget["max_tokens"] = min(budget["max_tokens"], remaining)
                remaining -= budget["max_tokens"]
            if any("force_status" in branch for branch in prepared):
                self.events.append(
                    Event(
                        session_id,
                        "budget.exceeded",
                        {
                            "scope": "spawn_envelope",
                            "spawn_id": spawn_id,
                            "cap_tokens": SPAWN_ENVELOPE_TOKENS,
                        },
                    )
                )
        for branch in prepared:
            self.events.append(
                Event(
                    session_id,
                    "subagent.started",
                    {
                        "spawn_id": spawn_id,
                        "branch_id": branch["branch_id"],
                        "task_kind": kind,
                        "scope": branch["scope_summary"],
                        "scope_clamped": branch["scope_clamped"],
                        "tools_clamped": branch["tools_clamped"],
                        "budget": branch["budget"],
                        "watermark": mark,
                        "mode": effective_mode,
                    },
                )
            )
        started = monotonic()
        results: List[Optional[Dict[str, Any]]] = [None] * len(prepared)
        with ThreadPoolExecutor(max_workers=len(prepared)) as pool:
            futures = {}
            for position, branch in enumerate(prepared):
                if branch.get("force_status"):
                    results[position] = self._branch_shell(
                        branch, branch["force_status"]
                    )
                    continue
                futures[
                    pool.submit(
                        self._run_branch,
                        state,
                        branch,
                        session_id,
                        spawn_id,
                        cancelled,
                    )
                ] = position
            for future, position in futures.items():
                try:
                    results[position] = future.result(
                        timeout=prepared[position]["budget"]["deadline_s"] + 10
                    )
                except Exception as exc:
                    results[position] = self._branch_shell(
                        prepared[position],
                        "failed",
                        error_type=type(exc).__name__,
                    )
        # Staleness is judged once, at delivery (design §5.6): if the
        # case moved while branches ran, findings are withheld.
        stale = watermark(state) != mark
        branches_out = []
        for branch, result in zip(prepared, results):
            if result is None:
                result = self._branch_shell(branch, "failed")
            if stale and result.get("status") not in {
                "timeout",
                "cancelled",
                "failed",
            }:
                result = {
                    **result,
                    "status": "stale",
                    "conclusion": "",
                    "findings": [],
                    "sources": [],
                    "digest": None,
                    "followup": None,
                }
                self.events.append(
                    Event(
                        session_id,
                        "subagent.stale",
                        {
                            "spawn_id": spawn_id,
                            "branch_id": branch["branch_id"],
                            "task_kind": kind,
                        },
                    )
                )
            self.events.append(
                Event(
                    session_id,
                    "subagent.finished",
                    {
                        "level": "branch",
                        "spawn_id": spawn_id,
                        "branch_id": branch["branch_id"],
                        "task_kind": kind,
                        "status": result.get("status"),
                        "usage": result.get("usage"),
                        "duration_ms": result.get("usage", {}).get("duration_ms"),
                    },
                )
            )
            branches_out.append(result)
        usage_total = {
            key: sum(branch["usage"].get(key, 0) for branch in branches_out)
            for key in (
                "model_calls",
                "input_tokens",
                "output_tokens",
                "cache_hit_tokens",
                "total_tokens",
            )
        }
        wall_ms = max(0, round((monotonic() - started) * 1000))
        settled = sum(
            1
            for branch in branches_out
            if branch.get("status") in {"completed", "partial"}
        )
        self.events.append(
            Event(
                session_id,
                "subagent.finished",
                {
                    "level": "spawn",
                    "spawn_id": spawn_id,
                    "task_kind": kind,
                    "settled": f"{settled}/{len(branches_out)}",
                    "usage_total": usage_total,
                    "wall_ms": wall_ms,
                    "mode": effective_mode,
                },
            )
        )
        self._daily_add(usage_total.get("total_tokens", 0))
        return {
            "ok": True,
            "spawn_id": spawn_id,
            "mode": effective_mode,
            "task_kind": kind,
            "branches": branches_out,
            "usage_total": usage_total,
            "wall_ms": wall_ms,
            "settled": f"{settled}/{len(branches_out)}",
        }

    def _prepare_branch(
        self,
        state: CaseState,
        proposal: Dict[str, Any],
        raw_branch: Dict[str, Any],
        index: int,
        kind: str,
        allowed_keys: set,
        workspace: ReasoningWorkspace,
        case_ref: str,
        view_extras: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Clamp one branch proposal and build its ScopedCaseView."""
        tier = KINDS[kind]
        branch_id = str(raw_branch.get("branch_id") or f"b{index + 1}")
        scope = raw_branch.get("scope") if isinstance(raw_branch.get("scope"), dict) else {}
        clamped: List[str] = []

        requested_keys = scope.get("fact_keys")
        if not isinstance(requested_keys, list):
            requested_keys = sorted(state.facts.keys())
        kept_keys = []
        for key in requested_keys:
            if (
                isinstance(key, str)
                and key in allowed_keys
                and key in state.facts
                and key not in kept_keys
            ):
                kept_keys.append(key)
            elif isinstance(key, str):
                clamped.append(key)

        requested_hypotheses = scope.get("hypotheses")
        if not isinstance(requested_hypotheses, list):
            requested_hypotheses = []
        kept_hypotheses = []
        for hypothesis_id in requested_hypotheses:
            if (
                isinstance(hypothesis_id, str)
                and hypothesis_id in workspace.hypotheses
                and hypothesis_id not in kept_hypotheses
            ):
                kept_hypotheses.append(hypothesis_id)
            elif isinstance(hypothesis_id, str):
                clamped.append(f"hypothesis:{hypothesis_id}")

        transcript_tier = scope.get("transcript", "none")
        if transcript_tier not in {"none", "evidence_only", "recent_window"}:
            transcript_tier = "none"
        if transcript_tier == "recent_window" and kind != "case_digest":
            # Recent raw turns are a digest-only privilege (design §5.2).
            transcript_tier = "evidence_only"
            clamped.append("transcript:recent_window")

        kind_tools = set(_KIND_TOOLS[kind])
        tools_clamped: List[str] = []
        requested_tools = raw_branch.get("tools", proposal.get("tools"))
        if isinstance(requested_tools, list):
            for tool in requested_tools:
                if tool not in kind_tools:
                    tools_clamped.append(str(tool))
            effective_tools = (kind_tools & set(requested_tools)) | {
                "subagent_report"
            }
        else:
            effective_tools = set(kind_tools)

        budget = {
            "max_calls": tier["max_calls"],
            "max_tokens": tier["max_tokens"],
            "deadline_s": tier["deadline_s"],
        }
        raw_budget = raw_branch.get("budget", proposal.get("budget"))
        if isinstance(raw_budget, dict):
            for field_name in budget:
                value = raw_budget.get(field_name)
                if isinstance(value, (int, float)) and 0 < value < budget[field_name]:
                    budget[field_name] = value

        view = self._build_view(
            state,
            kind,
            raw_branch,
            case_ref,
            kept_keys,
            kept_hypotheses,
            workspace,
            transcript_tier,
            bool(scope.get("prior_case")),
            view_extras,
        )
        return {
            "branch_id": branch_id,
            "subagent_id": str(uuid4()),
            "objective": str(
                raw_branch.get("objective") or proposal.get("objective") or ""
            )[:200],
            "view": view,
            "scope_clamped": clamped,
            "tools_clamped": tools_clamped,
            "effective_tools": effective_tools,
            "budget": budget,
            "scope_summary": {
                "fact_keys": kept_keys,
                "hypotheses": kept_hypotheses,
                "transcript": transcript_tier,
                "prior_case": bool(scope.get("prior_case")),
            },
        }

    def _build_view(
        self,
        state: CaseState,
        kind: str,
        raw_branch: Dict[str, Any],
        case_ref: str,
        kept_keys: List[str],
        kept_hypotheses: List[str],
        workspace: ReasoningWorkspace,
        transcript_tier: str,
        want_prior: bool,
        view_extras: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        facts = {}
        for key in kept_keys:
            fact = state.facts[key]
            facts[key] = {
                "key": fact.key,
                "value": fact.value,
                "assertion": fact.assertion.value,
                "evidence": fact.evidence,
            }
        view: Dict[str, Any] = {
            "case_ref": case_ref,
            "task_kind": kind,
            "facts": facts,
            "hypotheses": [
                workspace.hypotheses[hypothesis_id].to_dict()
                for hypothesis_id in kept_hypotheses
            ],
            "content_excerpts": self._content_excerpts(kept_hypotheses),
        }
        # Blinding: a branch objective is free text written by the main
        # flow — the very author being blinded — so it must never reach
        # a second-opinion child, even when it carries no tendency on its
        # face (e.g. "主流程倾向急诊，请确认是否急诊" would otherwise
        # pass through verbatim). The structured task (task_kind, facts,
        # hypotheses, excerpts) still describes the job, so legitimate
        # task info survives.
        if kind != "second_opinion":
            view["objective"] = str(
                raw_branch.get("objective") or ""
            )[:200]
        if kind != "second_opinion":
            # Blinding is structural for second opinions: no conclusion,
            # no disposition-bearing fields ever enter their view.
            view["phase"] = state.phase.value
            view["risk_level"] = state.risk.level.value
        if transcript_tier == "evidence_only":
            view["evidence"] = [
                {"fact_key": key, "evidence": fact.evidence}
                for key, fact in ((k, state.facts[k]) for k in kept_keys)
                if fact.evidence
            ]
        elif transcript_tier == "recent_window":
            view["transcript"] = [
                {"role": item.get("role"), "content": item.get("content")}
                for item in state.transcript[-RECENT_WINDOW_TURNS:]
            ]
        if want_prior:
            prior = state.runtime_snapshot.get("prior_case")
            if prior is not None:
                prior = dict(prior) if isinstance(prior, dict) else prior
                if kind == "second_opinion" and isinstance(prior, dict):
                    # A prior disposition is the strongest tendency anchor
                    # for the same patient; strip it from second-opinion
                    # views and keep only the complaint/outcome facts.
                    prior.pop("disposition_level", None)
                    prior.pop("disposition_text", None)
                view["prior_case"] = prior
        if view_extras:
            for key, value in view_extras.items():
                if value is not None:
                    view[key] = value
        if kind == "second_opinion":
            # Re-assert blinding after the extras merge so no extra can
            # re-inject an objective, a disposition anchor, or a
            # structural conclusion field into a blinded view.
            view.pop("objective", None)
            for key in ("phase", "risk_level", "conclusion"):
                view.pop(key, None)
            prior = view.get("prior_case")
            if isinstance(prior, dict):
                prior = dict(prior)
                prior.pop("disposition_level", None)
                prior.pop("disposition_text", None)
                view["prior_case"] = prior
        return view

    @staticmethod
    def _content_excerpts(hypothesis_ids: List[str]) -> List[Dict[str, Any]]:
        index = _content_index()
        excerpts = []
        for hypothesis_id in hypothesis_ids:
            entry = index.get(hypothesis_id)
            if entry:
                excerpts.append(
                    {
                        "ref": hypothesis_id,
                        "title": entry["title"],
                        "excerpt": entry["text"][:1200],
                    }
                )
        return excerpts

    def _branch_shell(
        self, branch: Dict[str, Any], status: str, error_type: str = None
    ) -> Dict[str, Any]:
        result = {
            "subagent_id": branch["subagent_id"],
            "branch_id": branch["branch_id"],
            "task_kind": branch["view"]["task_kind"],
            "status": status,
            "conclusion": "",
            "findings": [],
            "sources": [],
            "uncertainty": {"level": "high", "reasons": []},
            "usage": {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_hit_tokens": 0,
                "total_tokens": 0,
                "duration_ms": 0,
            },
            "validation": {
                "schema_ok": False,
                "quotes_total": 0,
                "quotes_verified": 0,
                "dropped_claims": 0,
                "scope_clamped": list(branch["scope_clamped"]),
                "notes": [],
            },
            "digest": None,
            "followup": None,
        }
        if error_type:
            result["error_type"] = error_type
        return result

    def _run_branch(
        self,
        state: CaseState,
        branch: Dict[str, Any],
        session_id: str,
        spawn_id: str,
        parent_cancelled,
    ) -> Dict[str, Any]:
        kind = branch["view"]["task_kind"]
        budget = branch["budget"]
        sub_session = f"sub:{branch['subagent_id']}#{branch['branch_id']}"
        started = monotonic()
        cancel = ThreadEvent()
        timed_out = ThreadEvent()
        done = ThreadEvent()

        def _watch() -> None:
            deadline = started + float(budget["deadline_s"])
            while not done.is_set():
                if parent_cancelled is not None and parent_cancelled.is_set():
                    cancel.set()
                    return
                if monotonic() >= deadline:
                    timed_out.set()
                    cancel.set()
                    return
                done.wait(0.02)

        watcher = Thread(target=_watch, daemon=True)
        watcher.start()
        report: Optional[Dict[str, Any]] = None
        settle: Optional[str] = None
        error_type: Optional[str] = None
        web_pool: Dict[str, str] = {}
        # Token-fuse fallback (P2-B-D10): _usage_for only sees
        # model.responded events, so a backend that never emits them
        # would leave max_tokens unmetered. Estimates are accumulated
        # only for calls that produced no new usage event (see the
        # fuse check below); metered calls contribute nothing, so the
        # event path is unchanged.
        fallback_tokens = 0
        metered_calls_seen = 0
        last_call_estimate = 0
        fallback_tripped_tokens: Optional[int] = None
        # Excerpts this branch was actually fed: the initial view's
        # content_excerpts plus every knowledge_search result returned
        # during the run. Knowledge quotes verify against this set only
        # (see _validate_report) — never against entry full text.
        knowledge_pool: Dict[str, str] = {}
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": KIND_INSTRUCTIONS[kind]},
            {"role": "user", "content": json.dumps(branch["view"], ensure_ascii=False)},
        ]
        try:
            with self._slots:
                steps = 0
                while steps < int(budget["max_calls"]):
                    if timed_out.is_set():
                        settle = "timeout"
                        break
                    if cancel.is_set():
                        settle = "cancelled"
                        break
                    steps += 1
                    try:
                        raw_command = self.backend.decide_cancellable(
                            sub_session, messages, cancel
                        )
                    except DecisionInterrupted:
                        settle = "timeout" if timed_out.is_set() else "cancelled"
                        break
                    except Exception as exc:
                        settle = "failed"
                        error_type = type(exc).__name__
                        break
                    last_call_estimate = _estimate_roundtrip_tokens(
                        messages, raw_command
                    )
                    command = self._normalize_command(raw_command)
                    if command is None:
                        self._feed(
                            messages,
                            None,
                            {"ok": False, "error": "回复必须是恰好一个 {tool, arguments} JSON 信封"},
                        )
                        continue
                    tool = command["tool"]
                    arguments = command["arguments"]
                    if tool == "subagent_report":
                        if kind == "second_opinion" and arguments.get(
                            "disposition_tendency"
                        ) not in TENDENCY_LEVELS:
                            # The structured tendency is a required part of
                            # the second-opinion schema (design §5.2): feed
                            # the schema error back and let the child repair
                            # within its remaining steps.
                            self._feed(
                                messages,
                                command,
                                {
                                    "ok": False,
                                    "error": "second_opinion 的 subagent_report 必须带合法的 disposition_tendency（self_care|routine_visit|urgent_today|emergency）",
                                },
                            )
                            continue
                        report = arguments
                        break
                    if tool not in branch["effective_tools"]:
                        self.events.append(
                            Event(
                                session_id,
                                "subagent.denied",
                                {
                                    "spawn_id": spawn_id,
                                    "branch_id": branch["branch_id"],
                                    "tool": tool,
                                    "reason": "tool_not_allowed",
                                },
                            )
                        )
                        self._feed(
                            messages,
                            command,
                            {"ok": False, "error": f"工具 {tool} 不在本次任务的允许清单内"},
                        )
                        continue
                    outcome = self._execute_tool(
                        tool,
                        arguments,
                        branch,
                        session_id,
                        spawn_id,
                        web_pool,
                        knowledge_pool,
                    )
                    self._feed(messages, command, outcome)
                    usage = self._usage_for(sub_session, started)
                    if usage["model_calls"] == metered_calls_seen:
                        # No new usage event for this call: without the
                        # estimate its tokens would be invisible to the
                        # fuse (P2-B-D10). Metered calls add nothing.
                        fallback_tokens += last_call_estimate
                    metered_calls_seen = usage["model_calls"]
                    if (
                        usage["total_tokens"] + fallback_tokens
                        > int(budget["max_tokens"])
                    ):
                        settle = "budget_exhausted"
                        if fallback_tokens:
                            fallback_tripped_tokens = fallback_tokens
                        payload: Dict[str, Any] = {
                            "spawn_id": spawn_id,
                            "branch_id": branch["branch_id"],
                            "task_kind": kind,
                            "limit": "max_tokens",
                            "max_tokens": budget["max_tokens"],
                        }
                        if fallback_tokens:
                            payload["estimated_tokens"] = fallback_tokens
                        self.events.append(
                            Event(session_id, "budget.exceeded", payload)
                        )
                        break
                else:
                    settle = "budget_exhausted"
                    self.events.append(
                        Event(
                            session_id,
                            "budget.exceeded",
                            {
                                "spawn_id": spawn_id,
                                "branch_id": branch["branch_id"],
                                "task_kind": kind,
                                "limit": "max_calls",
                                "max_calls": budget["max_calls"],
                            },
                        )
                    )
        finally:
            done.set()
            watcher.join(timeout=1)
        usage = self._usage_for(sub_session, started)
        if fallback_tripped_tokens is not None:
            # Event-based totals stay authoritative (0 here); the fuse
            # fired on the estimate, so record it alongside, not instead.
            usage = {**usage, "estimated_tokens": fallback_tripped_tokens}
        if settle == "cancelled":
            self.events.append(
                Event(
                    session_id,
                    "subagent.cancelled",
                    {
                        "spawn_id": spawn_id,
                        "branch_id": branch["branch_id"],
                        "task_kind": kind,
                    },
                )
            )
        if report is None:
            result = self._branch_shell(branch, settle or "failed", error_type)
            result["usage"] = usage
            return result
        result = self._validate_report(
            state, branch, report, web_pool, session_id, spawn_id, knowledge_pool
        )
        result["usage"] = usage
        return result

    @staticmethod
    def _normalize_command(command: Any) -> Optional[Dict[str, Any]]:
        if isinstance(command, str):
            try:
                command = parse_decision(command)
            except Exception:
                return None
        if not isinstance(command, dict):
            return None
        if not isinstance(command.get("tool"), str) or not isinstance(
            command.get("arguments"), dict
        ):
            return None
        return command

    @staticmethod
    def _feed(
        messages: List[Dict[str, str]],
        command: Optional[Dict[str, Any]],
        outcome: Dict[str, Any],
    ) -> None:
        if command is not None:
            messages.append(
                {
                    "role": "assistant",
                    "content": json.dumps(command, ensure_ascii=False),
                }
            )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {"tool_result": outcome}, ensure_ascii=False
                ),
            }
        )

    def _execute_tool(
        self,
        tool: str,
        arguments: Dict[str, Any],
        branch: Dict[str, Any],
        session_id: str,
        spawn_id: str,
        web_pool: Dict[str, str],
        knowledge_pool: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        kind = branch["view"]["task_kind"]
        if tool == "case_view":
            return {"ok": True, "tool": tool, "view": branch["view"]}
        if tool == "knowledge_search":
            results = _knowledge_search(
                str(arguments.get("query", "")), arguments.get("max_results", 3)
            )
            if knowledge_pool is not None:
                for item in results:
                    knowledge_pool[item["ref"]] = item["excerpt"]
            return {"ok": True, "tool": tool, "results": results}
        if tool in {"web_search", "web_fetch"}:
            allowed = (
                KINDS[kind]["allow_web"] and self.web_enabled and self.web_tools is not None
            )
            if not allowed:
                self.events.append(
                    Event(
                        session_id,
                        "subagent.denied",
                        {
                            "spawn_id": spawn_id,
                            "branch_id": branch["branch_id"],
                            "tool": tool,
                            "reason": "web_unavailable",
                        },
                    )
                )
                return {"ok": False, "tool": tool, "error": "联网工具在本次部署中不可用"}
            try:
                if tool == "web_search":
                    value = self.web_tools.search(
                        str(arguments.get("query", "")),
                        purpose=str(arguments.get("purpose", "guideline_check")),
                        caller="subagent",
                    )
                    return {"ok": True, "tool": tool, "result": value}
                url = str(arguments.get("url", ""))
                value = self.web_tools.fetch(url, caller="subagent")
                text = (
                    value.get("content", "")
                    if isinstance(value, dict)
                    else str(value)
                )
                fetch_id = hashlib.sha256(url.encode()).hexdigest()[:12]
                web_pool[fetch_id] = text
                web_pool[url] = text
                return {
                    "ok": True,
                    "tool": tool,
                    "fetch_id": fetch_id,
                    "url": url,
                    "content": text[:8000],
                }
            except Exception as exc:
                return {"ok": False, "tool": tool, "error": type(exc).__name__}
        return {"ok": False, "tool": tool, "error": "未知工具"}

    def _usage_for(self, sub_session: str, started: float) -> Dict[str, int]:
        usage = {
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_tokens": 0,
            "total_tokens": 0,
            "duration_ms": max(0, round((monotonic() - started) * 1000)),
        }
        try:
            events = self.events.for_session(sub_session)
        except Exception:
            return usage
        for event in events:
            if event.event_type != "model.responded":
                continue
            metrics = event.payload.get("usage") or {}
            usage["model_calls"] += 1
            usage["input_tokens"] += int(metrics.get("prompt_tokens", 0) or 0)
            usage["output_tokens"] += int(metrics.get("completion_tokens", 0) or 0)
            usage["cache_hit_tokens"] += int(
                metrics.get("prompt_cache_hit_tokens", 0) or 0
            )
            usage["total_tokens"] += int(
                metrics.get("total_tokens", 0)
                or (
                    int(metrics.get("prompt_tokens", 0) or 0)
                    + int(metrics.get("completion_tokens", 0) or 0)
                )
            )
        return usage
