"""Public consultation API; implementation and composition live in core.py."""

from .thresholds import SELF_CHECK_SUSPEND_CAP, URGENT_MINIMUM_FACTS
from .vocabulary import (
    VALUE_LABELS,
    is_refusal_text,
    is_steady_not_improving_evidence,
    is_summary_confirmation_text,
    merge_preserving_denials,
)
from . import closing
from .core import ConsultationEngine
from .types import (
    ConsultationBusyError,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
)


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
