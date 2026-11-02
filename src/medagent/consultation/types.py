"""Re-export of the layer-crossing contract types.

The definitions moved to ``medagent.shared_types`` so that the kernel, the
feature packages and the app layer can all name them without depending on each
other.  This module stays because the rest of this package imports the four
names from here, which keeps a single place to change if they move again.
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
