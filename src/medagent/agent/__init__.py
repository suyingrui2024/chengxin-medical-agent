"""Package entry point: composes the mixins back into the public class."""
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .core import AdaptiveConsultationEngineCore
from .pathway import AdaptiveConsultationEnginePathway
from .context import AdaptiveConsultationEngineContext
from .submit import AdaptiveConsultationEngineSubmit
from .run import AdaptiveConsultationEngineRun
from .execute import AdaptiveConsultationEngineExecute
from ._preamble import ConsultationEngine


class AdaptiveConsultationEngine(
    AdaptiveConsultationEngineCore,
    AdaptiveConsultationEnginePathway,
    AdaptiveConsultationEngineContext,
    AdaptiveConsultationEngineSubmit,
    AdaptiveConsultationEngineRun,
    AdaptiveConsultationEngineExecute,
    ConsultationEngine
):
    """LLM proposes actions; evidence/type/eligibility gates own every effect."""

    _ADULT_AGE_PROMPT = "你多大？可直接写年龄，例如 32 岁。"


__all__ = list(_PREAMBLE_ALL) + [
    "AdaptiveConsultationEngine"
]
