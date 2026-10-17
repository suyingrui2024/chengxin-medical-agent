"""Replay archived implementation patches in isolation, with zero model calls."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    archive = Path(__file__).resolve().parent
    original = json.loads((archive / "public-report.json").read_text())
    for name, expected in original["source_sha256"].items():
        if hashlib.sha256((project / name).read_bytes()).hexdigest() != expected:
            raise SystemExit(
                "Restore the frozen source snapshot before replay: " + name
            )
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(project / "scripts"), str(project / "src")]
    from public_execution_cases import load_cases, seed, verify

    _, cases = load_cases()
    expected_results = json.loads((archive / "implementation-audit.json").read_text())
    report = {"model_calls": 0, "runs": []}
    for expected in expected_results["runs"]:
        identity = f"r{expected['repetition']}-{expected['case']}-{expected['arm']}"
        case = deepcopy(cases[expected["case"]])
        case["metadata"]["patch"] = (
            archive / "implementation-audit" / identity / "implementation.patch"
        ).read_text()
        root = args.output / identity
        seed(root / "workspace", case)
        result = verify(root / "workspace", root / "state", case, reference=True)
        previous = expected["verification"]
        reproduced = (
            bool(previous.get("outcomes"))
            and result.get("outcomes") == previous["outcomes"]
            and result.get("pytest_exit_code") == previous.get("pytest_exit_code")
            and result.get("passed", False) == expected["source_correctness_passed"]
        )
        record = {
            "run": identity,
            "reproduced": reproduced,
            "passed": result.get("passed", False),
            "verification": result,
        }
        report["runs"].append(record)
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {"run": identity, "reproduced": reproduced, "passed": record["passed"]}
            ),
            flush=True,
        )
    report["all_outcomes_reproduced"] = len(report["runs"]) == 16 and all(
        run["reproduced"] for run in report["runs"]
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["all_outcomes_reproduced"]:
        raise SystemExit("Archived patch replay differed; inspect the preserved report")


if __name__ == "__main__":
    main()
