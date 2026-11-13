"""Move frozen evaluation inputs out of the source tree, with verified recovery.

The manifest pins every file to its original Git commit and SHA256. Reports
remain in docs/evaluations; source snapshots, downloaded dependencies and
environments live in a sibling archive. No Git history is rewritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/evaluations/archive-manifest.json"
FROZEN_DIRS = {
    "source",
    "sources",
    "upstream",
    "wheels",
    "environment",
    "inputs",
    "snapshot",
    "snapshots",
    "snapshot-final",
    "frozen-evaluation-source",
}


def digest(path: Path) -> str:
    """Hash the full bytes without loading a large dependency into memory."""
    checksum = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def candidates() -> list[str]:
    """Only frozen, Git-tracked inputs; reports and current fixtures stay put."""
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z", "docs/evaluations"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    return [
        name
        for name in tracked
        if name
        and (ROOT / name).is_file()
        and (
            FROZEN_DIRS.intersection(Path(name).parts[2:-1])
            or any(part.startswith("snapshot-") for part in Path(name).parts[2:-1])
            or name.endswith(
                (".whl", ".tar.gz", ".tar.zst", ".py", ".js", ".mjs", ".cjs", ".ps1", ".sh")
            )
        )
    ]


def archive() -> None:
    """Record the recovery manifest before moving any verified frozen input."""
    previous = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else None
    names = candidates()
    dirty = (
        subprocess.check_output(
            ["git", "diff", "--name-only", "-z", "HEAD", "--", "docs/evaluations"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    if set(dirty).intersection(names):
        raise ValueError("Evaluation files have local changes; refusing to move them")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    if previous and previous["commit"] != commit:
        raise ValueError("Extend an archive only from the original pinned commit")
    target = ROOT.parent / "_archives" / ROOT.name / commit / "files"
    if any((target / name).exists() for name in names):
        raise ValueError("Archive destination already contains an input")
    entries = [
        {"path": name, "bytes": (ROOT / name).stat().st_size, "sha256": digest(ROOT / name)}
        for name in names
    ]
    old_entries = previous["files"] if previous else []
    MANIFEST.write_text(
        json.dumps(
            {"version": 1, "commit": commit, "files": old_entries + entries},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for entry in entries:
        source = ROOT / entry["path"]
        destination = target / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        if digest(destination) != entry["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {entry['path']}")
    print(
        json.dumps(
            {
                "files": len(entries),
                "bytes": sum(e["bytes"] for e in entries),
                "archive": str(target),
            },
            ensure_ascii=False,
        )
    )


def restore(name: str) -> None:
    """Recover a recorded path from the local archive or pinned Git object."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = {entry["path"]: entry for entry in manifest["files"]}
    if name not in entries:
        raise ValueError("Path is not recorded in the archive manifest")
    entry = entries[name]
    destination = ROOT / name
    if not destination.resolve().is_relative_to(ROOT.resolve() / "docs/evaluations"):
        raise ValueError("Recovery path must stay in docs/evaluations")
    if destination.exists():
        if digest(destination) == entry["sha256"]:
            return
        raise ValueError("Destination has different content; refusing to overwrite")
    source = ROOT.parent / "_archives" / ROOT.name / manifest["commit"] / "files" / name
    data = (
        source.read_bytes()
        if source.is_file()
        else subprocess.check_output(["git", "show", f"{manifest['commit']}:{name}"], cwd=ROOT)
    )
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError("Recovery checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    print(f"Restored: {name}")


def verify() -> None:
    """Verify every local archived file against the committed recovery manifest."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = ROOT.parent / "_archives" / ROOT.name / manifest["commit"] / "files"
    seen = set()
    for entry in manifest["files"]:
        name = entry["path"]
        if name in seen:
            raise ValueError("Duplicate archive path")
        seen.add(name)
        source = target / name
        if source.stat().st_size != entry["bytes"] or digest(source) != entry["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {name}")
    print(f"Verified {len(seen)} archived files")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--archive", action="store_true")
    action.add_argument("--restore", metavar="REPOSITORY_PATH")
    action.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.archive:
        archive()
    elif args.restore:
        restore(args.restore)
    elif args.verify:
        verify()
    else:
        names = candidates()
        print(
            json.dumps(
                {"files": len(names), "bytes": sum((ROOT / name).stat().st_size for name in names)}
            )
        )


if __name__ == "__main__":
    main()
