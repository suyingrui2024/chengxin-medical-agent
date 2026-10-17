from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, TypeVar


class Plugin(Protocol):
    name: str

    def mount(self, context: "RuntimeContext") -> Optional[Callable[[], None]]:
        ...


T = TypeVar("T")


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: Dict[str, Any] = {}

    def provide(self, name: str, service: Any) -> Callable[[], None]:
        if name in self._services:
            raise ValueError(f"service already registered: {name}")
        self._services[name] = service

        def dispose() -> None:
            self._services.pop(name, None)

        return dispose

    def require(self, name: str) -> Any:
        if name not in self._services:
            raise RuntimeError(f"required service is missing: {name}")
        return self._services[name]

    def get(self, name: str, default: Any = None) -> Any:
        return self._services.get(name, default)

    def names(self) -> List[str]:
        return sorted(self._services)


@dataclass
class RuntimeContext:
    services: ServiceRegistry


class PluginHost:
    """Small reversible plugin host inspired by DSH's composition model."""

    def __init__(self, plugins: Iterable[Plugin]) -> None:
        self.context = RuntimeContext(services=ServiceRegistry())
        self._plugins = list(plugins)
        self._disposers: List[Callable[[], None]] = []

    def start(self) -> RuntimeContext:
        try:
            for plugin in self._plugins:
                disposer = plugin.mount(self.context)
                if disposer:
                    self._disposers.append(disposer)
        except Exception:
            self.stop()
            raise
        return self.context

    def stop(self) -> None:
        while self._disposers:
            self._disposers.pop()()

