"""Local review workspace: isolated backend configurations and in-process history."""
from __future__ import annotations

import json
import os
from collections import OrderedDict
from threading import RLock, Thread
from urllib.parse import urlparse

from .agent import AdaptiveConsultationEngine
from .domain import utc_now
from .events import EventStore
from .consultation_subject import subject_profile
from .backends import CompatibleBackend, DshBackend
from .runtime import build_runtime
from pathlib import Path
from .extensions.builtin import load_registry, templates
from .events import Event
from .medical_images import vision_configuration_status
from .web_tools import web_mode


class StaleCompositionError(ValueError):
    pass


class Workspace:
    def __init__(self, engine, events, backend):
        self.engine = engine
        self.events = events
        self.backend = backend
        self.engines = [engine]
        self.hosts = []
        self.probes = set()
        self.closing = False
        # Persistence coordinates of the startup composition. The desktop
        # sidecar builds its first runtime with an explicit session_dir and
        # event_path; a recomposition that forgets them produces an engine
        # whose session_store is None, so every session created after a
        # settings apply silently stops reaching disk. Derived from the
        # startup engine/events so any host that composes a Workspace
        # (server, desktop) keeps the same behaviour.
        store = getattr(engine, "session_store", None)
        self.session_dir = getattr(store, "root", None)
        event_path = getattr(events, "_path", None)
        self.event_path = Path(event_path) if event_path is not None else None
        # Subagent-drafted follow-up messages (followup_batch), keyed by
        # session_id. A draft is never sent from here; the user
        # confirms/edits it in the follow-up UI before anything sends.
        # Drafts persist to follow_up_drafts.json inside the session dir
        # so the server process that generated (or loaded) them keeps
        # serving them across restarts; without a session dir they stay
        # process-memory, exactly like the sessions themselves.
        self.follow_up_drafts = self._load_follow_up_drafts()
        self._draft_batch_thread = None
        # (session_id, due_at) pairs already attempted in this process.
        # A case whose draft keeps failing the coordinator's lint would
        # otherwise re-burn model calls on every follow-up list read; it
        # is retried on the next process start or when a new plan (new
        # due_at) replaces the old one.
        self._draft_attempted = set()
        # Set by the server once its SubagentCoordinator exists; _compose
        # re-attaches it to each adaptive engine it builds (previously
        # only the startup engine ever received it).
        self.subagent_coordinator = None
        self._current_host = None
        # Bounded idempotency log for session creation: request_id -> the
        # exact response already returned, so a retried POST /api/sessions
        # hands back the session it created instead of minting a duplicate.
        self._session_creations: "OrderedDict[str, dict]" = OrderedDict()
        self.lock = RLock()
        self.settings = {
            k: os.environ.get(k, "")
            for k in (
                "MEDAGENT_LLM_BASE_URL",
                "MEDAGENT_LLM_API_KEY",
                "MEDAGENT_LLM_MODEL",
                "MEDAGENT_VISION_MODEL",
            )
        }

    def public_settings(self):
        with self.lock:
            return {
                "backend": self.backend,
                "base_url": self.settings["MEDAGENT_LLM_BASE_URL"],
                "model": self.settings["MEDAGENT_LLM_MODEL"],
                "vision_model": self.settings["MEDAGENT_VISION_MODEL"],
                "has_api_key": bool(self.settings["MEDAGENT_LLM_API_KEY"]),
                "dsh_review_mode": os.environ.get("MEDAGENT_DSH_REVIEW_MODE", "shadow"),
                "web_tools_mode": web_mode(),
                "subagent_web": os.environ.get("MEDAGENT_SUBAGENT_WEB", "off")
                == "on",
                "subagents": {
                    "mode": os.environ.get("MEDAGENT_SUBAGENT_MODE", "off")
                },
                "storage": (
                    "session_store"
                    if getattr(self.engine, "session_store", None) is not None
                    else "process_memory"
                ),
            }

    def resolve(self, payload):
        if set(payload) - {
            "backend",
            "base_url",
            "model",
            "api_key",
            "vision_model",
        }:
            raise ValueError("配置包含不支持的字段")
        mode = payload.get("backend")
        if mode not in {"demo", "compatible", "dsh"}:
            raise ValueError("请选择支持的运行方式")
        config = dict(self.settings)
        mapping = {
            "base_url": "MEDAGENT_LLM_BASE_URL",
            "model": "MEDAGENT_LLM_MODEL",
            "api_key": "MEDAGENT_LLM_API_KEY",
            "vision_model": "MEDAGENT_VISION_MODEL",
        }
        for key, name in mapping.items():
            value = payload.get(key)
            if value is not None:
                if not isinstance(value, str) or len(value) > 4096:
                    raise ValueError("配置字段格式错误")
                if key == "api_key" and not value.strip():
                    continue  # Blank retains an existing key, never sends it back to the browser.
                config[name] = value.strip()
        if mode == "demo":
            config = {name: "" for name in self.settings}
        url = config["MEDAGENT_LLM_BASE_URL"]
        if mode != "demo":
            changing_service = mode != self.backend or url.rstrip("/") != self.settings[
                "MEDAGENT_LLM_BASE_URL"
            ].rstrip("/")
            if (
                changing_service
                and self.settings["MEDAGENT_LLM_API_KEY"]
                and not payload.get("api_key", "").strip()
            ):
                raise ValueError("更换服务地址或运行方式时，请重新填写对应服务的密钥")
            parsed = urlparse(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("请填写不含账号、查询参数或片段的 HTTP(S) 服务地址")
            if parsed.scheme == "http" and parsed.hostname not in {
                "127.0.0.1",
                "::1",
                "localhost",
            }:
                raise ValueError("外部模型服务必须使用 HTTPS；HTTP 只允许本机测试地址")
            if not config["MEDAGENT_LLM_API_KEY"] or not config["MEDAGENT_LLM_MODEL"]:
                raise ValueError("请填写模型 ID 和 API 密钥")
        return mode, config

    def apply(self, payload):
        with self.lock:
            if self.closing:
                raise RuntimeError("工作台正在关闭")
            mode, config = self.resolve(payload)
            self._compose(mode, config, self.engine.content.registry)
            self.backend, self.settings = mode, config
            return self.public_settings()

    def _attach_subagents(self, engine):
        coordinator = getattr(self, "subagent_coordinator", None)
        if coordinator is not None and isinstance(
            engine, AdaptiveConsultationEngine
        ):
            engine.subagent_coordinator = coordinator

    def _compose(self, mode, config, registry):
        host = build_runtime(
            backend=mode,
            config=config,
            events=self.events,
            registry=registry,
            session_dir=self.session_dir,
            event_path=self.event_path,
        )
        context = host.start()
        engine = context.services.require("consultation")
        self._attach_subagents(engine)
        replaced = self._current_host
        self.hosts.append(host)
        self._current_host = host
        self.engine = engine
        self.engines.append(engine)
        if replaced is not None:
            # The replaced engine stays in self.engines (its sessions are
            # still reachable through engine_for), but its runtime host is
            # stopped and dropped: hosts used to accumulate one live
            # composition per apply/reconfigure until process shutdown.
            self.hosts.remove(replaced)
            try:
                replaced.stop()
            except Exception as exc:
                # The new composition is already installed above; a
                # failing teardown of the old host must not fail the
                # apply/reconfigure that replaced it — the exception
                # would otherwise leave backend/settings pointing at the
                # old composition while self.engine already serves the
                # new one. EventStore appends are fail-open, so
                # recording the failure cannot raise either.
                self.events.append(
                    Event(
                        "workspace",
                        "composition.host_stop_failed",
                        {"error_type": type(exc).__name__},
                    )
                )

    def session_creation(self, request_id):
        """Response already returned for this create ``request_id``, if any."""
        if not request_id:
            return None
        with self.lock:
            payload = self._session_creations.get(request_id)
            if payload is not None:
                self._session_creations.move_to_end(request_id)
            return payload

    def remember_session_creation(self, request_id, payload):
        if not request_id:
            return
        with self.lock:
            self._session_creations[request_id] = payload
            self._session_creations.move_to_end(request_id)
            while len(self._session_creations) > 128:
                self._session_creations.popitem(last=False)

    def catalog(self):
        with self.lock:
            return {**self.engine.content.registry.catalog(), "templates": templates()}

    def reconfigure(self, payload):
        with self.lock:
            if self.closing:
                raise ValueError("工作台正在关闭")
            if set(payload) - {"id", "enabled", "template_id", "expected_revision"}:
                raise ValueError("能力配置包含未支持字段")
            old = self.engine.content.registry
            if payload.get("expected_revision") != old.revision:
                raise StaleCompositionError("能力配置已经变化，请刷新后再试")
            if "template_id" in payload:
                if "id" in payload or "enabled" in payload:
                    raise ValueError("模板与能力切换不能混在同一次操作")
                registry = load_registry(payload["template_id"])
            else:
                registry = old.with_enabled(payload.get("id"), payload.get("enabled"))
            self._compose(self.backend, self.settings, registry)
            self.events.append(
                Event(
                    "workspace",
                    "composition.changed",
                    {
                        "previous_sha256": old.revision,
                        "new_sha256": registry.revision,
                        "template": registry.snapshot()["template"],
                        "scope": "new_sessions_only",
                    },
                )
            )
            return self.catalog()

    def preview(self, identity, arguments, expected_revision):
        with self.lock:
            registry = self.engine.content.registry
            if expected_revision != registry.revision:
                raise StaleCompositionError("能力配置已经变化，请刷新后再试")
        result = registry.execute(identity, arguments, "developer_preview")
        self.events.append(Event("capability-preview", "capability.previewed", result))
        return result

    def probe(self, payload):
        with self.lock:
            if self.closing:
                raise RuntimeError("工作台正在关闭")
            mode, config = self.resolve(payload)
            if mode == "demo":
                return {
                    "ok": True,
                    "message": "离线演示已就绪，无需模型连接。",
                    "capabilities": {
                        "consultation": {"status": "offline", "message": "使用本地固定流程"},
                        "dsh": {"status": "offline", "message": "离线模式未启用"},
                        "vision": {"status": "offline", "message": "离线模式不发送图片"},
                    },
                }
            events = EventStore()
            backend = (
                CompatibleBackend(events, config)
                if mode == "compatible"
                else DshBackend(events, Path(__file__).resolve().parents[2], config)
            )
            self.probes.add(backend)
        try:
            result = backend.decide(
                "connection-check",
                [
                    {
                        "role": "system",
                        "content": 'Return only {"tool":"inspect_case","arguments":{}}.',
                    },
                    {"role": "user", "content": "Connection test. No patient data."},
                ],
            )
        finally:
            with self.lock:
                self.probes.discard(backend)
                if hasattr(backend, "close"):
                    backend.close()
        if result != {"tool": "inspect_case", "arguments": {}}:
            raise ValueError("服务有响应，但未返回预期 JSON 协议")
        return {
            "ok": True,
            "message": "连接成功，模型返回了有效的协议响应。",
            "capabilities": {
                "consultation": {"status": "verified", "message": "问诊协议已验证"},
                "dsh": {
                    "status": "verified" if mode == "dsh" else "not_selected",
                    "message": "DSH 协议已验证" if mode == "dsh" else "当前使用直接模型接口",
                },
                "vision": vision_configuration_status(config, mode),
            },
        }

    def engine_for(self, session_id):
        with self.lock:
            for engine in reversed(self.engines):
                try:
                    engine.state(session_id)
                    return engine
                except KeyError:
                    continue
        raise KeyError(session_id)

    def sessions(self):
        output = []
        with self.lock:
            for engine in self.engines:
                with engine._sessions_lock:
                    states = list(engine._sessions.values())
                for state in states:
                    output.append(
                        {
                            "session_id": state.session_id,
                            "title": str(state.get("chief_complaint", "新的问诊")),
                            "subject_label": (subject_profile(state) or {}).get(
                                "label"
                            ),
                            "created_at": state.created_at,
                            "updated_at": state.updated_at,
                            "phase": state.phase.value,
                            "risk": state.risk.level.value,
                            "fact_count": len(state.facts),
                            "backend": getattr(
                                getattr(engine, "backend", None), "name", "demo"
                            ),
                        }
                    )
        return sorted(output, key=lambda s: s["created_at"], reverse=True)

    # -- follow-up draft store + in-process batch trigger (§5.7.4) --

    def _drafts_file(self):
        if self.session_dir is None:
            return None
        return Path(self.session_dir) / "follow_up_drafts.json"

    def _load_follow_up_drafts(self):
        path = self._drafts_file()
        if path is None:
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        drafts = payload.get("drafts") if isinstance(payload, dict) else None
        if not isinstance(drafts, dict):
            return {}
        return {
            session_id: draft
            for session_id, draft in drafts.items()
            if isinstance(session_id, str) and isinstance(draft, dict)
        }

    def _persist_follow_up_drafts(self):
        """Best-effort durable write; the caller holds ``self.lock``. A
        failed write keeps the in-memory copy authoritative for this
        process instead of breaking the follow-up list."""
        path = self._drafts_file()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {"version": 1, "drafts": self.follow_up_drafts},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as exc:
            # Not just OSError: a coordinator result carrying an
            # unserializable value fails json.dumps with TypeError, and
            # the offline batch script drives this same path with no
            # outer catch to absorb it. Either way the in-memory copy
            # stays authoritative for this process; the failure leaves
            # an event trace instead of vanishing silently (or crashing
            # the caller). EventStore appends are fail-open.
            self.events.append(
                Event(
                    "workspace",
                    "followup.drafts_persist_failed",
                    {"error_type": type(exc).__name__},
                )
            )

    def store_follow_up_drafts(self, results):
        """Merge coordinator drafts into the durable store.

        Idempotent by session_id: a session that already has a draft is
        never re-drafted or overwritten, whether the existing draft came
        from this process, a previous run of the batch script, or a
        server restart that reloaded it from disk.
        """
        stored = 0
        with self.lock:
            for result in results or []:
                if not isinstance(result, dict):
                    continue
                session_id = result.get("session_id")
                if not session_id or session_id in self.follow_up_drafts:
                    continue
                if not result.get("draft_message"):
                    continue
                self.follow_up_drafts[session_id] = dict(result)
                stored += 1
            if stored:
                self._persist_follow_up_drafts()
                self.events.append(
                    Event(
                        "workspace",
                        "followup.drafts_stored",
                        {"count": stored},
                    )
                )
        return stored

    def follow_up_drafts_due(self):
        """follow_ups() entries that are due, still scheduled and have
        no draft yet — the batch's work list (design §5.7.4)."""
        now = utc_now()
        due = []
        for item in self.follow_ups():
            plan = item.get("plan") or {}
            if plan.get("status") != "scheduled":
                continue
            if str(plan.get("due_at", "")) > now:
                continue
            if item["session_id"] in self.follow_up_drafts:
                continue
            attempt = (item["session_id"], str(plan.get("due_at", "")))
            if attempt in self._draft_attempted:
                continue
            due.append(item)
        return due

    def maybe_schedule_followup_drafts(self) -> int:
        """Queue in-process draft generation for due follow-ups.

        Called on the follow-up list read path; generation runs on a
        daemon thread so the response never waits on the model. Returns
        how many cases were queued (0 when nothing ran).

        Advisory-only surfacing: the coordinator's spawn wiring treats
        shadow runs as telemetry (recorded, never shown), and the
        offline batch script applies the same advisory gate, so a
        shadow/off coordinator — or one without a cancellable backend —
        leaves the list exactly as it was. Fail-open throughout.
        """
        try:
            coordinator = getattr(self, "subagent_coordinator", None)
            if (
                coordinator is None
                or getattr(coordinator, "mode", "off") != "advisory"
            ):
                return 0
            backend = getattr(coordinator, "backend", None)
            if not callable(getattr(backend, "decide_cancellable", None)):
                return 0
            due = self.follow_up_drafts_due()
            if not due:
                return 0
            with self.lock:
                thread = self._draft_batch_thread
                if thread is not None and thread.is_alive():
                    return 0
                states = {}
                for item in due:
                    try:
                        states[item["session_id"]] = self.engine_for(
                            item["session_id"]
                        ).state(item["session_id"])
                    except KeyError:
                        continue
                due = [
                    item for item in due if item["session_id"] in states
                ]
                if not due:
                    return 0
                for item in due:
                    self._draft_attempted.add(
                        (
                            item["session_id"],
                            str((item.get("plan") or {}).get("due_at", "")),
                        )
                    )
                thread = Thread(
                    target=self._run_followup_draft_batch,
                    args=(coordinator, due, states),
                    name="medical-followup-drafts",
                    daemon=True,
                )
                self._draft_batch_thread = thread
                thread.start()
            return len(due)
        except Exception:
            return 0

    def _run_followup_draft_batch(self, coordinator, items, states):
        try:
            results = coordinator.run_followup_batch(items, states)
            self.store_follow_up_drafts(results)
        except Exception:
            # Drafts are an advisory cache; a generation failure must
            # never surface on the follow-up list path.
            pass

    def follow_ups(self):
        """All follow-up plans across engines, earliest due first.

        The longitudinal loop's read side: plans live on their cases, so
        this aggregates them for the due list / outcome-recording UI.
        """
        output = []
        with self.lock:
            for engine in self.engines:
                with engine._sessions_lock:
                    states = list(engine._sessions.values())
                for state in states:
                    if not state.follow_up:
                        continue
                    plan = dict(state.follow_up)
                    item = {
                        "session_id": state.session_id,
                        "title": str(
                            state.get("chief_complaint", "新的问诊")
                        ),
                        "created_at": state.created_at,
                        "closed_reason": state.closed_reason,
                        "risk": state.risk.level.value,
                        "plan": plan,
                    }
                    draft = self.follow_up_drafts.get(state.session_id)
                    if draft is not None:
                        item["draft"] = draft
                    output.append(item)
        return sorted(output, key=lambda item: item["plan"].get("due_at", ""))

    def delete_session(self, session_id):
        with self.lock:
            engine = self.engine_for(session_id)
            engine.delete_session(session_id)
            self.events.discard_session(session_id)
            if self.follow_up_drafts.pop(session_id, None) is not None:
                self._persist_follow_up_drafts()
            self._draft_attempted = {
                attempt
                for attempt in self._draft_attempted
                if attempt[0] != session_id
            }

    def close(self):
        with self.lock:
            self.closing = True
            for backend in [
                getattr(engine, "backend", None) for engine in self.engines
            ] + list(self.probes):
                if hasattr(backend, "close"):
                    backend.close()
            for host in self.hosts:
                host.stop()
