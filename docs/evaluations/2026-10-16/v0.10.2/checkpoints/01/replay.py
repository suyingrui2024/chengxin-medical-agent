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


def main():
    archive = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=archive.parents[3])
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--negative-control", action="store_true")
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
    runs = report["runs"]
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
            shutil.copytree(prepared, workspace)
            if not args.negative_control:
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
        expected = False if args.negative_control else row["source_correct"]
        outputs.append(
            {
                "run": name,
                "expected_source_correct": expected,
                "matches_original": checked.get("passed") is expected,
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
    raise SystemExit(0 if all(row["matches_original"] for row in outputs) else 1)


if __name__ == "__main__":
    main()
