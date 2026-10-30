"""Package entry point: composes the mixins back into the public class."""
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .types import *  # noqa: F401,F403
from .types import UserInput, StaleActionError, SubjectBoundaryError, ConsultationBusyError
from .core import ConsultationEngineCore
from .calculation import ConsultationEngineCalculation
from .subject import ConsultationEngineSubject
from .handlers import ConsultationEngineHandlers
from .planning import ConsultationEnginePlanning
from .emergency import ConsultationEngineEmergency
from .closing_flow import ConsultationEngineClosing
from ._preamble import TaskConversationMixin, ObservationMixin, CalculationFollowupMixin


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


__all__ = list(_PREAMBLE_ALL) + [
    "UserInput",
    "StaleActionError",
    "SubjectBoundaryError",
    "ConsultationBusyError",
    "ConsultationEngine"
]
