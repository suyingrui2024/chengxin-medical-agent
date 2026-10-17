from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Assertion(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class Source(str, Enum):
    PATIENT = "patient"
    CAREGIVER = "caregiver"
    DEVICE = "device"
    CAMERA = "camera"
    SYSTEM_INFERENCE = "system_inference"


class ObservationQuality(str, Enum):
    NOT_ASSESSED = "not_assessed"
    ADEQUATE = "adequate"
    INADEQUATE = "inadequate"
    NOT_DONE = "not_done"


class RiskLevel(str, Enum):
    EMERGENCY = "emergency"
    URGENT = "urgent"
    ROUTINE = "routine"
    UNDETERMINED = "undetermined"


class Phase(str, Enum):
    OPENING = "opening"
    EMERGENCY_SCREEN = "emergency_screen"
    HISTORY = "history"
    BACKGROUND = "background"
    OBSERVATION = "observation"
    SUMMARY = "summary"
    CLOSED = "closed"


@dataclass
class Fact:
    key: str
    value: Any
    assertion: Assertion
    source: Source
    turn_id: str
    recorded_at: str = field(default_factory=utc_now)
    quality: ObservationQuality = ObservationQuality.NOT_ASSESSED
    supersedes: Optional[str] = None
    fact_id: str = field(default_factory=lambda: str(uuid4()))
    evidence: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["assertion"] = self.assertion.value
        result["source"] = self.source.value
        result["quality"] = self.quality.value
        return result


@dataclass
class RiskAssessment:
    level: RiskLevel = RiskLevel.UNDETERMINED
    triggers: List[str] = field(default_factory=list)
    assessed_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "triggers": list(self.triggers),
            "assessed_at": self.assessed_at,
        }


@dataclass
class CaseState:
    session_id: str
    phase: Phase = Phase.OPENING
    facts: Dict[str, Fact] = field(default_factory=dict)
    history: List[Fact] = field(default_factory=list)
    asked_questions: List[str] = field(default_factory=list)
    active_question: Optional[str] = None
    active_question_is_clarification: bool = False
    active_task: Optional[str] = None
    risk: RiskAssessment = field(default_factory=RiskAssessment)
    closed_reason: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    revision: int = 0
    transcript: List[Dict[str, str]] = field(default_factory=list)
    followup_counts: Dict[str, int] = field(default_factory=dict)
    pending_input: Optional[str] = None
    runtime_snapshot: Dict[str, Any] = field(default_factory=dict)
    calculations: List[Dict[str, Any]] = field(default_factory=list)
    pending_self_checks: List[Dict[str, Any]] = field(default_factory=list)
    pending_calculation: Optional[Dict[str, Any]] = None
    calculation_resume: Optional[Dict[str, Any]] = None
    observation_resume: Optional[Dict[str, Any]] = None
    observation_attempts: List[Dict[str, Any]] = field(default_factory=list)
    observation_feedback: Optional[Dict[str, Any]] = None

    def record(self, fact: Fact) -> None:
        previous = self.facts.get(fact.key)
        if previous:
            fact.supersedes = previous.fact_id
        self.facts[fact.key] = fact
        self.history.append(fact)
        self.updated_at = utc_now()

    def get(self, key: str, default: Any = None) -> Any:
        fact = self.facts.get(key)
        if not fact or fact.assertion == Assertion.UNKNOWN:
            return default
        return fact.value

    def assertion(self, key: str) -> Assertion:
        fact = self.facts.get(key)
        return fact.assertion if fact else Assertion.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "facts": {key: fact.to_dict() for key, fact in self.facts.items()},
            "history": [fact.to_dict() for fact in self.history],
            "asked_questions": list(self.asked_questions),
            "active_question": self.active_question,
            "active_question_is_clarification": self.active_question_is_clarification,
            "active_task": self.active_task,
            "risk": self.risk.to_dict(),
            "closed_reason": self.closed_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revision": self.revision,
            "runtime_snapshot": self.runtime_snapshot,
            "calculations": list(self.calculations),
            "pending_calculation": self.pending_calculation,
            "pending_self_checks": [dict(item) for item in self.pending_self_checks],
            "observation_attempts": [dict(item) for item in self.observation_attempts],
        }


@dataclass
class QuickReply:
    label: str
    value: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class AgentReply:
    message: str
    phase: Phase
    kind: str = "question"
    question_id: Optional[str] = None
    quick_replies: List[QuickReply] = field(default_factory=list)
    task: Optional[Dict[str, Any]] = None
    summary: Optional[Dict[str, Any]] = None
    developer_trace: Optional[Dict[str, Any]] = None
    action_id: Optional[str] = None
    input_error: bool = False
    calculation_input: Optional[Dict[str, Any]] = None
    calculation_result: Optional[Dict[str, Any]] = None
    observation_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        from .presentation import reply_presentation

        payload = {
            "message": self.message,
            "phase": self.phase.value,
            "kind": self.kind,
            "question_id": self.question_id,
            "quick_replies": [item.to_dict() for item in self.quick_replies],
            "task": self.task,
            "summary": self.summary,
            "developer_trace": self.developer_trace,
            "action_id": self.action_id,
            "input_error": self.input_error,
            "calculation_input": self.calculation_input,
            "calculation_result": self.calculation_result,
            "observation_result": self.observation_result,
        }
        payload["presentation"] = reply_presentation(payload)
        return payload
