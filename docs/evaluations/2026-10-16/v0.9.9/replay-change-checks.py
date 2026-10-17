"""Verify the archived candidate with the corrected checker, with no model calls."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=archive.parents[3])
    args = parser.parse_args()
    root = args.project_root.resolve()
    report = json.loads((archive / "live-first-report.json").read_text())
    correction = json.loads((archive / "corrected-verification.json").read_text())
    required = {
        name: digest
        for name, digest in report["source_sha256"].items()
        if name.startswith(("src/", "integrations/"))
        or name
        in {"scripts/benchmark_execution.py", "package.json", "package-lock.json"}
    }
    mismatches = [
        name
        for name, digest in required.items()
        if not (root / name).is_file() or sha(root / name) != digest
    ]
    if mismatches:
        raise SystemExit(
            "Restore archived v0.9.9 runtime in a separate project first; source drift: "
            + ", ".join(mismatches)
        )
    files = json.loads((archive / "candidate-sha256.json").read_text())
    for name, digest in files.items():
        path = archive / "candidate" / name
        if path.is_symlink() or not path.is_file() or sha(path) != digest:
            raise SystemExit("Archived candidate mismatch: " + name)
    sys.path.insert(0, str(root / "scripts"))
    sys.path.insert(0, str(root / "src"))
    from benchmark_execution import check_artifacts

    with tempfile.TemporaryDirectory(prefix="chengxin-v099-replay-") as raw:
        scratch = Path(raw)
        shutil.copytree(archive / "candidate", scratch / "workspace")
        result = check_artifacts(
            scratch / "workspace", scratch / "state", correction["probe"]
        )
    print(json.dumps({"model_calls": 0, "verification": result}, ensure_ascii=False))
    raise SystemExit(0 if result.get("passed") else 1)


if __name__ == "__main__":
    main()
