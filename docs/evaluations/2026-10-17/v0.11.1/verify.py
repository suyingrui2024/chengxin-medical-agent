"""Verify frozen bytes and rerun selected directory/preview backend tests, without model or download."""
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
    for name, sha in values.items():
        if (
            not (project / name).is_file()
            or hashlib.sha256((project / name).read_bytes()).hexdigest() != sha
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
            "tests.test_execution_files",
            "tests.test_execution_artifacts",
            "tests.test_execution.WorkspaceFileTests",
        ]
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, buffer=True).run(suite)
    checks = {
        "all_24_selected_tests_ran": result.testsRun == 24,
        "no_skips": not result.skipped,
        "selected_backend_tests_passed": result.wasSuccessful(),
        "source_unchanged": sources(project) == count,
    }
    report = {
        "kind": "frozen-directory-preview-verification/v1",
        "source_files_checked": count,
        "external_model_calls": 0,
        "network_downloads": 0,
        "checks": checks,
        "passed": all(checks.values()),
        "unittest_log": stream.getvalue(),
        "scope": "Real filesystem and localhost HTTP tests. Does not repeat native Electron flows, model evaluations or clinical validation.",
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"unittest_log", "scope"}},
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
