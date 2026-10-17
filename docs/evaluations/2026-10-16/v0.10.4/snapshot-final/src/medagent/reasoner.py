"""Ordering strategy used only by the offline demonstration workflow."""
from __future__ import annotations

from typing import List, Optional, Protocol

from .domain import CaseState
from .knowledge import Question


class QuestionRanker(Protocol):
    def choose(self, state: CaseState, candidates: List[Question]) -> Optional[str]:
        ...


class OrderedQuestionRanker:
    def choose(self, state: CaseState, candidates: List[Question]) -> Optional[str]:
        return candidates[0].id if candidates else None
