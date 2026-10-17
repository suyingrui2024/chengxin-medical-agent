"""Restore the frozen source and replay saved decisions using installed dependencies."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh output required")
    dependencies = args.dependencies.resolve()
    if not (dependencies / "@deepseek-ai/dsh-sdk-client/package.json").is_file():
        raise SystemExit("Existing locked Node dependencies required")
    evidence = json.loads((HERE / "verification.json").read_text())
    with tempfile.TemporaryDirectory(prefix="delivery-frozen-replay-") as raw:
        temporary = Path(raw)
        project = temporary / "project"
        shutil.copytree(HERE / "snapshot-final", project)
        checked = evidence["final_runtime_source_sha256"]
        matched = all(
            (project / name).is_file()
            and hashlib.sha256((project / name).read_bytes()).hexdigest() == expected
            for name, expected in checked.items()
        )
        no_env = not any(project.rglob(".env*"))
        if not matched or not no_env:
            raise SystemExit("Restored source failed verification")
        (project / "node_modules").symlink_to(dependencies, target_is_directory=True)
        replay_output = temporary / "replay.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(HERE / "replay.py"),
                "--project-root",
                str(project),
                "--output",
                str(replay_output),
            ],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        replay = json.loads(replay_output.read_text()) if replay_output.is_file() else {}
        result = {
            "returncode": completed.returncode,
            "all_final_runtime_files_match": matched,
            "checked_runtime_files": len(checked),
            "restored_project_has_no_env": no_env,
            "replayed_cases": len(replay.get("runs", [])),
            "all_replayed_cases_passed": replay.get("passed", False),
            "external_model_requests": 0,
            "dependency_policy": "Reuse installed locked dependencies; no downloads.",
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "replay": replay,
        }
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key not in {"stdout", "stderr", "replay"}}))
        raise SystemExit(0 if completed.returncode == 0 and replay.get("passed") else 1)


if __name__ == "__main__":
    main()
