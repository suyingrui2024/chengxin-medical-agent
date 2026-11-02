#!/usr/bin/env python3
"""File-naming guard.

Why this exists
---------------
``module_size.py`` bounds how big a file may get and ``import_rules.py`` bounds
who may import whom.  Neither notices a file that is the right size in the right
layer but is named ``utils``.  A name is the only thing a reader has before
opening the file, so a name that describes nothing guarantees the file becomes
whatever the next caller needs -- which is how a ``utils.py`` grows to 900 lines
and takes the architecture with it.

The rules here are deliberately narrow.  This is not a style checker; it rejects
only names that carry no information, plus the two naming mistakes that have
already cost this repo real time.

Rules
-----
R1  No files named after a category of leftovers: ``utils``, ``helpers``,
    ``common``, ``shared``, ``misc``, ``stuff``, ``tmp``, ``temp``, ``scratch``.
    Name the file after the thing it does.

R2  No batch or version suffix on a source file: ``_extra``, ``_old``, ``_new``,
    ``_copy``, ``_backup``, ``_bak``, ``_orig``, ``_final``, ``_tmp``, ``_temp``,
    ``_v2`` and up, or a bare ``_2``/``_3``.  ``formulas_extra`` held 34 of the
    42 formulas -- it was the main table named as if it were the appendix -- and
    "extra" told no reader that.  A second file means the first one has two
    responsibilities; split them and name both.

R3  Every ``.py`` directly under ``tests/`` is a test module, so it must be
    named ``test_*.py``.  A pytest-style test that does not start with ``test_``
    is never collected: 84 tests sat unrun this way before the suite moved to
    pytest.  Shared fixtures and helpers belong in ``tests/support/``, where
    this rule does not apply.

What is *not* checked
---------------------
``core.py`` is allowed.  It reads like a leftover word, but in this repo it is a
consistent convention: ``consultation/core.py``, ``consultation/agent/core.py``
and ``server/core.py`` each hold the base mixin of a class assembled from
several mixin modules, sitting beside ``agent.py``/``patient.py``/``closing.py``
by design.  Renaming those would desynchronise them from the class names they
define (``HandlerCore``, ``ConsultationEngineCore``) without making any of them
easier to find.

Run locally::

    python scripts/lint/naming.py

Pass ``--root <dir>`` to point the check at a fixture tree; the test-suite uses
that to prove each rule actually fires.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SCAN_ROOTS = ("src", "tests", "scripts")
SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".git",
    ".venv",
    "build",
    "dist",
    ".runtime",
    "test-results",
    "docs",
}

# R1: names that describe a category of leftovers rather than a thing.
LEFTOVER_STEMS = frozenset(
    {
        "utils",
        "util",
        "helpers",
        "helper",
        "common",
        "shared",
        "misc",
        "stuff",
        "tmp",
        "temp",
        "scratch",
        "miscellaneous",
    }
)

# R2: suffixes that date a file instead of describing it.
#
# ``_copy`` is deliberately absent. The first run of this check flagged
# ``test_close_copy.py``, which tests the *close copy* -- the wording the
# consultation says when it closes -- not a copy of something. In this codebase
# "copy" is a domain word, so a rule against it would be wrong more often than
# right; ``_old``/``_backup``/``_bak`` cover the copy-of-a-file case.
BATCH_SUFFIX = re.compile(
    r"_(extra|old|new|backup|bak|orig|final|tmp|temp|v[2-9]\d*|[2-9])$"
)

# R3: the only files allowed to sit directly in tests/ without a test_ prefix.
TESTS_ALLOWED = frozenset({"conftest.py", "__init__.py"})


def iter_module_files(root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root_name in SCAN_ROOTS:
        base = root / root_name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def check(path: pathlib.Path, root: pathlib.Path) -> str | None:
    rel = path.relative_to(root).as_posix()
    stem = path.stem

    if stem in LEFTOVER_STEMS:
        return (
            f"{rel}: '{stem}' names a category of leftovers, not a thing. "
            "Name the file after what it does."
        )

    match = BATCH_SUFFIX.search(stem)
    if match:
        return (
            f"{rel}: '{match.group(0).lstrip('_')}' suffix records when the file "
            "was written, not what it holds. Name both halves of the split, or "
            "merge them."
        )

    parts = path.relative_to(root).parts
    if parts[:1] == ("tests",) and len(parts) == 2 and path.name not in TESTS_ALLOWED:
        if not path.name.startswith("test_"):
            return (
                f"{rel}: a module directly under tests/ must be named test_*.py "
                "or pytest will never collect it. Move shared helpers to "
                "tests/support/."
            )

    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="File-naming guard.")
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=REPO_ROOT,
        help="tree to scan (defaults to the repository root)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    files = iter_module_files(root)
    failures = [msg for path in sorted(files) if (msg := check(path, root))]

    if failures:
        print("naming check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            f"\n{len(failures)} file(s) misnamed. A file name is the reader's "
            "only map; see the rules at the top of this script.",
            file=sys.stderr,
        )
        return 1

    print(f"naming check passed: {len(files)} module(s) scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
