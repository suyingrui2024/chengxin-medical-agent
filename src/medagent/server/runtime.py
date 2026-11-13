"""Composing the server's services, and choosing which model backend runs.

One factory owns the whole graph -- events, content, triage, self-checks and
the consultation engine -- so no route can assemble a half-wired set.  The
backend is chosen here too: ``demo`` is explicitly offline, and anything else
must bring its address, key and model name, with a plaintext non-loopback
endpoint refused at build time rather than at first call.

Start at ``build_runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from typing import Optional

from ..consultation import ConsultationEngine
from ..core.events import EventStore
from ..clinical.knowledge import ContentRepository
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


class RuntimeServices(dict):
    """Named services shared by the server and desktop composition."""

    def require(self, name):
        try:
            return self[name]
        except KeyError:
            raise RuntimeError(f"required service is missing: {name}") from None

    def names(self):
        return sorted(self)


@dataclass
class RuntimeContext:
    services: RuntimeServices = field(default_factory=RuntimeServices)


@dataclass(eq=False, repr=False)
class Runtime:
    """Publish a complete service graph on start; release references on stop."""

    mode: str
    settings: dict
    event_path: Optional[Path]
    events: Optional[EventStore]
    registry: object
    session_dir: Optional[Path]
    context: RuntimeContext = field(default_factory=RuntimeContext, init=False)

    def start(self):
        """Publish the graph atomically, preserving startup storage precedence."""
        if self.context.services:
            raise ValueError("runtime already started")
        mode, settings = self.mode, self.settings
        event_store = self.events if self.events is not None else EventStore(self.event_path)
        capabilities = self.registry or load_registry()
        content = ContentRepository(capabilities)
        triage = TriageService()
        self_checks = SelfCheckService(content.registry)
        kwargs = dict(
            content=content,
            triage=triage,
            self_checks=self_checks,
            events=event_store,
            ranker=HypothesisRanker(),
        )
        # Explicit directory, then environment override, then event-log sibling.
        store_dir = self.session_dir
        if store_dir is None:
            env_dir = os.environ.get("MEDAGENT_SESSION_DATA_DIR", "").strip()
            if env_dir:
                store_dir = Path(env_dir)
            elif self.event_path is not None:
                store_dir = Path(self.event_path).parent / "sessions"
        if store_dir is not None:
            from ..infra.session_store import SessionStore

            kwargs["session_store"] = SessionStore(store_dir)
        if mode == "demo":
            engine = ConsultationEngine(**kwargs)
        else:
            transport = (
                CompatibleBackend(event_store, settings)
                if mode == "compatible"
                else DshBackend(event_store, Path(__file__).resolve().parents[2], settings)
            )
            engine = AdaptiveConsultationEngine(**kwargs, backend=transport)
        engine.runtime_descriptor = {
            "adapter": mode,
            "model": settings.get("MEDAGENT_LLM_MODEL", "") if mode != "demo" else "",
            "dsh_version": "0.2.1-rc.2" if mode == "dsh" else None,
        }
        self.context.services.update(
            backend_mode=mode,
            events=event_store,
            capabilities=capabilities,
            content=content,
            triage=triage,
            self_checks=self_checks,
            consultation=engine,
        )
        return self.context

    def stop(self):
        self.context.services.clear()


def build_runtime(
    event_path: Optional[Path] = None,
    backend: Optional[str] = None,
    config: Optional[dict] = None,
    events: Optional[EventStore] = None,
    registry=None,
    session_dir: Optional[Path] = None,
) -> Runtime:
    """Prepare an offline or model-backed composition, built when start() runs."""
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
        require_https_or_loopback_endpoint(settings["MEDAGENT_LLM_BASE_URL"].rstrip("/"))

    return Runtime(mode, settings, event_path, events, registry, session_dir)
