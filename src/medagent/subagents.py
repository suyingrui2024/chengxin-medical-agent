"""Controlled subagent coordinator for the adaptive consultation engine.

Implements docs/联网与子智能体工具设计.md §5/§6 (Phase C, core C1):
a Python-layer coordinator that lets the adaptive engine spawn bounded,
read-only subagents for a closed set of task kinds. The medical DSH
bridge stays zero-tool by design; sub-runs go through the same
``decide_cancellable`` backend abstraction as the foreground, receive a
one-shot projected view of the case (never the CaseState itself), and
their reports only become consumable after a deterministic validation
pipeline (verbatim evidence checks, banned-phrase lint, uncertainty
floor, drop-rate fuse, staleness watermark).

Fail-open throughout: any failure degrades to a status dict (or ``None``
/ ``[]`` for the convenience runners) instead of raising into the
consultation loop. Subagent output is advisory data with provenance; it
never writes facts and never reaches the patient unmediated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib import resources
from threading import BoundedSemaphore, Event as ThreadEvent, Lock, Thread
from time import monotonic
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .backends import parse_decision
from .conclusion import BANNED_PHRASES, GAP_IMPACTS, MEASUREMENT_FACTS
from .decision_wait import DecisionInterrupted
from .domain import CaseState, utc_now
from .events import Event
from .reasoning import ReasoningWorkspace, load_pathways


MODES = {"off", "shadow", "advisory"}

# Structured disposition tendency required from second_opinion reports
# (design §5.2); the wiring layer maps these onto the case risk groups.
TENDENCY_LEVELS = {"self_care", "routine_visit", "urgent_today", "emergency"}

# Per-kind budget tiers (design §5.5). followup_batch runs off the
# consultation clock: a per-case deadline of 60s inside a 600s batch.
KINDS: Dict[str, Dict[str, Any]] = {
    "evidence_lookup": {
        "max_calls": 6,
        "max_tokens": 24000,
        "deadline_s": 45,
        "allow_web": True,
    },
    "second_opinion": {
        "max_calls": 3,
        "max_tokens": 16000,
        "deadline_s": 40,
        "allow_web": False,
    },
    "case_digest": {
        "max_calls": 2,
        "max_tokens": 12000,
        "deadline_s": 30,
        "allow_web": False,
    },
    "followup_batch": {
        "max_calls": 4,
        "max_tokens": 16000,
        "deadline_s": 60,
        "allow_web": False,
        "batch_deadline_s": 600,
    },
}

# Read-only tool surface per kind (design §5.3). ``subagent_report`` is
# the structural reporting channel and is always available; the proposal
# may narrow the rest but can never widen beyond this table. No write
# tool and no spawn tool exist here — depth 1 holds by absence.
_KIND_TOOLS: Dict[str, tuple] = {
    "evidence_lookup": (
        "case_view",
        "knowledge_search",
        "web_search",
        "web_fetch",
        "subagent_report",
    ),
    "second_opinion": ("case_view", "knowledge_search", "subagent_report"),
    "case_digest": ("case_view", "knowledge_search", "subagent_report"),
    "followup_batch": ("case_view", "knowledge_search", "subagent_report"),
}

MAX_BRANCHES = 4

# Aggregate envelope shared by all branches of one in-consultation spawn
# (design §5.5); followup_batch is exempt (its own batch envelope applies).
SPAWN_ENVELOPE_TOKENS = 48000
SPAWN_ENVELOPE_DEADLINE_S = 45
RECENT_WINDOW_TURNS = 16
MAX_FINDINGS = 20
MAX_CLAIM_CHARS = 300
MAX_CONCLUSION_CHARS = 1000

# Patient-facing imperative/disposition phrasing that must never survive
# validation inside a claim or draft (design §5.4 step 4). BANNED_PHRASES
# already covers the certainty/diagnosis wording.
_DISPOSITION_PATTERN = re.compile(
    r"(应立即就医|必须立即就医|立即就医|必须服用|立即服用|必须住院"
    r"|立即停药|加量服用|减量服用|应尽快手术|必须手术"
    r"|去急诊|看急诊|挂急诊|尽快就医)"
)
# Dosage figures are banned from follow-up drafts (design §4.3 rule
# applied to generated drafts): a reminder may not carry dose numbers.
# NOTE: no trailing \b — in CJK text a unit is almost always followed by
# a Chinese character, which \b treats as a word character, so
# "200mg每日两次" / "2片药" / "500毫克维生素" previously slipped through
# while only punctuation-terminated forms ("200mg。") were caught.
_DOSAGE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(mg|毫克|ml|毫升|片|粒|支|袋|iu|IU)"
)

_ENVELOPE_HINT = """你只能返回一个 JSON 对象，格式恰好为 {"tool": ..., "arguments": {...}}，不得输出任何其他文字、解释或代码块标记。
可用工具：
- knowledge_search：检索本地内容库（鉴别条目与自护内容）。arguments: {"query": "检索词", "max_results": 3}。结果会以 tool_result 回喂给你，其中的 ref 是内容定位符，摘录是可逐字引用的原文。
- case_view：重读本次任务的病例投影。arguments: {}。
- subagent_report：提交最终结果并结束任务，arguments 形状见下方「提交格式」。
证据纪律（硬性）：每条 finding 必须至少带一条 evidence，quote 必须是逐字引文——case 类引文只能出自病例投影中对应事实的 evidence 或值（ref 填事实键）；knowledge 类引文只能出自 knowledge_search 回喂的摘录（ref 填该摘录的 ref）；没有逐字证据的判断不要写成 finding。不得编造引文、数字或来源。
禁止事项：不得给出确诊结论，不得给出处置、用药、剂量或就医时限指令，不得使用「确诊、保证、肯定是」等措辞；你的产出只是供主流程复核的参考材料，不是给患者的答复。"""

KIND_INSTRUCTIONS: Dict[str, str] = {
    "evidence_lookup": (
        """你是一个受控的证据检索子智能体。主流程正在考虑若干鉴别方向，但证据稀薄；你的任务是针对病例投影中给定的方向，整理：还缺什么关键证据、现有事实中哪些表现支持或反对该方向、需要注意的红旗点。先用 knowledge_search 查本地内容库再下结论。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"conclusion":"≤1000字小结","findings":[{"claim":"≤300字","evidence":[{"source_type":"case|knowledge|web","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":["..."]}}}
若检索后仍无任何已验证来源支持任何判断，findings 返回空数组并在 uncertainty 中说明。"""
    ),
    "second_opinion": (
        """你是一个受控的二意见复核子智能体。你看不到主流程的结论、方向与处置倾向——这是刻意的盲化设计，请完全基于病例投影独立判断：当前信息下你倾向的处置等级（自我观察/常规就诊/今日尽快就诊/急诊）与理由、你认为最不能漏掉的方向、以及还需要补问什么。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"conclusion":"≤1000字，写明你的独立处置倾向与理由","disposition_tendency":"self_care|routine_visit|urgent_today|emergency 四选一，与你的结论一致","findings":[{"claim":"≤300字","evidence":[{"source_type":"case|knowledge","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":["..."]}}}
处置倾向只能写在 conclusion 里作为复核意见，不得写成给患者的指令口吻。"""
    ),
    "case_digest": (
        """你是一个受控的病例摘要子智能体。你的任务是把病例投影（事实与最近对话）压缩成结构化摘要，供主流程替换窗口外的原文。摘要中的每条已确认事实必须带逐字引文（出自事实 evidence、事实值或患者原话），不得改写、推断或补全；发现的表述矛盾要单列出来。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"digest":{"timeline":["按时间顺序的事件短句"],"confirmed_facts":[{"key":"事实键","quote":"逐字引文"}],"contradictions":["矛盾点短句"],"prior_outcomes":["既往结局短句，无则空数组"]},"conclusion":"","findings":[],"uncertainty":{"level":"low|medium|high","reasons":[]}}}"""
    ),
    "followup_batch": (
        """你是一个受控的随访整理子智能体。你会收到一个已关闭病例的结论与随访计划投影；你的任务是判断这次随访的优先级并起草一条给患者的随访提醒草稿。草稿只是草稿，会经人工确认后才发送。草稿中不得出现确诊、保证类措辞，不得写具体药物剂量，不得给出新的处置指令。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"priority":"high|routine","reason":"优先级理由≤300字","draft_message":"随访提醒草稿≤300字","findings":[{"claim":"判断依据","evidence":[{"source_type":"case|knowledge","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":[]}}}"""
    ),
}


def watermark(state: CaseState) -> Dict[str, Any]:
    """Staleness watermark for a case (design §5.6).

    Exported for the wiring layer: capture before a spawn, compare at
    delivery; any change means the case moved on and results are stale.
    """
    fact_fingerprint = "|".join(
        sorted(f"{key}:{fact.fact_id}" for key, fact in state.facts.items())
    )
    conclusion = state.conclusion if isinstance(state.conclusion, dict) else {}
    return {
        "phase": state.phase.value,
        "revision": state.revision,
        "updated_at": state.updated_at,
        "fact_ids_hash": hashlib.sha256(fact_fingerprint.encode()).hexdigest(),
        "conclusion_revision": conclusion.get("revision"),
    }


def _load_content_json(name: str) -> Dict[str, Any]:
    raw = (
        resources.files("medagent.content").joinpath(name).read_text(encoding="utf-8")
    )
    return json.loads(raw)


_content_index_cache: Optional[Dict[str, Dict[str, str]]] = None


def _content_index() -> Dict[str, Dict[str, str]]:
    """locator -> {title, text} over differentials + self_care content.

    Locators are hypothesis ids, pathway ids and self_care group ids;
    the text is the entry's canonical JSON. Knowledge quotes are NOT
    verified against this full text at validation time — only against
    the excerpts actually fed to the branch (see _validate_report).
    """
    global _content_index_cache
    if _content_index_cache is not None:
        return _content_index_cache
    index: Dict[str, Dict[str, str]] = {}
    for pathway in load_pathways():
        index[pathway["id"]] = {
            "title": pathway["id"],
            "text": json.dumps(pathway, ensure_ascii=False),
        }
        for hypothesis in pathway.get("hypotheses", []):
            index[hypothesis["id"]] = {
                "title": hypothesis.get("label", hypothesis["id"]),
                "text": json.dumps(hypothesis, ensure_ascii=False),
            }
    for group in _load_content_json("self_care.json").get("groups", []):
        index[group["id"]] = {
            "title": group["id"],
            "text": json.dumps(group, ensure_ascii=False),
        }
    _content_index_cache = index
    return index


def _known_fact_keys(state: CaseState) -> set:
    """v1 allowed fact-key universe for scope clamping.

    Union of: the case's own fact keys, every fact key referenced by the
    differentials content (support/refute conditions and discriminating
    questions), and the conclusion layer's gap/measurement key sets.
    Scope is additionally intersected with the case's recorded facts, so
    an allowed-but-absent key is still clamped out of the view.
    """
    keys = set(state.facts)
    for pathway in load_pathways():
        for hypothesis in pathway.get("hypotheses", []):
            for condition in hypothesis.get("support", []) + hypothesis.get(
                "refute", []
            ):
                if condition.get("fact_key"):
                    keys.add(condition["fact_key"])
            keys.update(hypothesis.get("discriminating_questions", []))
    keys |= set(GAP_IMPACTS) | set(MEASUREMENT_FACTS)
    return keys


def _knowledge_search(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """Deterministic keyword-substring retrieval over the content pool."""
    terms = [term for term in re.split(r"[\s,，、;；]+", query or "") if len(term) >= 2]
    if not terms:
        return []
    scored = []
    for ref, entry in _content_index().items():
        hits = sum(1 for term in terms if term in entry["text"])
        if hits:
            scored.append((hits, ref, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    cap = max(1, min(int(max_results or 3), 5))
    return [
        {"ref": ref, "title": entry["title"], "excerpt": entry["text"][:1200]}
        for _, ref, entry in scored[:cap]
    ]


def _banned_hit(text: str) -> bool:
    return any(phrase in text for phrase in BANNED_PHRASES)


def _estimate_roundtrip_tokens(messages: List[Dict[str, str]], raw_command: Any) -> int:
    """Coarse token estimate for one backend round trip (P2-B-D10 fuse).

    Roughly two characters per token for mixed Chinese/English text.
    Deliberately coarse: this is a fuse for backends that emit no
    ``model.responded`` usage events at all, not metering — it only
    ever stands in for calls that produced no new usage event, so a
    fully metered backend keeps exactly its event-driven behaviour.
    """
    prompt_chars = sum(
        len(str(item.get("content", "")))
        for item in messages
        if isinstance(item, dict)
    )
    if isinstance(raw_command, str):
        response_chars = len(raw_command)
    elif isinstance(raw_command, dict):
        response_chars = len(json.dumps(raw_command, ensure_ascii=False))
    else:
        response_chars = 0
    return (prompt_chars + response_chars + 1) // 2


class SubagentCoordinator:
    """Spawns, budgets and validates bounded subagent runs (design §5)."""

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

    # ------------------------------------------------------------------
    # Per-kind mode resolution
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Public runners (all fail-open: they return statuses, never raise)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Spawn pipeline
    # ------------------------------------------------------------------

    def _backend_available(self) -> bool:
        return callable(getattr(self.backend, "decide_cancellable", None))

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

    # ------------------------------------------------------------------
    # Branch execution
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Validation pipeline (design §5.4, steps in order)
    # ------------------------------------------------------------------

    def _validate_report(
        self,
        state: CaseState,
        branch: Dict[str, Any],
        report: Any,
        web_pool: Dict[str, str],
        session_id: str,
        spawn_id: str,
        knowledge_pool: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        kind = branch["view"]["task_kind"]
        result = self._branch_shell(branch, "failed")
        validation = result["validation"]
        if not isinstance(report, dict):
            validation["notes"].append("report_not_object")
            return result
        validation["schema_ok"] = True
        if kind == "second_opinion":
            # Enum-validated at report acceptance time in the child loop.
            result["disposition_tendency"] = report.get("disposition_tendency")

        # Case evidence pool: only facts inside this branch's projection.
        case_pool: Dict[str, List[str]] = {}
        for key, fact_view in branch["view"]["facts"].items():
            pool = []
            if fact_view.get("evidence"):
                pool.append(str(fact_view["evidence"]))
            if fact_view.get("value") is not None:
                pool.append(str(fact_view["value"]))
            case_pool[key] = pool
        # Knowledge quotes verify ONLY against excerpts this branch was
        # actually fed — the initial view's content_excerpts plus the
        # knowledge_search results returned during this run. Verifying
        # against entry full text let a child "wash" an assertion with a
        # passage it never saw (e.g. text past the 1200-char excerpt
        # cut, quoted with no search at all).
        fed_knowledge: Dict[str, str] = {}
        for item in branch["view"].get("content_excerpts", []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("excerpt"), str)
            ):
                fed_knowledge[item["ref"]] = item["excerpt"]
        if knowledge_pool:
            fed_knowledge.update(knowledge_pool)
        knowledge_pool = fed_knowledge

        def evidence_ok(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            quote = item.get("quote")
            ref = item.get("ref")
            source_type = item.get("source_type")
            if not isinstance(quote, str) or not quote or not isinstance(ref, str):
                return False
            if source_type == "case":
                return any(quote in part for part in case_pool.get(ref, []))
            if source_type == "knowledge":
                text = knowledge_pool.get(ref)
                return text is not None and quote in text
            if source_type == "web":
                # v1 keeps web off by default; a web quote verifies only
                # against full text this branch actually fetched.
                text = web_pool.get(ref)
                return text is not None and quote in text
            return False

        raw_findings = report.get("findings")
        if not isinstance(raw_findings, list):
            raw_findings = []
        kept: List[Dict[str, Any]] = []
        dropped = 0
        quotes_total = 0
        quotes_verified = 0
        for finding in raw_findings[:MAX_FINDINGS]:
            if not isinstance(finding, dict):
                dropped += 1
                continue
            claim = finding.get("claim")
            evidence = finding.get("evidence")
            if (
                not isinstance(claim, str)
                or not claim
                or len(claim) > MAX_CLAIM_CHARS
                or not isinstance(evidence, list)
                or not evidence
            ):
                dropped += 1
                quotes_total += len(evidence) if isinstance(evidence, list) else 0
                continue
            quotes_total += len(evidence)
            verified_here = sum(1 for item in evidence if evidence_ok(item))
            if _banned_hit(claim) or _DISPOSITION_PATTERN.search(claim):
                dropped += 1
                continue
            if verified_here != len(evidence):
                dropped += 1
                continue
            # Only surviving claims contribute to quotes_verified: a
            # dropped claim's verified quotes previously inflated the
            # metric (P2-B-D09) even though its evidence never survives.
            quotes_verified += verified_here
            kept.append(
                {
                    "claim": claim,
                    "evidence": evidence,
                    "confidence": finding.get("confidence")
                    if finding.get("confidence") in {"low", "medium", "high"}
                    else "low",
                    "provenance": "subagent",
                }
            )
        dropped += max(0, len(raw_findings) - MAX_FINDINGS)
        validation["quotes_total"] = quotes_total
        validation["quotes_verified"] = quotes_verified
        validation["dropped_claims"] = dropped

        conclusion = report.get("conclusion")
        conclusion = conclusion if isinstance(conclusion, str) else ""
        if len(conclusion) > MAX_CONCLUSION_CHARS:
            conclusion = conclusion[:MAX_CONCLUSION_CHARS]
            validation["notes"].append("conclusion_truncated")
        if conclusion and (
            _banned_hit(conclusion) or _DISPOSITION_PATTERN.search(conclusion)
        ):
            # A conclusion carrying banned/disposition phrasing is
            # dropped wholesale, never partially reworded.
            conclusion = ""
            dropped += 1
            validation["dropped_claims"] = dropped
            validation["notes"].append("conclusion_dropped_lint")

        uncertainty = report.get("uncertainty")
        if not isinstance(uncertainty, dict):
            uncertainty = {}
        level = uncertainty.get("level")
        if level not in {"low", "medium", "high"}:
            level = "high"
        reasons = uncertainty.get("reasons")
        reasons = [str(item)[:200] for item in reasons] if isinstance(reasons, list) else []
        if dropped > 0 and level == "low":
            level = "medium"  # uncertainty floor (step 5)
            reasons.append("dropped_claims_floor")

        total_claims = len(kept) + dropped
        status: Optional[str] = None
        if total_claims > 0 and dropped / total_claims > 0.5:
            status = "rejected_by_validation"  # drop-rate fuse (step 6)
            kept = []
        elif kind == "evidence_lookup" and quotes_verified == 0:
            status = "rejected_by_validation"  # no verified source (step 5)
            kept = []
        result["conclusion"] = conclusion
        result["findings"] = kept
        result["uncertainty"] = {"level": level, "reasons": reasons}
        sources: List[Dict[str, Any]] = []
        seen_sources = set()
        for finding in kept:
            for item in finding["evidence"]:
                key = (item["source_type"], item["ref"])
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                source = {
                    "kind": {
                        "case": "fact_key",
                        "knowledge": "content_id",
                        "web": "url",
                    }[item["source_type"]],
                    "locator": item["ref"],
                }
                if item["source_type"] == "web":
                    source["retrieved_at"] = utc_now()
                sources.append(source)
        result["sources"] = sources

        if kind == "case_digest":
            result["digest"] = self._validate_digest(
                state,
                report.get("digest"),
                scope_keys=set(branch["scope_summary"]["fact_keys"]),
            )
            if status is None:
                status = (
                    "completed"
                    if result["digest"] is not None and dropped == 0
                    else "partial"
                    if result["digest"] is not None
                    else "failed"
                )
        elif kind == "followup_batch":
            result["followup"] = self._validate_followup(report, validation)
            if status is None:
                status = "completed" if dropped == 0 else "partial"
        else:
            if status is None:
                status = "completed" if dropped == 0 else "partial"
        result["status"] = status
        self.events.append(
            Event(
                session_id,
                "subagent.validated",
                {
                    "spawn_id": spawn_id,
                    "branch_id": branch["branch_id"],
                    "task_kind": kind,
                    "status": status,
                    "quotes_total": quotes_total,
                    "quotes_verified": quotes_verified,
                    "dropped_claims": dropped,
                    "followup_draft_dropped": (
                        kind == "followup_batch" and result["followup"] is None
                    ),
                },
            )
        )
        return result

    def _validate_digest(
        self,
        state: CaseState,
        digest: Any,
        scope_keys: Optional[set] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(digest, dict):
            return None

        def clean(items: Any) -> List[str]:
            if not isinstance(items, list):
                return []
            output = []
            for item in items:
                if not isinstance(item, str):
                    continue
                text = " ".join(item.split())
                if not text or len(text) > MAX_CLAIM_CHARS:
                    continue
                if _banned_hit(text) or _DISPOSITION_PATTERN.search(text):
                    continue
                if text not in output:
                    output.append(text)
            return output[:20]

        transcript_pool = "\n".join(
            item.get("content", "")
            for item in state.transcript
            if item.get("role") == "user"
        )
        # Anchor pool for timeline items: fact evidence/values plus the
        # patient's own turns. A timeline item is kept only if it shares
        # at least one 2-character substring with this pool — a
        # paraphrase of something actually said/recorded ("主诉感冒一周
        # 未愈" vs "感冒一周了还没好") survives, while an event that
        # appears nowhere in the case ("上周做过胃镜结果正常") is
        # dropped instead of entering the digest as verified content.
        # This is a lexical anchor, not a semantic proof: an invented
        # item sharing a common bigram with the transcript could still
        # pass, and such items must not be read as quote-verified.
        anchor_pool = [
            str(part)
            for fact in state.facts.values()
            for part in (fact.evidence or "", fact.value)
            if part is not None and str(part)
        ] + [
            item.get("content", "")
            for item in state.transcript
            if item.get("role") == "user"
        ]

        def anchored(text: str) -> bool:
            for pool_text in anchor_pool:
                if not pool_text:
                    continue
                if text in pool_text or pool_text in text:
                    return True
                bigrams = {text[i : i + 2] for i in range(len(text) - 1)}
                if any(bigram in pool_text for bigram in bigrams):
                    return True
            return False
        confirmed: List[Dict[str, str]] = []
        raw_confirmed = digest.get("confirmed_facts")
        if isinstance(raw_confirmed, list):
            for item in raw_confirmed:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                quote = item.get("quote")
                fact = state.facts.get(key) if isinstance(key, str) else None
                if fact is None or not isinstance(quote, str) or not quote:
                    continue
                if scope_keys is not None and key not in scope_keys:
                    # Branch scope is the task boundary (P2-B-D08): an
                    # out-of-scope fact, however well quoted, must not
                    # enter the digest — it is skipped outright, so it
                    # counts neither as a pass nor as a failure.
                    continue
                pool = [part for part in (fact.evidence or "",) if part]
                if fact.value is not None:
                    pool.append(str(fact.value))
                if any(quote in part for part in pool) or quote in transcript_pool:
                    confirmed.append({"key": key, "quote": quote})
        validated = {
            "timeline": [
                item for item in clean(digest.get("timeline")) if anchored(item)
            ],
            "confirmed_facts": confirmed,
            "contradictions": clean(digest.get("contradictions")),
            "prior_outcomes": clean(digest.get("prior_outcomes")),
        }
        if not confirmed and not validated["timeline"]:
            return None
        return validated

    @staticmethod
    def _validate_followup(
        report: Dict[str, Any], validation: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        priority = report.get("priority")
        if priority not in {"high", "routine"}:
            priority = "routine"
        reason = report.get("reason")
        reason = reason if isinstance(reason, str) else ""
        draft = report.get("draft_message")
        if not isinstance(draft, str) or not draft.strip():
            validation["notes"].append("followup_draft_missing")
            return None
        draft = draft.strip()
        if (
            len(draft) > MAX_CLAIM_CHARS
            or _banned_hit(draft)
            or _DISPOSITION_PATTERN.search(draft)
            or _DOSAGE_PATTERN.search(draft)
        ):
            validation["notes"].append("followup_draft_lint")
            return None
        return {
            "priority": priority,
            "reason": reason[:MAX_CLAIM_CHARS],
            "draft_message": draft,
        }
