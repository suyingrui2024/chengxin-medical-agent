"""Supplemental source-only audit. Does not replace the preregistered strict score."""

import argparse
import difflib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from public_execution_cases import (
    FIXTURES,
    inventory,
    load_cases,
    protected,
    seed,
    sha256,
    verify,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    args = parser.parse_args()
    source = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    data = source.read_bytes()
    (output / "input-report.json").write_bytes(data)
    (output / "audit-driver.py").write_bytes(Path(__file__).read_bytes())
    report = json.loads(data)
    _, cases = load_cases(args.fixtures)
    results = {
        "scope": "Supplemental adapted DEV implementation-only correctness. Restore all frozen tests, configs and dependencies before applying the official hidden test patch. Never changes the original strict runtime/instruction-compliance result. Not an official SWE-bench score.",
        "input_report_sha256": sha256(data),
        "audit_driver_sha256": sha256(Path(__file__).read_bytes()),
        "runs": [],
    }
    for run in report["runs"]:
        name = f"r{run['repetition']}-{run['case']}-{run['arm']}"
        case = cases[run["case"]]
        run_root = (
            ROOT / run["artifact_directory"]
            if run.get("artifact_directory")
            else source.with_suffix("") / name
        )
        run_root = run_root.resolve()
        run_root.relative_to(ROOT / ".runtime/evaluations")
        if run["arm"] == "official-sdk":
            candidate = run_root / "workspace"
        else:
            candidates = [
                p
                for p in (run_root / "workspaces").iterdir()
                if re.fullmatch(r"[a-f0-9-]{36}", p.name)
            ]
            if len(candidates) != 1:
                raise ValueError("Ambiguous product workspace")
            candidate = candidates[0]
        current = inventory(candidate)
        original = case["provenance"]["source_sha256"]
        altered = sorted(
            name
            for name, digest in original.items()
            if protected(name) and current.get(name) != digest
        )
        implementation_root = {
            "pydicom/pydicom": "pydicom",
            "marshmallow-code/marshmallow": "src/marshmallow",
            "pylint-dev/astroid": "astroid",
            "pvlib/pvlib-python": "pvlib",
            "sqlfluff/sqlfluff": "src/sqlfluff",
        }[case["metadata"]["repo"]]
        work = output / name
        workspace = work / "workspace"
        seed(workspace, case)
        changed = []
        patch = []
        for filename in sorted(set(original) | set(current)):
            if (
                not filename.startswith(implementation_root + "/")
                or not filename.endswith(".py")
                or protected(filename)
            ):
                continue
            if original.get(filename) == current.get(filename):
                continue
            target = workspace / filename
            before = target.read_text() if target.exists() else ""
            if filename in current:
                target.parent.mkdir(parents=True, exist_ok=True)
                after = (candidate / filename).read_text()
                target.write_text(after)
            else:
                after = ""
                target.unlink()
            changed.append(filename)
            patch.extend(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    fromfile="a/" + filename,
                    tofile="b/" + filename,
                )
            )
        work.mkdir(parents=True, exist_ok=True)
        (work / "implementation.patch").write_text("".join(patch))
        check = verify(workspace, work / "state", case)
        result = {
            "case": run["case"],
            "repetition": run["repetition"],
            "arm": run["arm"],
            "strict_original_passed": run["passed"],
            "protected_files_changed": altered,
            "implementation_files_changed": changed,
            "source_correctness_passed": check.get("passed", False),
            "verification": check,
        }
        results["runs"].append(result)
        (work / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        (output / "report.json").write_text(json.dumps(results, indent=2) + "\n")
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in [
                        "case",
                        "repetition",
                        "arm",
                        "strict_original_passed",
                        "source_correctness_passed",
                        "protected_files_changed",
                    ]
                }
            ),
            flush=True,
        )
    results["summary"] = {
        arm: {
            "attempts": sum(r["arm"] == arm for r in results["runs"]),
            "source_correctness_passed": sum(
                r["source_correctness_passed"]
                for r in results["runs"]
                if r["arm"] == arm
            ),
        }
        for arm in ["product", "official-sdk"]
    }
    (output / "report.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
