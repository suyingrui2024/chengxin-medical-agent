"""Small value / exception types used across the package."""
from ._preamble import *  # noqa: F401,F403


@dataclass(frozen=True)
class UserInput:
    text: str
    choice_value: Optional[str] = None
    expected_action_id: Optional[str] = None
    calculation: Optional[Dict[str, Any]] = None
    observation: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None


class StaleActionError(RuntimeError):
    pass


class SubjectBoundaryError(ValueError):
    def __init__(self, message, requested=None):
        super().__init__(message)
        self.requested = requested


class ConsultationBusyError(RuntimeError):
    pass


__all__ = [
    "UserInput",
    "StaleActionError",
    "SubjectBoundaryError",
    "ConsultationBusyError",
]
