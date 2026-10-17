"""Reconstruct archived candidates and compare every frozen oracle outcome; no API."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
INPUT_ARCHIVE = HERE.parent / "v0.10.2"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_verified(source, target):
    if target.exists():
        if digest(source) != digest(target):
            raise ValueError("Existing input differs: " + str(target))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def prepare(root, identities):
    from prepare_public_execution import extract_source

    cache = root / ".runtime/public-test-sample"
    for name in ["inputs-v3.json", "python39.json"]:
        copy_verified(INPUT_ARCHIVE / "inputs" / name, cache / name)
    for name in ["wheels", "python"]:
        for path in (INPUT_ARCHIVE / "inputs" / name).rglob("*"):
            if path.is_file():
                copy_verified(path, cache / name / path.relative_to(INPUT_ARCHIVE / "inputs" / name))
    inputs = json.loads((cache / "inputs-v3.json").read_text())
    for identity in identities:
        source = INPUT_ARCHIVE / "inputs/archives" / (identity + ".tar.gz")
        copy_verified(source, cache / "archives" / source.name)
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != inputs["cases"][identity]["sha256"]:
            raise ValueError("Source archive drift")
        directory = root / inputs["cases"][identity]["source"]
        if not directory.exists():
            directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=directory.parent) as raw:
                staged = Path(raw) / "source"
                staged.mkdir()
                extract_source(data, staged)
                staged.replace(directory)
    command = [sys.executable, str(root / "scripts/materialize_public_test_sample.py")]
    for identity in identities:
        command.extend(["--case", identity])
    subprocess.run(command, cwd=root, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=HERE.parents[3])
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--negative-control", action="store_true")
    parser.add_argument("--run")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh replay output required")
    root = args.project_root.resolve()
    report = json.loads((HERE / "round1.json").read_text())
    for name, expected in report["source_sha256"].items():
        if not (root / name).is_file() or digest(root / name) != expected:
            raise SystemExit("Restore round1/snapshot into a separate project; source drift: " + name)
    sys.path[:0] = [str(root / "scripts"), str(root / "src"), str(INPUT_ARCHIVE)]
    from public_test_sample import inventory, run_code, code_for, parse, classify
    from candidate_tree import rebuild
    from execution_task_contract import CONTRACT
    from public_execution_cases import protected

    preflight = json.loads((INPUT_ARCHIVE / "preflight-05.json").read_text())
    environments = json.loads((HERE / "round1/snapshot/benchmarks/swe-lite-test-sample/environments-v5.json").read_text())["cases"]
    identities = list(dict.fromkeys(row["case"] for row in report["runs"]))
    if args.prepare:
        prepare(root, identities)
    rows = report["runs"]
    if args.run:
        rows = [row for row in rows if f"r{row['repetition']}-{row['case']}-{row['arm']}" == args.run]
        if len(rows) != 1:
            raise SystemExit("Unknown run")
    if args.negative_control:
        rows = rows[:1]
    result = {"model_requests": 0, "negative_control": args.negative_control, "scope": "Frozen required-node replay only; delivered new regression tests have a separate post-hoc audit.", "runs": []}
    for row in rows:
        identity = row["case"]
        name = f"r{row['repetition']}-{identity}-{row['arm']}"
        environment = environments[identity]
        prepared = root / environment["prepared_source"]
        if inventory(prepared) != environment["source_sha256"]:
            raise ValueError("Prepared source differs; use --prepare in a restored project")
        instance = json.loads((root / "benchmarks/swe-lite-test-sample" / identity / "instance.json").read_text())
        with tempfile.TemporaryDirectory(prefix="wait-policy-replay-") as raw:
            temporary = Path(raw)
            if args.negative_control:
                workspace = temporary / "workspace"
                shutil.copytree(prepared, workspace)
                reference = preflight["cases"][identity]["original"]
                expected = False
            else:
                workspace = rebuild(prepared, HERE / "round1" / name / "candidate", row["candidate_tree"], environment["source_sha256"], temporary / "workspace", protected, CONTRACT["forbidden_new_basenames"])
                reference = row["verification"]
                expected = row["source_correct"]
            checked = classify(parse(run_code(workspace, temporary / "state", code_for(instance, "candidate", preflight["cases"][identity]["test_id_map"]))))
        checks = {"oracle_finished": checked.get("runtime_completed") is True and not checked.get("setup_error"), "source_boolean_matches": checked.get("passed") is expected, "pytest_exit_matches": checked.get("pytest_exit_code") == reference.get("pytest_exit_code"), "all_node_outcomes_match": bool(reference.get("outcomes")) and checked.get("outcomes") == reference.get("outcomes")}
        if args.negative_control:
            checks["original_bug_reproduced"] = checked.get("original_bug_reproduced") is True
        result["runs"].append({"run": name, "checks": checks, "replay_matches_original": all(checks.values()), "verification": checked})
        result["passed"] = all(row["replay_matches_original"] for row in result["runs"])
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"run": name, "replay_matches_original": all(checks.values()), "negative_control": args.negative_control}), flush=True)
    raise SystemExit(0 if result.get("passed") else 1)


if __name__ == "__main__":
    main()
