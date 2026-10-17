"""Replay each original independent check, preserving the expected second failure."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=archive.parents[3])
    args = parser.parse_args()
    root = args.project_root.resolve()
    outputs = []
    for name in ["first", "second", "third"]:
        folder = archive / "runs" / name
        report = json.loads((folder / "report.json").read_text())
        for path in [
            "integrations/dsh/isolated-entry.mjs",
            "integrations/dsh/check-artifacts.mjs",
            "package-lock.json",
        ]:
            if (
                not (root / path).is_file()
                or digest(root / path) != report["source_sha256"][path]
            ):
                raise SystemExit(
                    "Restore the archived launcher and locked dependencies in a separate project; source drift: "
                    + path
                )
        hashes = json.loads((folder / "candidate-sha256.json").read_text())
        for path, expected in hashes.items():
            if (
                Path(path).is_absolute()
                or ".." in Path(path).parts
                or (folder / "candidate" / path).is_symlink()
                or digest(folder / "candidate" / path) != expected
            ):
                raise SystemExit("Candidate hash/path mismatch")
        with tempfile.TemporaryDirectory(
            prefix="chengxin-clarification-replay-"
        ) as raw:
            scratch = Path(raw)
            shutil.copytree(folder / "candidate", scratch / "workspace")
            (scratch / "state").mkdir()
            result = subprocess.run(
                ["node", str(root / "integrations/dsh/check-artifacts.mjs")],
                input=json.dumps(
                    {
                        "workspace": str(scratch / "workspace"),
                        "state": str(scratch / "state"),
                        "code": report["probe"],
                    }
                ),
                capture_output=True,
                text=True,
                timeout=20,
                cwd=root,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LANG": "C.UTF-8",
                },
            )
            checked = json.loads(result.stdout)
        expected = report["independent_verification"]["passed"]
        outputs.append(
            {
                "run": name,
                "expected_passed": expected,
                "verification": checked,
                "matches_original": checked.get("passed") is expected,
            }
        )
    print(json.dumps({"model_requests": 0, "runs": outputs}, ensure_ascii=False))
    raise SystemExit(0 if all(row["matches_original"] for row in outputs) else 1)


if __name__ == "__main__":
    main()
