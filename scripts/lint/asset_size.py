#!/usr/bin/env python3
"""Large-asset guard.

Why this exists
---------------
The repository already carries 481 MiB in 24 files above ``THRESHOLD``: the
matplotlib/pvlib source tarballs and CPython/pandas wheels the 2026-10
evaluation runs were executed against, four copies of each CJK font, and the
multi-megabyte result JSON. Those are frozen evidence for published numbers —
deleting them would make the snapshots unreproducible, and rewriting history to
move them elsewhere would invalidate every recorded commit id.

So this gate does not ask anyone to shrink the past. It stops the past from
being copied again: a new file above ``THRESHOLD`` fails until someone writes it
down here, which is the moment to ask "does this need to be in git at all?".
Fonts and wheels repeated into yet another snapshot are the exact case it is
meant to catch.

Rules
-----
* Any file in the working tree at or above ``THRESHOLD`` must have a
  ``BASELINE`` entry. Untracked files count too: the point is what a clone
  would download after ``git add``.
* A baselined file may not grow. Snapshots are immutable; growth means somebody
  appended to an artefact that was supposed to be frozen.
* A ``BASELINE`` entry whose file is gone is a warning, not a failure — that is
  what pruning or archiving a snapshot looks like, and the entry should then be
  deleted by hand.
* A new file whose bytes duplicate a frozen one is reported as a copy, not as
  an unexplained asset: the fix is a recorded hash, not another 24 MiB font.

Run locally::

    python scripts/lint/asset_size.py            # gate
    python scripts/lint/asset_size.py --verbose  # plus every duplicate group
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
THRESHOLD = 8 * 1024 * 1024

# Never scanned: not shipped, regenerated, or not ours.
SKIP_DIRS = {
    ".git",
    ".runtime",
    ".workbuddy",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "test-results",
}

# path (relative to repo root) -> size in bytes.  Frozen evidence only.
# Remove an entry once the file has been archived out of the working tree.
BASELINE: dict[str, int] = {
    "docs/evaluations/2026-10-16/v0.10.5-transfer/environment/archives/matplotlib__matplotlib-25498.tar.gz": 37691777,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/archives/matplotlib__matplotlib-25311.tar.gz": 37682384,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/archives/matplotlib__matplotlib-26011.tar.gz": 35415318,
    "docs/evaluations/2026-10-16/v0.9.8/upstream/pvlib__pvlib-python-1707.tar.gz": 35407579,
    "docs/evaluations/2026-10-16/v0.10.2/uniform-supplement.original-report.json": 27434331,
    "docs/evaluations/2026-10-16/v0.10.2/public-report.json": 27434331,
    "docs/evaluations/2026-10-16/v0.10.5-transfer/environment/python/cpython-3.9.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz": 26326845,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/python/cpython-3.9.20+20241002-x86_64-unknown-linux-gnu-install_only.tar.gz": 26326845,
    "src/medagent/web/NotoSerifCJKsc-Regular.otf": 24543080,
    "docs/evaluations/2026-10-17/v0.11.17/source/src/medagent/web/NotoSerifCJKsc-Regular.otf": 24543080,
    "docs/evaluations/2026-10-16/v0.10.2/uniform-supplement.json": 21208898,
    "docs/evaluations/2026-10-16/v0.10.2/supplemental-checkpoint-01.original-report.json": 19210282,
    "docs/evaluations/2026-10-16/v0.10.5-transfer/complete.json": 17692839,
    "src/medagent/web/NotoSansCJKsc-Regular.otf": 16437364,
    "docs/review-packets/2026-10-17-v0.11.17/sources/src/medagent/web/NotoSansCJKsc-Regular.otf": 16437364,
    "docs/evaluations/2026-10-17/v0.11.17/source/src/medagent/web/NotoSansCJKsc-Regular.otf": 16437364,
    "docs/evaluations/2026-10-16/v0.10.2/supplemental-checkpoint-01.json": 15722253,
    "docs/evaluations/2026-10-16/v0.9.8/wheels/pandas-1.5.3-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": 12062104,
    "docs/evaluations/2026-10-16/v0.10.5-transfer/environment/wheels/pandas-1.5.3-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": 12062104,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/wheels/pandas-1.5.3-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": 12062104,
    "docs/evaluations/2026-10-16/v0.10.5-transfer/environment/wheels/matplotlib-3.7.1-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": 11571945,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/wheels/matplotlib-3.7.1-cp310-cp310-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": 11571945,
    "docs/evaluations/2026-10-16/v0.10.5-transfer/environment/wheels/Babel-2.11.0-py3-none-any.whl": 9501187,
    "docs/evaluations/2026-10-16/v0.10.2/inputs/wheels/Babel-2.11.0-py3-none-any.whl": 9501187,
}


def iter_files() -> list[pathlib.Path]:
    """All files, pruning skipped directories instead of walking into them.

    ``node_modules`` and ``.git`` hold enough files that filtering after
    ``rglob`` costs seconds; the gate has to stay under a second.
    """
    files: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            files.append(pathlib.Path(dirpath, name))
    return files


def mib(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MiB"


def digest(path: pathlib.Path) -> str:
    """Content hash, so a copy is reported as a copy rather than a new file.

    Two byte-identical fonts in two snapshots are not two assets; they are one
    asset stored twice. Only files above the threshold are hashed, which keeps
    the gate well under a second on a warm cache.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    total = 0
    by_digest: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    digests: dict[str, str] = {}

    # Two passes: the duplicate check needs every digest before it can say
    # what a file is a copy *of*. Path order must not decide the message.
    for path in sorted(iter_files()):
        rel = path.relative_to(ROOT).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:  # pragma: no cover - unreadable file
            warnings.append(f"{rel}: could not stat ({exc})")
            continue
        if size < THRESHOLD:
            continue

        total += size
        sizes[rel] = size
        try:
            sha = digest(path)
        except OSError as exc:  # pragma: no cover - unreadable file
            warnings.append(f"{rel}: could not hash ({exc})")
            continue
        digests[rel] = sha
        by_digest.setdefault(sha, []).append(rel)

    for rel in sorted(digests):
        size = sizes[rel]
        ceiling = BASELINE.get(rel)
        if ceiling is None:
            twins = [p for p in by_digest[digests[rel]] if p in BASELINE and p != rel]
            if twins:
                failures.append(
                    f"{rel}: {mib(size)} is byte-identical to {twins[0]}; "
                    "record the hash and the source URL instead of a copy"
                )
            else:
                failures.append(
                    f"{rel}: {mib(size)} is a new large file; add it to BASELINE "
                    "only if the snapshot must be reproducible from git"
                )
        elif size > ceiling:
            failures.append(f"{rel}: {mib(size)}, grew past baselined {mib(ceiling)}")
        else:
            seen.add(rel)

    for rel in sorted(set(BASELINE) - seen):
        warnings.append(f"{rel}: baselined file is gone or shrank below threshold")

    # Existing duplicates are the debt this gate exists to stop growing. The
    # total is worth seeing on every run; the per-group detail is only useful
    # when someone is deciding what to archive, hence --verbose.
    duplicate_groups = [
        paths for paths in by_digest.values() if len(paths) > 1
    ]
    redundant = sum(sizes[group[0]] * (len(group) - 1) for group in duplicate_groups)
    if redundant and "--verbose" in sys.argv:
        for paths in sorted(duplicate_groups, key=lambda group: -sizes[group[0]]):
            wasted = sizes[paths[0]] * (len(paths) - 1)
            warnings.append(
                f"{mib(sizes[paths[0]])} stored {len(paths)} times "
                f"({mib(wasted)} redundant): {', '.join(paths)}"
            )

    if warnings:
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)

    if failures:
        print("asset-size check FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            f"\n{len(failures)} large file(s) not accounted for. Prefer a hash "
            "and a download step over a second copy of the bytes.",
            file=sys.stderr,
        )
        return 1

    print(
        f"asset-size check passed: {len(seen)} frozen assets above the "
        f"{mib(THRESHOLD)} ceiling, {mib(total)} total, "
        f"{mib(redundant)} in {len(duplicate_groups)} duplicate group(s) "
        "(--verbose lists them)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
