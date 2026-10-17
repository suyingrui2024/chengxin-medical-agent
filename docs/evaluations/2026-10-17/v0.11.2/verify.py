"""Verify frozen bytes and patient-history regressions without a model or download."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def sources(project):
    values = json.loads((HERE / "source-files.json").read_text())
    for name, expected in values.items():
        path = project / name
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Frozen source drift: " + name)
    return len(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project, output = args.project.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit("Fresh output required")
    count = sources(project)
    sys.path[:0] = [str(project / "src"), str(project)]
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        [
            "tests.test_uncertain_reports",
            "tests.test_consultation",
            "tests.test_presentation",
            "tests.test_agent",
        ]
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, buffer=True).run(suite)
    report = {
        "source_files_checked": count,
        "tests_run": result.testsRun,
        "external_model_calls": 0,
        "network_downloads": 0,
        "checks": {
            "57_selected_tests_ran": result.testsRun == 57,
            "no_skips": not result.skipped,
            "tests_passed": result.wasSuccessful(),
            "source_unchanged": sources(project) == count,
        },
        "scope": "Frozen patient-history/controller/presentation checks; does not rerun Electron, real-model cases or clinical validation.",
        "unittest_log": stream.getvalue(),
    }
    report["passed"] = all(report["checks"].values())
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"unittest_log", "scope"}
            }
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
