#!/usr/bin/env python3
"""Version guard.

Why this exists
---------------
The version lived in four places at once: ``package.json``, ``pyproject.toml``,
``src/medagent/__init__.py`` and a ``policy_version`` string stamped into every
``agent.started`` event.  Nothing compared them, so a release could bump three
of the four and leave the fourth behind -- and the fourth is the one an auditor
reads.  A stale policy version says the clinical policy in force was a
different one from the code that ran.

Now ``src/medagent/__init__.py`` is the source, the stamped policy version is
derived from it at runtime, and this check keeps the two manifest files -- which
tools read without importing the package -- in step with it.

Run locally::

    python scripts/lint/version.py
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
INIT = ROOT / "src" / "medagent" / "__init__.py"
PACKAGE_JSON = ROOT / "package.json"
PYPROJECT = ROOT / "pyproject.toml"


def source_version(root: pathlib.Path = ROOT) -> str | None:
    """The version from the single source of truth, read without importing it."""
    try:
        tree = ast.parse((root / "src/medagent/__init__.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(
                        node.value.value, str
                    ):
                        return node.value.value
    return None


def package_json_version(root: pathlib.Path = ROOT) -> str | None:
    try:
        return json.loads((root / "package.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def pyproject_version(root: pathlib.Path = ROOT) -> str | None:
    """The version under ``[project]``.

    Parsed with a regex rather than ``tomllib`` because the project supports
    Python 3.10 and ``tomllib`` arrived in 3.11.
    """
    try:
        text = (root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    section = re.search(r"^\[project\]$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return None
    found = re.search(r'^version\s*=\s*"([^"]+)"', section.group(1), re.M)
    return found.group(1) if found else None


def stamped_policy_versions(root: pathlib.Path = ROOT) -> list[tuple[pathlib.Path, int, str]]:
    """Every hardcoded ``medical-tools-vX.Y.Z`` still in the source tree."""
    found = []
    for path in sorted((root / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"medical-tools-v\d+\.\d+\.\d+", line):
                found.append((path, number, match.group(0)))
    return found


def check(root: pathlib.Path) -> list[str]:
    """Every way the four declarations can disagree. Empty means consistent."""
    failures: list[str] = []
    version = source_version(root)
    if not version:
        return ["cannot read src/medagent/__init__.py: no __version__"]

    manifest = package_json_version(root)
    if manifest != version:
        failures.append(
            f"package.json: version {manifest!r} does not match the source "
            f"of truth ({version!r})"
        )
    manifest = pyproject_version(root)
    if manifest != version:
        failures.append(
            f"pyproject.toml: version {manifest!r} does not match the source "
            f"of truth ({version!r})"
        )


    for path, number, stamped in stamped_policy_versions(root):
        rel = path.relative_to(root)
        failures.append(
            f"{rel}:{number}: hardcoded {stamped!r} -- derive it from "
            "__version__ so an audit record cannot claim a policy the code "
            "did not run"
        )
    return failures


def main() -> int:
    failures = check(ROOT)
    if failures:
        print("version check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            "\nThe version is declared once, in src/medagent/__init__.py. "
            "Update the manifests to match it.",
            file=sys.stderr,
        )
        return 1

    version = source_version(ROOT)
    print(f"version check passed: {version} (source of truth: src/medagent/__init__.py).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
