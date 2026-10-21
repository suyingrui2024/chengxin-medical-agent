"""Local review workspace: isolated backend configurations and in-process history."""
from __future__ import annotations

import os
from threading import RLock
from urllib.parse import urlparse

from .events import EventStore
from .consultation_subject import subject_profile
from .backends import CompatibleBackend, DshBackend
from .runtime import build_runtime
from pathlib import Path
from .extensions.builtin import load_registry, templates
from .events import Event
from .medical_images import vision_configuration_status


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
                "storage": "process_memory",
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

    def _compose(self, mode, config, registry):
        host = build_runtime(
            backend=mode, config=config, events=self.events, registry=registry
        )
        context = host.start()
        self.hosts.append(host)
        self.engine = context.services.require("consultation")
        self.engines.append(self.engine)

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

    def delete_session(self, session_id):
        with self.lock:
            engine = self.engine_for(session_id)
            engine.delete_session(session_id)
            self.events.discard_session(session_id)

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
