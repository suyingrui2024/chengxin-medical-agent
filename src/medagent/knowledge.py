from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional

from .domain import Assertion, Fact, Phase, QuickReply
from .extensions.builtin import load_registry
from .text_match import contains_unnegated

# Patient-facing age prompt for non-child subjects, shared by both engines.
# The catalog text ("婴幼儿请尽量写清月龄…") is what the *model* sees in its
# catalog; presenting it verbatim to a 32- or 65-year-old asking for
# themselves reads as a template bug (live-eval cases 1/5/9). Child
# consultations keep the catalog wording, where the months phrasing is
# the point.
ADULT_AGE_PROMPT = "患者多大？可直接写年龄，例如 32 岁。"


def question_fact_gates_pass(question: "Question", facts: Mapping[str, Fact]) -> bool:
    """Fact-dependent eligibility, evaluated at candidate-computation time.

    A question drops out of the candidate set when a recorded fact
    excludes it (``excludes_fact_equals``, e.g. pregnancy screening
    once sex is recorded male) or when a known age falls outside its
    ``age_between_when_known`` window. An unknown age keeps the
    question eligible: the gate only fires on known facts. Shared by
    the adaptive and deterministic engines so both apply identical
    gating semantics.
    """
    for key, excluded in (question.excludes_fact_equals or {}).items():
        fact = facts.get(key)
        if fact is not None and fact.value in excluded:
            return False
    window = question.age_between_when_known
    if window:
        age = facts.get("age_years")
        if (
            age is not None
            and age.assertion == Assertion.PRESENT
            and isinstance(age.value, (int, float))
            and not isinstance(age.value, bool)
            and not window[0] <= age.value <= window[1]
        ):
            return False
    return True


@dataclass(frozen=True)
class Question:
    id: str
    phase: Phase
    fact_key: str
    prompt: str
    input: str
    options: List[QuickReply]
    applies_when: Dict[str, Any]
    # Wave-2 gating metadata. ``blocking=False`` questions stay askable and
    # recordable but never gate review_summary / controller auto-summary.
    # ``excludes_fact_equals`` / ``age_between_when_known`` are evaluated
    # against current facts at candidate-computation time; they are engine
    # metadata and are never forwarded into the model's question catalog.
    blocking: bool = True
    excludes_fact_equals: Dict[str, List[Any]] = field(default_factory=dict)
    age_between_when_known: Optional[List[float]] = None


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
            blocking=bool(raw.get("blocking", True)),
            excludes_fact_equals={
                str(key): list(values)
                for key, values in dict(raw.get("excludes_fact_equals", {})).items()
            },
            age_between_when_known=(
                list(raw["age_between_when_known"])
                if raw.get("age_between_when_known")
                else None
            ),
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

