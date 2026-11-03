"""The errors the execution package raises.

Two kinds, and the difference matters to a caller: ``ExecutionError`` means
the request was refused and will be refused again, while ``ExecutionBusy``
means the workspace is in use right now and the same request may succeed
later.

Both are ``ValueError`` subclasses so that the existing ``except ValueError``
handlers in the transport layer keep catching them.
"""
from __future__ import annotations


class ExecutionError(ValueError):
    pass

class ExecutionBusy(ExecutionError):
    pass


__all__ = [
    "ExecutionBusy",
    "ExecutionError",
]
