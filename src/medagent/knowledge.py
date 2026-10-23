from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from importlib import resources
from typing import Any, Dict, List, Optional

from .domain import Phase, QuickReply
from .extensions.builtin import load_registry
from .text_match import contains_unnegated


@dataclass(frozen=True)
class Question:
    id: str
    phase: Phase
    fact_key: str
    prompt: str
    input: str
    options: List[QuickReply]
    applies_when: Dict[str, Any]


class ContentRepository:
    def __init__(self, registry=None) -> None:
        package = resources.files("medagent.content")
        self._questions_raw = json.loads(package.joinpath("questions.json").read_text(encoding="utf-8"))
        self.registry = registry or load_registry()
        self.questions = [self._make_question(item) for item in self._questions_raw["questions"]]
        self.tasks = self.registry.tasks
        if {task["result_key"] for task in self.tasks.values()} & {q.fact_key for q in self.questions}:
            raise ValueError("观察结果不能覆盖核心问诊字段")

    @staticmethod
    def _make_question(raw: Dict[str, Any]) -> Question:
        return Question(
            id=raw["id"],
            phase=Phase(raw["phase"]),
            fact_key=raw["fact_key"],
            prompt=raw["prompt"],
            input=raw["input"],
            options=[QuickReply(**item) for item in raw.get("options", [])],
            applies_when=dict(raw.get("applies_when", {})),
        )

    def question(self, question_id: str) -> Question:
        for item in self.questions:
            if item.id == question_id:
                return item
        raise KeyError(question_id)

    def applicable_task(self, chief_complaint: str) -> Optional[Dict[str, Any]]:
        tasks = self.applicable_tasks(chief_complaint)
        return tasks[0] if tasks else None

    def applicable_tasks(self, chief_complaint: str) -> List[Dict[str, Any]]:
        complaint = chief_complaint.lower()
        output = []
        for task in self.tasks.values():
            terms = task.get("applies_when", {}).get("chief_complaint_contains_any", [])
            if any(term.lower() in complaint for term in terms):
                output.append(dict(task))
        return output

    @staticmethod
    def question_applies(question: Question, chief_complaint: str) -> bool:
        terms = question.applies_when.get("chief_complaint_contains_any", [])
        if not terms:
            return True
        value = chief_complaint.lower()
        return any(contains_unnegated(value, term.lower()) for term in terms)

    @property
    def versions(self) -> Dict[str, str]:
        return {
            "questions": self._questions_raw["version"],
            "questions_sha256": hashlib.sha256(json.dumps(self._questions_raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "self_checks": self.registry.revision,
        }

