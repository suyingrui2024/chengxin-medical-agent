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
R1  No file whose last name word is a category of leftovers: ``utils``,
    ``helpers``, ``common``, ``shared``, ``misc``, ``stuff``, ``tmp``, ``temp``,
    ``scratch``.  Name the file after the thing it does.

R2  No file with a date in its name -- ``extra``, ``old``, ``new``,
    ``backup``, ``bak``, ``orig``, ``final``, ``late``, ``legacy``, ``tmp``,
    ``temp`` anywhere in the name, or a version word (``v2`` and up, a bare
    ``2``/``3``) at the end.
    ``formulas_extra`` held 34 of the 42 formulas -- it was the main table named
    as if it were the appendix.  ``styles-08-settings-and-late-refinements.css``
    named a review round, not a subject.  A second file means the first one has
    two responsibilities: split them and name both.

R3  Every ``.py`` under ``tests/`` is a test module unless it sits in
    ``support/`` or ``fixtures/``, so it must be named ``test_*.py``.  A
    pytest-style test that does not start with ``test_`` is never collected:
    84 tests sat unrun this way before the suite moved to pytest.  Shared
    fixtures and helpers belong in ``tests/support/``.

R4  No ``test_*.py`` directly under ``tests/``.  The suite is grouped by the
    package it exercises -- ``tests/conclusion/``, ``tests/execution/`` and so
    on -- and a file at the root defeats the point of the grouping: the next
    one lands there too, and 162 loose files is where this started.  ``tests/``
    itself is for ``conftest.py``, ``__init__.py`` and the directories.

The rules apply to ``.py``, ``.js``, ``.mjs``, ``.cjs`` and ``.css``.  They were
Python-only for one round, and in that round the stylesheets grew
``styles-01-tokens-base-execution.css`` and
``styles-08-settings-and-late-refinements.css`` unopposed: eight numbered
slices, four of them welding three subjects together with ``-and-``, one named
after when it was written.  A guard that covers one language guards one
language.

What is *not* checked
---------------------
``core.py`` is allowed.  It reads like a leftover word, but in this repo it is a
consistent convention: ``consultation/core.py``, ``consultation/agent/core.py``
and ``server/core.py`` each hold the base mixin of a class assembled from
several mixin modules, sitting beside ``agent.py``/``patient.py``/``closing.py``
by design.  Renaming those would desynchronise them from the class names they
define (``HandlerCore``, ``ConsultationEngineCore``) without making any of them
easier to find.

``_and_`` in the middle of a name is not checked either.  ``test_worry_and_goal.py``
tests worry and goal; the ``-and-`` problem was specific to stylesheets, where it
welded three unrelated subjects into one file *name* while the file itself was
only a size slice.  The fix there was to name the slice after its leading topic,
not to ban the word.

``copy`` is left out of R2 on purpose.  The first run of this check flagged
``test_close_copy.py``, which tests the *close copy* -- the wording the
consultation says when it closes -- not a copy of something.  ``copy`` is a
domain word here, so a rule against it would be wrong more often than right;
``old``/``backup``/``bak`` cover the copy-of-a-file case.

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

SCAN_ROOTS = ("src", "tests", "scripts", "desktop", "integrations")
EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".css"}
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
LEFTOVER_WORDS = frozenset(
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

# R2: words that date a file instead of describing it.
TIME_WORDS = frozenset(
    {"extra", "old", "new", "backup", "bak", "orig", "final", "late", "legacy", "tmp", "temp"}
)

# R2: ``v2``..``v9``, and a bare trailing version number.
VERSION_WORD = re.compile(r"^(?:v[2-9]\d*|[2-9])$")

# R3: the only files allowed under tests/ without a test_ prefix.
TESTS_ALLOWED = frozenset({"conftest.py", "__init__.py"})

#: Directories under ``tests/`` that hold helpers rather than collected tests.
TESTS_HELPER_DIRS = frozenset({"support", "fixtures"})

#: R4: the areas a test module may live in, mirroring ``src/medagent``.
TEST_AREAS = (
    "architecture",
    "clinical",
    "conclusion",
    "consultation",
    "dialogue",
    "execution",
    "extensions",
    "integration",
    "kernel",
    "records",
    "reports",
    "server",
    "subagents",
)

#: The stem is split on every separator so ``date-utils`` and ``date_utils``
#: are caught by the same rule, and a leading digit stays attached to nothing.
WORD_SPLIT = re.compile(r"[-_.]+")


def name_words(path: pathlib.Path) -> list[str]:
    """Lower-case words of a file name, in order, separators removed."""
    return [word.lower() for word in WORD_SPLIT.split(path.stem) if word]


def iter_source_files(root: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root_name in SCAN_ROOTS:
        base = root / root_name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in EXTENSIONS:
                continue
            if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
                continue
            files.append(path)
    return files


def check(path: pathlib.Path, root: pathlib.Path) -> str | None:
    rel = path.relative_to(root).as_posix()
    words = name_words(path)
    last = words[-1] if words else ""

    if last in LEFTOVER_WORDS:
        return (
            f"{rel}: '{last}' names a category of leftovers, not a thing. "
            "Name the file after what it does."
        )

    # Any word, not just the last one. The name this rule was written for was
    # ``styles-08-settings-and-late-refinements.css``; a suffix-only check would
    # have watched it go by, because the date sat in the middle.
    dated = [word for word in words if word in TIME_WORDS]
    if dated:
        return (
            f"{rel}: '{dated[0]}' records when the file was written, not what "
            "it holds. Name both halves of the split, or merge them."
        )

    if VERSION_WORD.match(last):
        return (
            f"{rel}: '{last}' records when the file was written, not what it "
            "holds. Name both halves of the split, or merge them."
        )

    parts = path.relative_to(root).parts
    if parts[:1] != ("tests",) or path.suffix != ".py":
        return None

    if path.name in TESTS_ALLOWED or parts[1] in TESTS_HELPER_DIRS:
        return None

    if not path.name.startswith("test_"):
        return (
            f"{rel}: a module under tests/ must be named test_*.py or pytest "
            "will never collect it. Move shared helpers to tests/support/."
        )

    if len(parts) == 2:
        return (
            f"{rel}: tests/ is not a place for test modules. Put it under the "
            "directory of the package it exercises (" + ", ".join(TEST_AREAS) + ")."
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

    files = iter_source_files(root)
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

    print(f"naming check passed: {len(files)} file(s) scanned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
