"""Package entry point: composes the mixins back into the public class."""
# Facade re-export: the package surface is the explicit `__all__` below, so the
# star is the one place that list is assembled. These are the only star
# imports left in the tree -- every other module imports what it uses.
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
