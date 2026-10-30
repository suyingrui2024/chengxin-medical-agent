"""Mixin: lifecycle for SubagentCoordinator."""
from ._preamble import *  # noqa: F401,F403


class CoordinatorLifecycle:
    def __init__(
        self,
        events,
        backend,
        *,
        mode: str = "off",
        kind_modes: Optional[Dict[str, str]] = None,
        web_enabled: bool = False,
        web_tools=None,
        max_concurrent: int = 8,
        daily_token_cap: Optional[int] = None,
    ):
        if mode not in MODES:
            raise ValueError("subagent mode must be off, shadow or advisory")
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.events = events
        self.backend = backend
        self.mode = mode
        # Per-kind rollout overrides (design §5.7): each task kind may run
        # at its own mode while the rest follow the global ``mode``. Only
        # gating reads go through mode_for(); the run/validate pipeline
        # itself is mode-agnostic — shadow vs advisory is decided by the
        # consumer of the result, not here.
        self.kind_modes: Dict[str, str] = {}
        if kind_modes:
            self._validate_kind_modes(kind_modes)
            self.kind_modes.update(kind_modes)
        self.web_enabled = bool(web_enabled)
        self.web_tools = web_tools
        self._slots = BoundedSemaphore(max_concurrent)
        # Deployment-level daily token fuse (design §5.5): once the day's
        # accumulated subagent tokens reach the cap, further spawns are
        # refused until the next day instead of silently burning budget.
        if daily_token_cap is None:
            try:
                daily_token_cap = int(
                    os.environ.get(
                        "MEDAGENT_SUBAGENT_DAILY_TOKEN_CAP", "2000000"
                    )
                )
            except ValueError:
                daily_token_cap = 2000000
            if daily_token_cap <= 0:
                daily_token_cap = 2000000
        self.daily_token_cap = daily_token_cap
        self._daily_lock = Lock()
        self._daily_date = date.today()
        self._daily_tokens = 0

    @staticmethod
    def _validate_kind_modes(mapping: Dict[str, str]) -> None:
        if not isinstance(mapping, dict):
            raise ValueError("kind_modes must be a dict of task kind to mode")
        for kind, kind_mode in mapping.items():
            if kind not in KINDS:
                raise ValueError(f"unknown subagent task kind: {kind!r}")
            if kind_mode not in MODES:
                raise ValueError(
                    f"subagent mode for {kind} must be off, shadow or advisory"
                )

    def mode_for(self, task_kind) -> str:
        """Effective mode for one task kind: override if set, else global.

        Unknown kinds (including None, e.g. a malformed proposal) fall
        back to the global mode.
        """
        if isinstance(task_kind, str) and task_kind in self.kind_modes:
            return self.kind_modes[task_kind]
        return self.mode

    def set_kind_modes(self, mapping: Dict[str, str]) -> Dict[str, str]:
        """Merge per-kind mode overrides and record the change.

        Raises ValueError on an unknown kind or mode (nothing is merged
        in that case). Appends a ``subagent.modes_changed`` event whose
        payload carries every kind's *effective* mode, and returns that
        same ``{kind: effective_mode}`` mapping.
        """
        self._validate_kind_modes(mapping)
        self.kind_modes.update(mapping)
        effective = {kind: self.mode_for(kind) for kind in KINDS}
        self.events.append(
            Event("", "subagent.modes_changed", {"kinds": effective})
        )
        return effective

    def _daily_usage(self) -> int:
        with self._daily_lock:
            if self._daily_date != date.today():
                self._daily_date = date.today()
                self._daily_tokens = 0
            return self._daily_tokens

    def _daily_add(self, tokens: int) -> None:
        with self._daily_lock:
            if self._daily_date != date.today():
                self._daily_date = date.today()
                self._daily_tokens = 0
            self._daily_tokens += max(0, int(tokens))

    def run_spawn(
        self,
        state: CaseState,
        proposal: Dict[str, Any],
        *,
        session_id: str,
        cancelled=None,
    ) -> Dict[str, Any]:
        """Run one spawn proposal against a case. Never raises."""
        try:
            return self._run_spawn(state, proposal, session_id, cancelled, None)
        except Exception as exc:  # fail-open: the consultation must go on
            return {
                "ok": False,
                "status": "failed",
                "error_type": type(exc).__name__,
                "branches": [],
            }

    def run_case_digest(
        self, state: CaseState, *, session_id: str, cancelled=None
    ) -> Optional[Dict[str, Any]]:
        """Controller-triggered digest of a long case (design §5.7.3).

        Returns the validated digest dict, or None when the coordinator
        is disabled/unavailable or the run did not produce a valid one.
        """
        if self.mode_for("case_digest") == "off":
            return None
        try:
            proposal = {
                "task_kind": "case_digest",
                "objective": "把长病例压缩为带逐字引文的结构化摘要",
                "branches": [
                    {
                        "branch_id": "digest",
                        "objective": "压缩病例为结构化摘要",
                        "scope": {
                            "fact_keys": sorted(state.facts.keys()),
                            "transcript": "recent_window",
                            "prior_case": True,
                        },
                    }
                ],
            }
            result = self._run_spawn(state, proposal, session_id, cancelled, None)
            if not result.get("ok") or not result.get("branches"):
                return None
            branch = result["branches"][0]
            if branch.get("status") in {"completed", "partial"}:
                return branch.get("digest")
            return None
        except Exception:
            return None

    def run_followup_batch(
        self, items: List[Dict[str, Any]], states: Dict[str, CaseState]
    ) -> List[Dict[str, Any]]:
        """One child per due follow-up case (design §5.7.4).

        ``items`` are workspace.follow_ups() entries, ``states`` maps
        session_id -> CaseState. Returns validated drafts only; a case
        whose draft fails the lint is dropped (and the drop is logged).
        """
        if self.mode_for("followup_batch") == "off" or not self._backend_available():
            return []
        output: List[Dict[str, Any]] = []
        batch_started = monotonic()
        batch_deadline = KINDS["followup_batch"]["batch_deadline_s"]
        for item in items or []:
            if not isinstance(item, dict):
                continue
            if monotonic() - batch_started > batch_deadline:
                self.events.append(
                    Event(
                        str(item.get("session_id", "")),
                        "budget.exceeded",
                        {"kind": "followup_batch", "limit": "batch_deadline_s"},
                    )
                )
                break
            session_id = item.get("session_id")
            state = states.get(session_id) if session_id else None
            if state is None:
                continue
            try:
                proposal = {
                    "task_kind": "followup_batch",
                    "objective": "整理随访优先级并起草提醒",
                    "branches": [
                        {
                            "branch_id": "followup",
                            "objective": "判断随访优先级并起草提醒草稿",
                            "scope": {
                                "fact_keys": sorted(state.facts.keys()),
                                "prior_case": True,
                            },
                        }
                    ],
                }
                extras = {
                    "title": item.get("title"),
                    "follow_up_plan": item.get("plan"),
                    "conclusion": state.conclusion or None,
                }
                result = self._run_spawn(state, proposal, session_id, None, extras)
            except Exception:
                continue
            if not result.get("ok") or not result.get("branches"):
                continue
            branch = result["branches"][0]
            followup = branch.get("followup")
            if branch.get("status") in {"completed", "partial"} and followup:
                output.append({"session_id": session_id, **followup})
        return output

    def _backend_available(self) -> bool:
        return callable(getattr(self.backend, "decide_cancellable", None))
