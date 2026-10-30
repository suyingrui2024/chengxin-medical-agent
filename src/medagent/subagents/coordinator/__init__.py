"""Package entry point: composes the mixins back into the public class."""
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .lifecycle import CoordinatorLifecycle
from .branch import CoordinatorBranch
from .validate import CoordinatorValidate


class SubagentCoordinator(
    CoordinatorLifecycle,
    CoordinatorBranch,
    CoordinatorValidate
):
    """Spawns, budgets and validates bounded subagent runs (design §5)."""

    pass


__all__ = list(_PREAMBLE_ALL) + [
    "SubagentCoordinator"
]
