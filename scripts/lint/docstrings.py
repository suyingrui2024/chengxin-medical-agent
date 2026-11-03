#!/usr/bin/env python3
"""Docstring guard.

Why this exists
---------------
The code is well shaped now: 1240 functions with a 16-line median, no god
package, no import cycles.  What it does not have is a word of intent.  Only
35% of functions carry a docstring, 242 functions of 25 lines or more carry
none, and the missing ones include ``_run``, ``_context`` and
``_tool_spawn_subagent`` -- the functions a newcomer opens first.

A docstring earns its place only when it says something the code cannot: what
invariant holds, what side effect happens, when not to call this.  Restating
the code is worse than nothing, so the threshold is deliberately low (25 lines)
and the requirement is deliberately narrow -- a short function is readable from
its code, and a long one is not.

Rules
-----
* Every module under ``src/medagent`` says what it is for.
* Every function of 25 lines or more says what it guarantees, what it touches,
  and when not to call it.
* Missing ones are grandfathered in ``BASELINE`` and can only be struck off,
  never added to -- the same shape as ``function_size.py``.

Run locally::

    python scripts/lint/docstrings.py
    python scripts/lint/docstrings.py --freeze
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_ROOT = ROOT / "src" / "medagent"

#: A function at or above this many lines has to explain itself.
LINE_THRESHOLD = 25

#: ``module::Qualified.name`` -> the line count when it was grandfathered.
#: Existing debt only: remove an entry once the function has a docstring.
BASELINE: dict[str, int] = {
    "subagents/coordinator/branch.py::CoordinatorBranch._run_spawn": 243,
    "subagents/coordinator/branch.py::CoordinatorBranch._run_branch": 207,
    "patient_records/store/schema.py::StoreSchema._initialize": 183,
    "core/presentation.py::reply_presentation": 143,
    "patient_records/store/assets.py::StoreAssets.finish_recognition": 107,
    "review/background_review.py::BackgroundReviewCoordinator._finish_success": 94,
    "subagents/coordinator/validate.py::CoordinatorValidate._validate_digest": 94,
    "subagents/coordinator/branch.py::CoordinatorBranch._build_view": 88,
    "subagents/coordinator/validate.py::CoordinatorValidate._validate_report": 88,
    "patient_records/store/assets.py::StoreAssets.revise_observation": 87,
    "patient_records/store/assets.py::StoreAssets.add_measurement": 78,
    "desktop_service.py::main": 75,
    "patient_records/store/queries.py::StoreQueries.glucose_trend": 75,
    "subagents/coordinator/branch.py::CoordinatorBranch._execute_tool": 67,
    "core/domain.py::CaseState.to_dict": 65,
    "patient_records/store/assets.py::StoreAssets.create_image_entry": 65,
    "patient_records/store/queries.py::StoreQueries.timeline": 57,
    "review/background_review.py::BackgroundReviewCoordinator._run": 57,
    "patient_records/store/queries.py::StoreQueries.entry": 53,
    "core/decision_wait.py::DecisionWaiter.invoke": 52,
    "review/background_review.py::BackgroundReviewCoordinator._snapshot": 51,
    "guards/red_flags.py::RedFlagScanner._combo_hit": 50,
    "patient_records/store/assets.py::StoreAssets._insert_observation": 49,
    "subagents/coordinator/lifecycle.py::CoordinatorLifecycle.__init__": 49,
    "kg/store.py::ConceptStore.add": 48,
    "review/background_review.py::BackgroundReviewCoordinator._finish_failure": 42,
    "desktop_service.py::main.watch_parent": 41,
    "guards/red_flags.py::RedFlagScanner.scan": 38,
    "patient_records/store/queries.py::StoreQueries.delete_entry": 38,
    "core/presentation.py::calculation_presentation": 37,
    "review/release_evaluation.py::validate_case": 36,
    "core/text_match.py::_occurrence_negated": 34,
    "subagents/coordinator/branch.py::CoordinatorBranch._branch_shell": 34,
    "guards/red_flags.py::load_definitions": 32,
    "kg/normalize.py::Normalizer.find": 32,
    "subagents/coordinator/branch.py::CoordinatorBranch._usage_for": 31,
    "core/domain.py::AgentReply.to_dict": 29,
    "patient_records/recognition_jobs.py::_run_job_in_thread": 29,
    "review/release_evaluation.py::evaluate_cases": 29,
    "infra/web_tools.py::WebTools.__init__": 28,
    "core/events.py::EventStore.append": 27,
    "guards/red_flags.py::RedFlagScanner._combo_is_discriminated": 27,
    "review/audit_recording.py::RecordingBackend._record": 27,
    "patient_records/store/assets.py::StoreAssets.answer_clarification": 26,
    "subagents/coordinator/validate.py::CoordinatorValidate._validate_followup": 26,
    "guards/red_flags.py::RedFlagScanner._pattern_hit": 25,

}

#: Modules without a module docstring, grandfathered.  Remove an entry once
#: the module says what it is for.
MODULE_BASELINE: set[str] = {
    "cli.py",
    "guards/self_checks.py",
    "patient_records/constants.py",
    "patient_records/validation.py",
    "subagents/constants.py",
    "subagents/content.py",
    "subagents/token_est.py",
}


def qualified_name(node: ast.AST, prefix: str = "") -> str:
    """``Class.method`` -> ``Class.method``, nested functions included."""
    return prefix + node.name  # type: ignore[attr-defined]


def functions(path: pathlib.Path) -> list[tuple[str, int, bool]]:
    """Every function in one file as ``(qualified name, lines, documented)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, int, bool]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (child.end_lineno or child.lineno) - child.lineno + 1
                found.append(
                    (f"{prefix}{child.name}", span, bool(ast.get_docstring(child)))
                )
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def measure(
    root: pathlib.Path | None = None,
) -> tuple[dict[str, tuple[int, bool]], set[str]]:
    """Every function and every module, keyed for the checks."""
    scan_root = root or SCAN_ROOT
    sizes: dict[str, tuple[int, bool]] = {}
    undocumented_modules: set[str] = set()
    for path in sorted(scan_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = str(path.relative_to(scan_root).as_posix())
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not ast.get_docstring(tree):
            undocumented_modules.add(module)
        for name, span, documented in functions(path):
            sizes[f"{module}::{name}"] = (span, documented)
    return sizes, undocumented_modules


def check(
    sizes: dict[str, tuple[int, bool]],
    undocumented_modules: set[str],
    baseline: dict[str, int],
    module_baseline: set[str],
) -> tuple[list[str], list[str]]:
    """Apply the rules.  Returns ``(failures, warnings)``."""
    failures: list[str] = []
    warnings: list[str] = []

    for module in sorted(undocumented_modules):
        if module in module_baseline:
            continue
        failures.append(
            f"{module}: no module docstring -- say what this module guarantees "
            "and where to start reading it"
        )

    for key, (span, documented) in sorted(sizes.items()):
        if span < LINE_THRESHOLD:
            continue
        if documented:
            if key in baseline:
                warnings.append(f"{key}: documented now -- strike it off")
            continue
        if key in baseline:
            continue
        failures.append(
            f"{key}: {span} lines with no docstring -- state what it "
            "guarantees, what it touches, and when not to call it"
        )

    for key in sorted(set(baseline) - set(sizes)):
        warnings.append(f"{key}: baselined but no longer exists -- update the entry")
    for module in sorted(module_baseline - undocumented_modules):
        warnings.append(f"{module}: documented now -- strike it off")

    return failures, warnings


def freeze() -> None:
    """Print the current gaps as BASELINE entries."""
    sizes, undocumented_modules = measure()
    rows = [
        (key, span)
        for key, (span, documented) in sizes.items()
        if span >= LINE_THRESHOLD and not documented
    ]
    rows.sort(key=lambda row: (-row[1], row[0]))
    print(f"# {len(rows)} functions of {LINE_THRESHOLD}+ lines with no docstring")
    for key, span in rows:
        print(f'    "{key}": {span},')
    print(f"\n# {len(undocumented_modules)} modules with no module docstring")
    for module in sorted(undocumented_modules):
        print(f'    "{module}",')


def main(argv: list[str]) -> int:
    sizes, undocumented_modules = measure()

    if "--freeze" in argv:
        freeze()
        return 0

    failures, warnings = check(
        sizes, undocumented_modules, BASELINE, MODULE_BASELINE
    )

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if failures:
        print("docstring check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            f"\n{len(failures)} function(s) or module(s) over the "
            f"{LINE_THRESHOLD}-line threshold without a docstring.  Write "
            "three sentences: what it does, what it guarantees, when not to "
            "call it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"docstring check passed: {len(sizes)} functions scanned, "
        f"{len(BASELINE)} grandfathered, "
        f"{len(MODULE_BASELINE)} module(s) grandfathered."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
