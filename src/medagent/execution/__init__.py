"""Package entry point: re-exports the execution public surface."""

from .errors import ExecutionBusy, ExecutionError
from .workspace_files import (
    open_workspace_directory,
    safe_bytes,
    safe_file,
)
from .session import ExecutionSession
from .session_registry import ExecutionManager

__all__ = [
    "ExecutionBusy",
    "ExecutionError",
    "ExecutionManager",
    "ExecutionSession",
    "open_workspace_directory",
    "safe_bytes",
    "safe_file",
]
