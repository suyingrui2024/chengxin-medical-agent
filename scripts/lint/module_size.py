#!/usr/bin/env python3
"""Module-size guard.

Why this exists
---------------
The 2026-10 modularisation effort split five multi-thousand-line god-modules
(``conclusion``, ``patient_records``, ``subagents``, ``consultation``,
``agent``) into packages of focused submodules.  Without a guard the same debt
would silently regrow, so this check fails CI whenever a first-party source
file drifts past the ceiling.

Rules
-----
* Any first-party ``.py`` / ``.js`` / ``.css`` file under the scanned roots must
  stay at or below ``THRESHOLD`` lines.
* ``BASELINE`` grandfathered files are existing debt: they may not grow beyond
  their recorded ceiling, and each entry should disappear as the file is split.
* Historical snapshots (``docs/evaluations``), vendored runtimes (``.runtime``)
  and build/test artefacts are never scanned.

Run locally::

    python scripts/lint/module_size.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
THRESHOLD = 1200

SCAN_ROOTS = ("src", "tests", "scripts")
SUFFIXES = {".py", ".js", ".css"}
SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".git",
    "build",
    "dist",
    ".runtime",
    "test-results",
    "docs",
}

# path (relative to repo root) -> current line count.  Existing debt only.
# Remove an entry once the file has been split below THRESHOLD.
BASELINE: dict[str, int] = {
    # Frontend god-files — scheduled for P2/P3 (cards/controller split, CSS layers).
    "src/medagent/web/styles.css": 6995,
    "src/medagent/web/app.js": 3613,
    "src/medagent/web/structured-ui.js": 2960,
    "src/medagent/web/patient-records-ui.js": 2154,
}


def iter_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for root_name in SCAN_ROOTS:
        root = ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files.append(path)
    return files


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    files = iter_files()

    for path in sorted(files):
        rel = path.relative_to(ROOT).as_posix()
        try:
            count = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError as exc:  # pragma: no cover - unreadable file
            warnings.append(f"{rel}: could not read ({exc})")
            continue

        ceiling = BASELINE.get(rel)
        if ceiling is not None:
            if count > ceiling:
                failures.append(
                    f"{rel}: {count} lines, grew past baselined ceiling {ceiling}"
                )
            continue

        if count > THRESHOLD:
            failures.append(
                f"{rel}: {count} lines exceeds {THRESHOLD}; "
                "split it or (for existing debt) add a BASELINE entry"
            )

    if warnings:
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)

    if failures:
        print("module-size check FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            f"\n{len(failures)} file(s) over the {THRESHOLD}-line ceiling. "
            "Split the module into focused submodules; do not raise the ceiling.",
            file=sys.stderr,
        )
        return 1

    scanned = len(files)
    baselined = sum(1 for p in files if p.relative_to(ROOT).as_posix() in BASELINE)
    print(
        f"module-size check passed: {scanned} files scanned, "
        f"{baselined} grandfathered, ceiling {THRESHOLD}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
