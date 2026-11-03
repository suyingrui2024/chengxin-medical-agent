"""The self-check tasks a patient is offered, and what counts as done.

A task is offered only when it applies and is not already answered, and
its result is accepted only through the versioned contract the task
declares -- "done" is a claim the record can verify, not a tick in the
interface.

Start at ``SelfCheckService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..extensions.builtin import load_registry
from ..clinical.interpreter import Interpretation


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

