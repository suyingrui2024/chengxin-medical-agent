"""Package entry point: re-exports the execution public surface."""
# Facade re-export: the package surface is the explicit `__all__` below, so the
# star is the one place that list is assembled. These are the only star
# imports left in the tree -- every other module imports what it uses.
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .session import ExecutionSession
from .session_registry import ExecutionManager


__all__ = list(_PREAMBLE_ALL) + [
    "ExecutionSession",
    "ExecutionManager",
]
