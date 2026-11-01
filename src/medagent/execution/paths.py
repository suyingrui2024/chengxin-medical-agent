"""Workspace path vocabulary shared by changes, checkpoints and observations.

These helpers used to live in ``changes``.  ``observations`` imported them
inside functions to dodge the resulting cycle, which meant the dependency was
real but invisible: the same three modules still could not be loaded
independently.  Path rules are the lowest layer here — nothing in this module
imports a sibling — so the cycle goes away instead of being hidden.
"""

from __future__ import annotations

from pathlib import PurePosixPath


class ChangeError(ValueError):
    pass


IGNORED_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


def watch_path_covers(parent, path):
    if parent == path:
        return True
    if parent == ".":
        suffix = path
    elif path.startswith(parent + "/"):
        suffix = path[len(parent) + 1 :]
    else:
        return False
    return not any(part in IGNORED_DIRECTORIES for part in suffix.split("/"))


def relative_path(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 1024
        or "\\" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(ord(c) < 32 for c in value)
    ):
        raise ChangeError("文件路径无效")
    return PurePosixPath(value)


def normalize_paths(paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 16:
        raise ValueError("Invalid checkpoint paths")
    for path in paths:
        if path != ".":
            relative_path(path)
    ordered = sorted(set(paths))
    return [
        p
        for p in ordered
        if not any(watch_path_covers(q, p) for q in ordered if q != p)
    ]
