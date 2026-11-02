"""Package entry point: composes the mixins back into the public class."""

from ._preamble import (
    SELF_CHECK_SUSPEND_CAP,
    URGENT_MINIMUM_FACTS,
    VALUE_LABELS,
    CalculationFollowupMixin,
    ObservationMixin,
    TaskConversationMixin,
    is_refusal_text,
    is_steady_not_improving_evidence,
    is_summary_confirmation_text,
    merge_preserving_denials,
)
from . import closing
from .calculation import ConsultationEngineCalculation
from .closing_flow import ConsultationEngineClosing
from .core import ConsultationEngineCore
from .emergency import ConsultationEngineEmergency
from .handlers import ConsultationEngineHandlers
from .planning import ConsultationEnginePlanning
from .subject import ConsultationEngineSubject
from .types import (
    ConsultationBusyError,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
)


class ConsultationEngine(
    ConsultationEngineCore,
    ConsultationEngineCalculation,
    ConsultationEngineSubject,
    ConsultationEngineHandlers,
    ConsultationEnginePlanning,
    ConsultationEngineEmergency,
    ConsultationEngineClosing,
    TaskConversationMixin,
    ObservationMixin,
    CalculationFollowupMixin
):
    """Stateful observe-plan-act loop with deterministic clinical guardrails."""

    _CLOSE_SOOTHING = "我听到你说的这些了，先别慌，按下面的步骤来就可以。"


__all__ = [
    "ConsultationBusyError",
    "ConsultationEngine",
    "SELF_CHECK_SUSPEND_CAP",
    "StaleActionError",
    "SubjectBoundaryError",
    "URGENT_MINIMUM_FACTS",
    "UserInput",
    "VALUE_LABELS",
    "closing",
    "is_refusal_text",
    "is_steady_not_improving_evidence",
    "is_summary_confirmation_text",
    "merge_preserving_denials",
]
