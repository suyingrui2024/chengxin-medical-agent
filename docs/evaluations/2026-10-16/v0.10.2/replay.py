"""Offline candidate replay against the frozen TEST oracle. Never loads .env or a model."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_verified(source, target):
    if target.exists():
        if digest(target) != digest(source):
            raise ValueError("Existing input cache differs: " + str(target))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def compare_verification(checked, expected, reference, negative=False):
    checks = {
        "source_boolean_matches": checked.get("passed") is expected,
        "oracle_completed": checked.get("runtime_completed") is True
        and not checked.get("setup_error"),
        "pytest_exit_matches": isinstance(reference.get("pytest_exit_code"), int)
        and checked.get("pytest_exit_code") == reference.get("pytest_exit_code"),
        "all_node_outcomes_match": bool(reference.get("outcomes"))
        and checked.get("outcomes") == reference.get("outcomes"),
    }
    if negative:
        checks["original_bug_reproduced"] = (
            checked.get("original_bug_reproduced") is True
        )
    return checks


def main():
    archive = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=archive.parents[3])
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--negative-control", action="store_true")
    parser.add_argument("--supplement", type=Path)
    parser.add_argument("--run")
    args = parser.parse_args()
    root = args.project_root.resolve()
    report = json.loads((archive / "public-report.json").read_text())
    for name, expected in report["source_sha256"].items():
        path = root / name
        if not path.is_file() or digest(path) != expected:
            raise SystemExit(
                "Restore snapshot-final into a separate project with locked dependencies; source drift: "
                + name
            )
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    from public_test_sample import inventory, run_code, code_for, parse, classify
    from prepare_public_execution import extract_source
    from public_execution_cases import protected
    from execution_task_contract import CONTRACT

    cache = root / ".runtime/public-test-sample"
    fixtures = root / "benchmarks/swe-lite-test-sample"
    environments = json.loads((fixtures / "environments-v5.json").read_text())
    if args.prepare:
        for name in ["inputs-v3.json", "python39.json"]:
            copy_verified(archive / "inputs" / name, cache / name)
        for name in ["archives", "wheels", "python", "metadata"]:
            for source in (archive / "inputs" / name).rglob("*"):
                if source.is_file():
                    copy_verified(
                        source,
                        cache / name / source.relative_to(archive / "inputs" / name),
                    )
        inputs = json.loads((cache / "inputs-v3.json").read_text())
        for identity, source in inputs["cases"].items():
            data = (cache / "archives" / (identity + ".tar.gz")).read_bytes()
            if hashlib.sha256(data).hexdigest() != source["sha256"]:
                raise ValueError("Source archive changed")
            directory = root / source["source"]
            if not directory.exists():
                directory.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=directory.parent) as raw:
                    staged = Path(raw) / "source"
                    staged.mkdir()
                    extract_source(data, staged)
                    staged.replace(directory)
        identity = "matplotlib__matplotlib-26011"
        source = archive / "inputs/native-build-v2" / identity
        target = cache / "native-build-v2" / identity
        record = json.loads((source / "build-record.json").read_text())
        for name in ["provenance.json", "verification.json"]:
            copy_verified(source / name, target / name)
        copy_verified(
            source / "build-record.json", target / "workspace/build-record.json"
        )
        copy_verified(
            source / Path(record["output"]).name,
            target / "workspace" / record["output"],
        )
        result = subprocess.run(
            [sys.executable, str(root / "scripts/materialize_public_test_sample.py")],
            cwd=root,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONPATH": str(root / "src"),
            },
            check=False,
        )
        if result.returncode:
            raise SystemExit(result.returncode)

    preflight = json.loads((archive / "preflight-05.json").read_text())
    supplement_path = args.supplement or archive / "uniform-supplement.json"
    supplements = {}
    if supplement_path.is_file():
        supplements = {
            (row["case"], row["repetition"], row["arm"]): row
            for row in json.loads(supplement_path.read_text())["runs"]
        }
    runs = report["runs"]
    if args.run:
        runs = [
            row
            for row in runs
            if f"r{row['repetition']}-{row['case']}-{row['arm']}" == args.run
        ]
        if len(runs) != 1:
            raise SystemExit("Requested run not in archived checkpoint")
    if args.negative_control:
        runs = [next(row for row in runs if row["source_correct"])]
    outputs = []
    for row in runs:
        name = f"r{row['repetition']}-{row['case']}-{row['arm']}"
        environment = environments["cases"][row["case"]]
        prepared = root / environment["prepared_source"]
        if inventory(prepared) != environment["source_sha256"]:
            raise ValueError(
                "Prepared fixture drift; use --prepare in the restored project"
            )
        instance = json.loads((fixtures / row["case"] / "instance.json").read_text())
        overlay = json.loads((archive / "runs" / name / "overlay.json").read_text())
        with tempfile.TemporaryDirectory(prefix="chengxin-public-test-replay-") as raw:
            scratch = Path(raw)
            workspace = scratch / "workspace"
            if not args.negative_control and "candidate_tree" in overlay:
                from candidate_tree import rebuild

                rebuild(
                    prepared,
                    archive / "runs" / name / "candidate",
                    overlay["candidate_tree"],
                    environment["source_sha256"],
                    workspace,
                    protected,
                    CONTRACT["forbidden_new_basenames"],
                )
            else:
                shutil.copytree(prepared, workspace)
            if not args.negative_control and "candidate_tree" not in overlay:
                for relative, value in overlay["files"].items():
                    path = Path(relative)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("Invalid overlay path")
                    if (
                        protected(relative)
                        or path.name in CONTRACT["forbidden_new_basenames"]
                    ):
                        continue
                    target = workspace / relative
                    if value is None:
                        target.unlink(missing_ok=True)
                    else:
                        candidate = archive / "runs" / name / "candidate" / relative
                        if candidate.is_symlink() or digest(candidate) != value:
                            raise ValueError("Candidate checksum changed")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(candidate, target)
            checked = classify(
                parse(
                    run_code(
                        workspace,
                        scratch / "state",
                        code_for(
                            instance,
                            "candidate",
                            preflight["cases"][row["case"]]["test_id_map"],
                        ),
                    )
                )
            )
        expectation_source = "original_verified_result"
        expected = False if args.negative_control else row["source_correct"]
        if not args.negative_control and not row["checks"].get("source_readable"):
            supplement = supplements.get(
                (row["case"], row["repetition"], row["arm"]), {}
            )
            expected = supplement.get("source_correct")
            if not isinstance(expected, bool):
                raise SystemExit(
                    "Original implementation was not assessed; supply its uniform supplemental assessment"
                )
            expectation_source = "supplement_for_originally_unavailable_assessment"
        reference = (
            preflight["cases"][row["case"]]["original"]
            if args.negative_control
            else row.get("independent_verification", {})
        )
        if expectation_source == "supplement_for_originally_unavailable_assessment":
            reference = supplement["verification"]
        integrity = compare_verification(
            checked, expected, reference, args.negative_control
        )
        outputs.append(
            {
                "run": name,
                "expected_source_correct": expected,
                "expectation_source": expectation_source,
                "integrity_checks": integrity,
                "matches_expected": all(integrity.values()),
                "verification": checked,
            }
        )
    print(
        json.dumps(
            {
                "model_requests": 0,
                "negative_control": args.negative_control,
                "runs": outputs,
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if all(row["matches_expected"] for row in outputs) else 1)


if __name__ == "__main__":
    main()
