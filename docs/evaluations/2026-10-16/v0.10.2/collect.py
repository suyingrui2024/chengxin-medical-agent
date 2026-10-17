"""Archive complete runs and byte-checked candidate overlays; refuses an incomplete final report."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = Path(__file__).resolve().parent
    root = archive.parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    target = args.checkpoint.resolve() if args.checkpoint else archive
    report = json.loads(
        (root / ".runtime/evaluations/public-test-v0102/report.json").read_text()
    )
    if not args.checkpoint and len(report["runs"]) != len(report["plan"]):
        raise SystemExit(
            "Fixed model plan is still running; only an explicit checkpoint is permitted"
        )
    if not report["frozen_source_unchanged"]:
        raise SystemExit("Recorded source drift")
    for name, expected in report["source_sha256"].items():
        if digest(root / name) != expected:
            raise SystemExit("Current frozen source changed: " + name)
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    from candidate_tree import scan, regular_bytes

    target.mkdir(parents=True, exist_ok=True)
    environments = json.loads(
        (root / "benchmarks/swe-lite-test-sample/environments-v5.json").read_text()
    )
    for row in report["runs"]:
        name = f"r{row['repetition']}-{row['case']}-{row['arm']}"
        source = root / ".runtime/evaluations/public-test-v0102/report" / name
        directory = (
            Path(row["snapshots"][0]["directory"])
            if row["arm"] == "product"
            else source / "workspace"
        )
        tree = scan(directory)
        current = tree["files"]
        if (
            row.get("candidate_sha256") is not None
            and current != row["candidate_sha256"]
        ):
            raise SystemExit("Candidate drift: " + name)
        baseline = environments["cases"][row["case"]]["source_sha256"]
        folder = target / "runs" / name
        folder.mkdir(parents=True, exist_ok=True)
        files = {}
        for relative in sorted(set(current) | set(baseline)):
            if current.get(relative) == baseline.get(relative):
                continue
            files[relative] = current.get(relative)
            if relative in current:
                destination = folder / "candidate" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(regular_bytes(directory / relative))
                if digest(destination) != current[relative]:
                    raise SystemExit("Overlay changed during copy")
        (folder / "overlay.json").write_text(
            json.dumps(
                {"files": files, "candidate_sha256": current, "candidate_tree": tree},
                indent=2,
            )
            + "\n"
        )
        shutil.copyfile(source / "requests.json", folder / "requests.json")
    (target / "public-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    shutil.copyfile(
        archive / "preflight-05.json", target / "preflight-05.json"
    ) if target != archive else None
    if args.checkpoint:
        shutil.copyfile(archive / "replay.py", target / "replay.py")
        shutil.copyfile(archive / "candidate_tree.py", target / "candidate_tree.py")
    else:
        snapshot = root / ".runtime/evaluations/public-test-v0102/report/snapshot"
        shutil.copytree(snapshot, archive / "snapshot-final", dirs_exist_ok=True)
        for name in ["desktop", "tests"]:
            shutil.copytree(
                root / name,
                archive / "snapshot-final" / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                dirs_exist_ok=True,
            )
        for name in ["README.md", "pyproject.toml", "THIRD_PARTY_NOTICES.md"]:
            shutil.copyfile(root / name, archive / "snapshot-final" / name)
    print(
        json.dumps(
            {
                "checkpoint": bool(args.checkpoint),
                "archived_runs": len(report["runs"]),
                "planned_runs": len(report["plan"]),
                "model_requests": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
