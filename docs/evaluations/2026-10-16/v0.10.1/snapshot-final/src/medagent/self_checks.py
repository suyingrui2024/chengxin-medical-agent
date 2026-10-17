from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .extensions.builtin import load_registry
from .interpreter import Interpretation


@dataclass(frozen=True)
class TaskResult:
    fact_key: str
    interpretation: Interpretation


class SelfCheckService:
    def __init__(self, registry=None):
        self.registry = registry or load_registry()

    def interpret(self, task_id: str, text: str, choice_value: Optional[str] = None) -> TaskResult:
        key, result = self.registry.interpret_task(task_id, text, choice_value)
        return TaskResult(key, result)

