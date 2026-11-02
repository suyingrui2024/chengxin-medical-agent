"""Package entry point: composes the mixins back into the public class."""
# Facade re-export: the package surface is the explicit `__all__` below, so the
# star is the one place that list is assembled. These are the only star
# imports left in the tree -- every other module imports what it uses.
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .core import AdaptiveConsultationEngineCore
from .pathway import AdaptiveConsultationEnginePathway
from .context import AdaptiveConsultationEngineContext
from .submit import AdaptiveConsultationEngineSubmit
from .run import AdaptiveConsultationEngineRun
from .execute import AdaptiveConsultationEngineExecute
from ._preamble import ConsultationEngine
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


__all__ = list(_PREAMBLE_ALL) + [
    "AdaptiveConsultationEngine"
]
