"""Rebuild archived candidate implementations and replay upstream tests, no models."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(value):
    path = Path(value)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts):
        raise ValueError("Invalid archived relative path")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clarified", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    archive = Path(__file__).resolve().parent
    original = json.loads((archive / "public-report.json").read_text())
    hashes = json.loads((archive / "archive-manifest.json").read_text())
    for name, expected in hashes.items():
        if digest(archive / relative(name)) != expected:
            raise SystemExit("Archive differs: " + name)
    for name, expected in original["source_sha256"].items():
        if digest(root / relative(name)) != expected:
            raise SystemExit(
                "Restore the frozen runtime/evaluator before replay: " + name
            )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    import prepare_public_execution as preparation
    from public_execution_cases import load_cases, seed, verify

    fixtures = root / original["fixtures"]
    metadata, cases = load_cases(fixtures)
    # Supply every pinned download locally; preparation performs normal hash checks.
    destinations = []
    for case in metadata["cases"]:
        destinations.append(
            (
                archive / "upstream" / (case["instance_id"] + ".tar.gz"),
                (root / case["source_cache"]).parent / "base.tar.gz",
            )
        )
    for name in {name for case in metadata["cases"] for name in case["dependencies"]}:
        filename = metadata["dependencies"][name]["filename"]
        destinations.append(
            (
                archive / "wheels" / filename,
                root / ".runtime/public-benchmark-research/dependencies" / filename,
            )
        )
    for source, target in destinations:
        if target.exists():
            if digest(source) != digest(target):
                raise SystemExit(
                    "Existing cache differs; inspect before replacing: " + str(target)
                )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    preparation.prepare(False, fixtures)
    selected = (
        json.loads((archive / "clarification-report.json").read_text())
        if args.clarified
        else original
    )
    expected = json.loads(
        (
            archive
            / (
                "clarified-audit.json"
                if args.clarified
                else "implementation-audit.json"
            )
        ).read_text()
    )
    report = {"model_calls": 0, "runs": []}
    for entry in expected["runs"]:
        name = f"r{entry['repetition']}-{entry['case']}-{entry['arm']}"
        run = output / name
        workspace = run / "workspace"
        seed(workspace, cases[entry["case"]])
        overlay = (
            archive
            / ("clarified-overlays" if args.clarified else "source-overlays")
            / name
        )
        changes = json.loads((overlay / "manifest.json").read_text())
        for filename, expected_hash in changes.items():
            target = workspace / relative(filename)
            if expected_hash is None:
                target.unlink()
            else:
                source = overlay / "files" / relative(filename)
                if digest(source) != expected_hash:
                    raise SystemExit("Overlay hash mismatch: " + filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        result = verify(workspace, run / "state", cases[entry["case"]])
        previous = entry["verification"]
        matched = (
            bool(previous.get("outcomes"))
            and result.get("outcomes") == previous["outcomes"]
            and result.get("pytest_exit_code") == previous.get("pytest_exit_code")
            and result.get("passed", False) == entry["source_correctness_passed"]
        )
        report["runs"].append(
            {"run": name, "reproduced": matched, "verification": result}
        )
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"run": name, "reproduced": matched}), flush=True)
    report["all_outcomes_reproduced"] = len(report["runs"]) == len(
        selected["runs"]
    ) == len(expected["runs"]) and all(r["reproduced"] for r in report["runs"])
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["all_outcomes_reproduced"]:
        raise SystemExit("Replay differs; inspect preserved results")


if __name__ == "__main__":
    main()
