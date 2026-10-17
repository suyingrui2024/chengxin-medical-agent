"""Host-owned, content-only workspace checkpoints; no source text or imports."""

import hashlib
import json
import sys

from .execution_changes import capture, relative_path, watch_path_covers
from .execution_identity import decode_identity


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


def checkpoint(directory, identity, paths, **limits):
    if (
        not isinstance(identity, (tuple, list))
        or len(identity) != 2
        or any(type(v) is not int or v < 0 for v in identity)
    ):
        raise ValueError("Invalid workspace identity")
    identity = tuple(identity)
    paths = normalize_paths(paths)
    tree = capture(directory, identity, paths=paths, with_previews=False, **limits)
    complete = tree["complete"] and tree["content_complete"]
    if any(
        entry["kind"] == "symlink"
        and any(path == name or path.startswith(name + "/") for path in paths)
        for name, entry in tree["entries"].items()
    ):
        complete = False
    entries = {
        name: {key: value for key, value in entry.items() if key != "text"}
        for name, entry in tree["entries"].items()
    }
    payload = {"paths": paths, "entries": entries}
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
