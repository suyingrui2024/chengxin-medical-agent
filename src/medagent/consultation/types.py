"""Re-export of the layer-crossing contract types.

The definitions moved to ``medagent.shared_types`` so that the kernel, the
feature packages and the app layer can all name them without depending on each
other.  This module stays because eight modules in this package reach the
types through ``from .types import *``.
"""
from ..shared_types import (
    ConsultationBusyError,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
)


__all__ = [
    "UserInput",
    "StaleActionError",
    "SubjectBoundaryError",
    "ConsultationBusyError",
]
