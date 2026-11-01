"""Value and error types that cross layer boundaries.

``UserInput`` and the three error types are the contract between a transport
layer (the HTTP handlers, the desktop shell) and an engine.  They used to live
in ``consultation/types.py``, which forced every layer that names them to
depend on the ``consultation`` feature package:

* the kernel modules ``location_revisions``, ``observations`` and
  ``task_conversation`` had to late-bind ``StaleActionError`` from
  ``consultation`` — a shared-kernel module reaching into a feature package,
  which the layering rules (R6) forbid;
* ``agent`` and ``server`` picked the same names up through
  ``from ..consultation import ...``, so the engine contract and the
  consultation workflow could not be changed independently.

They are data and errors, not behaviour, so they belong below every layer.
The package imports nothing from ``medagent`` and can therefore be depended
on by the kernel, by any feature package and by the app layer alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class UserInput:
    """One submitted user action.

    ``expected_action_id`` is what makes a stale write-back detectable: the
    engine compares it against the action it last offered, and raises
    ``StaleActionError`` when the page has moved on.
    """

    text: str
    choice_value: Optional[str] = None
    expected_action_id: Optional[str] = None
    calculation: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None


class StaleActionError(RuntimeError):
    """The submitted action refers to a page or card that has since changed."""


class SubjectBoundaryError(ValueError):
    """The request crossed the consultation-subject boundary."""

    def __init__(self, message, requested=None):
        super().__init__(message)
        self.requested = requested


class ConsultationBusyError(RuntimeError):
    """The engine is already busy with another turn."""


class ModelBackedEngine:
    """Marker for engines that carry a model backend.

    ``observations.ObservationMixin`` has to know whether it is mixed into the
    deterministic engine or the adaptive one, because only the adaptive one
    may take the model-backed branches.  It used to answer that with
    ``isinstance(self, AdaptiveConsultationEngine)``, which made a kernel
    mixin import the ``agent`` feature package — again a kernel -> feature
    dependency (R6).

    A marker base class keeps the "judge by type, not by a stray attribute"
    property while letting the question be asked from the kernel's own
    vocabulary: inheriting this class *is* the declaration.
    """


__all__ = [
    "UserInput",
    "StaleActionError",
    "SubjectBoundaryError",
    "ConsultationBusyError",
    "ModelBackedEngine",
]
