"""Test-only workspace roots for persistent Windows ProcessContainer sessions."""

import os
from pathlib import Path
import tempfile


def process_container_temporary_directory(prefix):
    """Return a disposable test root without treating durable session ACLs as failures.

    Windows BaseContainer grants the sandbox direct filesystem access without
    mutating host ACLs. DSH intentionally keeps its durable session history in
    that sandbox-owned state tree. Python can therefore complete every test
    assertion yet be unable to recursively remove that history from the host.
    Keep the small, test-only remainder on the project drive; production
    workspaces persist this same history deliberately. Other platforms retain
    the normal ``TemporaryDirectory`` cleanup behavior.
    """

    options = {"prefix": prefix}
    if os.name == "nt":
        root = (
            Path(__file__).resolve().parents[1]
            / ".runtime"
            / "windows"
            / "process-container-test-state"
        )
        root.mkdir(parents=True, exist_ok=True)
        options.update(dir=root, ignore_cleanup_errors=True)
    return tempfile.TemporaryDirectory(**options)
