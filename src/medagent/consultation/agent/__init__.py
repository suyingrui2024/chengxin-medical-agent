"""Package entry point: composes the mixins back into the public class."""

from ._preamble import ConsultationEngine, _capability_advertisement
from .context import AdaptiveConsultationEngineContext
from .core import AdaptiveConsultationEngineCore
from .execute import AdaptiveConsultationEngineExecute
from .pathway import AdaptiveConsultationEnginePathway
from .run import AdaptiveConsultationEngineRun
from .submit import AdaptiveConsultationEngineSubmit
from ...shared_types import ModelBackedEngine


class AdaptiveConsultationEngine(
    AdaptiveConsultationEngineCore,
    AdaptiveConsultationEnginePathway,
    AdaptiveConsultationEngineContext,
    AdaptiveConsultationEngineSubmit,
    AdaptiveConsultationEngineRun,
    AdaptiveConsultationEngineExecute,
    ConsultationEngine,
    ModelBackedEngine
):
    """LLM proposes actions; evidence/type/eligibility gates own every effect."""

    _ADULT_AGE_PROMPT = "你多大？可直接写年龄，例如 32 岁。"


__all__ = [
    "AdaptiveConsultationEngine",
    "_capability_advertisement",
]
