"""Host-owned, content-only workspace checkpoints; no source text or imports."""

import hashlib
import json
import sys
import stat
import time
import unicodedata

from .execution_changes import (
    capture, relative_path, watch_path_covers, IGNORED_DIRECTORIES,
)
from .execution_identity import decode_identity
from .platform_fs import os


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


def _capture_paths(directory, identity, paths):
    """Resolve native spelling through pinned parents, without following links."""
    from .execution import open_workspace_directory

    root = open_workspace_directory(directory, identity)
    cache, states, resolved = {}, {}, []
    scanned, started = 0, time.monotonic()
    def fold(name):
        return unicodedata.normalize("NFC", name).casefold()

    def bounded():
        if scanned > 50000 or time.monotonic() - started > 1.5:
            raise OSError("Scope lookup exceeded bounds")

    try:
        for requested in paths:
            if requested == ".":
                resolved.append(".")
                states[requested] = {"path": ".", "kind": "directory"}
                continue
            parent, actual, kind = os.dup(root), [], "missing"
            parts = requested.split("/")
            try:
                for index, component in enumerate(parts):
                    bounded()
                    info = os.fstat(parent)
                    if not info.st_ino:
                        raise OSError("Scope parent identity unavailable")
                    key = info.st_dev, info.st_ino
                    if key not in cache:
                        names = []
                        with os.scandir(parent) as entries:
                            for entry in entries:
                                scanned += 1
                                bounded()
                                names.append(entry.name)
                        cache[key] = names
                    names = cache[key]
                    if component not in names and ":" in component:
                        raise OSError("Drive and alternate stream aliases are not scopes")
                    try:
                        target = os.stat(component, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        actual.extend(parts[index:])
                        kind = "missing"
                        break
                    if (
                        stat.S_ISLNK(target.st_mode)
                        or getattr(target, "st_reparse_tag", 0)
                    ):
                        raise OSError("Linked input scope is not followed")
                    canonical = component if component in names else None
                    if canonical is None:
                        if not target.st_ino:
                            raise OSError("Scope identity unavailable")
                        preferred = [
                            name for name in names if fold(name) == fold(component)
                        ]
                        candidates = preferred + sorted(set(names) - set(preferred))
                        for name in candidates:
                            bounded()
                            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
                                canonical = name
                                break
                        if canonical is None:
                            raise OSError("Scope changed during lookup")
                    actual.append(canonical)
                    kind = "directory" if stat.S_ISDIR(target.st_mode) else "file"
                    if index != len(parts) - 1:
                        if kind != "directory":
                            actual.extend(parts[index + 1:])
                            kind = "missing"
                            break
                        child = os.open(
                            canonical,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent,
                        )
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (
                            target.st_dev, target.st_ino
                        ):
                            os.close(child)
                            raise OSError("Scope parent changed during lookup")
                        os.close(parent)
                        parent = child
                name = "/".join(actual)
                resolved.append(name)
                states[requested] = {"path": name, "kind": kind}
            finally:
                os.close(parent)
    finally:
        os.close(root)
    return normalize_paths(resolved), states


def checkpoint(directory, identity, paths, **limits):
    if (
        not isinstance(identity, (tuple, list))
        or len(identity) != 2
        or any(type(v) is not int or v < 0 for v in identity)
    ):
        raise ValueError("Invalid workspace identity")
    identity = tuple(identity)
    paths = normalize_paths(paths)
    try:
        scoped, scope_state = _capture_paths(directory, identity, paths)
        tree = capture(
            directory, identity, paths=scoped, with_previews=False, **limits
        )
        after_scopes, after_state = _capture_paths(directory, identity, paths)
        complete = (
            tree["complete"] and tree["content_complete"]
            and scoped == after_scopes and scope_state == after_state
            and all(
                state["kind"] != "file" or state["path"] in tree["entries"]
                for state in scope_state.values()
            )
        )
    except OSError:
        scoped, scope_state = paths, {}
        tree = {"entries": {}, "ignored_directories": sorted(IGNORED_DIRECTORIES)}
        complete = False
    if any(
        entry["kind"] == "symlink"
        and any(path == name or path.startswith(name + "/") for path in scoped)
        for name, entry in tree["entries"].items()
    ):
        complete = False
    entries = {
        name: {key: value for key, value in entry.items() if key != "text"}
        for name, entry in tree["entries"].items()
    }
    payload = {"paths": paths, "scopes": scope_state, "entries": entries}
    return {
        "schema": "workspace-checkpoint/v1",
        "paths": paths,
        "sha256": hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        if complete
        else None,
        "complete": complete,
        "files": len(entries),
        "ignored_directories": tree["ignored_directories"],
        "symlink_targets_followed": False,
    }


def main():
    try:
        value = json.load(sys.stdin)
        result = checkpoint(
            value["directory"], decode_identity(value["identity"]), value["paths"]
        )
        print(json.dumps({"ok": True, "result": result}))
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"ok": False, "error": "Checkpoint unavailable"}))


if __name__ == "__main__":
    main()
