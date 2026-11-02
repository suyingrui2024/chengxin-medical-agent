"""Package entry point: composes the mixins back into the public class."""

from .branch import CoordinatorBranch
from .lifecycle import CoordinatorLifecycle
from .validate import CoordinatorValidate


class SubagentCoordinator(
    CoordinatorLifecycle,
    CoordinatorBranch,
    CoordinatorValidate
):
    """Spawns, budgets and validates bounded subagent runs (design §5)."""

    pass


__all__ = [
    "SubagentCoordinator",
]
