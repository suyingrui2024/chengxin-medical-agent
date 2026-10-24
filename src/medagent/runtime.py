from __future__ import annotations

from pathlib import Path
import os
from typing import Callable, Optional

from .consultation import ConsultationEngine
from .events import EventStore
from .knowledge import ContentRepository
from .plugins import PluginHost, RuntimeContext
from .self_checks import SelfCheckService
from .triage import TriageService
from .reasoner import OrderedQuestionRanker
from .agent import AdaptiveConsultationEngine
from .backends import (
    CompatibleBackend,
    DshBackend,
    require_https_or_loopback_endpoint,
)
from .extensions.builtin import load_registry


class ServicePlugin:
    def __init__(self, name: str, factory: Callable[[RuntimeContext], object]) -> None:
        self.name = name
        self.factory = factory

    def mount(self, context: RuntimeContext):
        return context.services.provide(self.name, self.factory(context))


def build_runtime(event_path: Optional[Path] = None, backend: Optional[str] = None,
                  config: Optional[dict] = None, events: Optional[EventStore] = None, registry=None,
                  session_dir: Optional[Path] = None) -> PluginHost:
    """Compose an explicit profile, equivalent to a small DSH bundle."""
    mode = backend or os.environ.get("MEDAGENT_BACKEND", "demo")
    settings = config if config is not None else dict(os.environ)
    if mode not in {"demo", "compatible", "dsh"}:
        raise ValueError("backend must be demo, compatible or dsh")
    if mode != "demo":
        for key in ("MEDAGENT_LLM_BASE_URL", "MEDAGENT_LLM_API_KEY", "MEDAGENT_LLM_MODEL"):
            if not settings.get(key, "").strip():
                raise ValueError(f"{mode} backend requires {key}; demo is explicitly offline")
        # Fail fast here (not only when the consultation service is
        # first required and the backend is constructed): a plaintext
        # non-loopback endpoint is rejected at build time. The backend
        # constructors enforce the same rule for direct construction.
        require_https_or_loopback_endpoint(
            settings["MEDAGENT_LLM_BASE_URL"].rstrip("/")
        )

    def create_engine(ctx):
        kwargs = dict(content=ctx.services.require("content"), triage=ctx.services.require("triage"),
                      self_checks=ctx.services.require("self_checks"), events=ctx.services.require("events"),
                      ranker=OrderedQuestionRanker())
        # Session persistence: an explicit session_dir wins, then the
        # MEDAGENT_SESSION_DATA_DIR env override, then a sibling of the
        # event log. Without any of these, sessions stay process-memory.
        store_dir = session_dir
        if store_dir is None:
            env_dir = os.environ.get("MEDAGENT_SESSION_DATA_DIR", "").strip()
            if env_dir:
                store_dir = Path(env_dir)
            elif event_path is not None:
                store_dir = Path(event_path).parent / "sessions"
        if store_dir is not None:
            from .session_store import SessionStore

            kwargs["session_store"] = SessionStore(store_dir)
        if mode == "demo":
            engine = ConsultationEngine(**kwargs)
        else:
            transport = (CompatibleBackend(kwargs["events"], settings) if mode == "compatible" else
                         DshBackend(kwargs["events"], Path(__file__).resolve().parents[2], settings))
            engine = AdaptiveConsultationEngine(**kwargs, backend=transport)
        engine.runtime_descriptor = {"adapter": mode, "model": settings.get("MEDAGENT_LLM_MODEL", "") if mode != "demo" else "",
                                     "dsh_version": "0.2.1-rc.2" if mode == "dsh" else None}
        return engine

    plugins = [
        ServicePlugin("backend_mode", lambda _: mode),
        ServicePlugin("events", lambda _: events if events is not None else EventStore(event_path)),
        ServicePlugin("capabilities", lambda _: registry or load_registry()),
        ServicePlugin("content", lambda ctx: ContentRepository(ctx.services.require("capabilities"))),
        ServicePlugin("triage", lambda _: TriageService()),
        ServicePlugin("self_checks", lambda ctx: SelfCheckService(ctx.services.require("content").registry)),
        ServicePlugin("consultation", create_engine),
    ]
    return PluginHost(plugins)

