"""Composing the server's services, and choosing which model backend runs.

One factory owns the whole graph -- events, content, triage, self-checks and
the consultation engine -- so no route can assemble a half-wired set.  The
backend is chosen here too: ``demo`` is explicitly offline, and anything else
must bring its address, key and model name, with a plaintext non-loopback
endpoint refused at build time rather than at first call.

Start at ``build_runtime``.
"""

from __future__ import annotations

from pathlib import Path
import os
from typing import Callable, Optional

from ..consultation import ConsultationEngine
from ..core.events import EventStore
from ..clinical.knowledge import ContentRepository
from ..infra.plugins import PluginHost, RuntimeContext
from ..guards.self_checks import SelfCheckService
from ..clinical.triage import TriageService
from ..clinical.hypothesis_ranking import HypothesisRanker
from ..consultation.agent import AdaptiveConsultationEngine
from ..infra.backends import (
    CompatibleBackend,
    DshBackend,
    require_https_or_loopback_endpoint,
)
from ..extensions.builtin import load_registry


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
        """Build the consultation engine for one runtime composition.

        The planner is the differential one by default; the rule planner stays
        available as MEDAGENT_PLANNER=rule, the rollback the A/B evaluation is
        the evidence for.  Session persistence is layered the same way: an
        explicit directory wins, then the environment override, then a sibling
        of the event log -- and without any of them, sessions stay in process
        memory, which is a documented shape, not an accident.
        """
        kwargs = dict(content=ctx.services.require("content"), triage=ctx.services.require("triage"),
                      self_checks=ctx.services.require("self_checks"), events=ctx.services.require("events"),
                      ranker=HypothesisRanker())
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
            from ..infra.session_store import SessionStore

            kwargs["session_store"] = SessionStore(store_dir)
        if mode == "demo":
            engine = ConsultationEngine(**kwargs)
        else:
            transport = (CompatibleBackend(kwargs["events"], settings) if mode == "compatible" else
                         DshBackend(kwargs["events"], Path(__file__).resolve().parents[3], settings))
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

