"""Restore the frozen project and all four prepared sources without network or API."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output.resolve()
    dependencies = args.dependencies.resolve()
    if project.exists() or output.exists():
        raise SystemExit("Fresh project and output required")
    frozen = json.loads((HERE / "environment/frozen-files.json").read_text())
    for name, sha in frozen.items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != sha:
            raise SystemExit("Frozen environment input drift: " + name)
    sources = json.loads((HERE / "source-files.json").read_text())
    for name, sha in sources.items():
        if hashlib.sha256((HERE / "snapshot" / name).read_bytes()).hexdigest() != sha:
            raise SystemExit("Frozen evaluator source drift: " + name)
    shutil.copytree(HERE / "snapshot", project)
    if any(project.rglob(".env*")):
        raise SystemExit("Unexpected configuration artifact")
    (project / "node_modules").symlink_to(dependencies, target_is_directory=True)
    shared = project / ".runtime/public-test-sample"
    transfer = project / ".runtime/public-transfer"
    shared.mkdir(parents=True)
    transfer.mkdir(parents=True)
    for source, name in [
        ("shared-inputs-v3.json", shared / "inputs-v3.json"),
        ("transfer-inputs-v3.json", transfer / "inputs-v3.json"),
        ("python39.json", shared / "python39.json"),
    ]:
        shutil.copy2(HERE / "environment" / source, name)
    for name, destination in [
        ("wheels", shared / "wheels"),
        ("python", shared / "python"),
        ("archives", transfer / "archives"),
    ]:
        shutil.copytree(HERE / "environment" / name, destination)
    script = r"""import sys,hashlib
from pathlib import Path
sys.dont_write_bytecode=True
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'scripts'),str(root/'src')]
import prepare_public_transfer as preparation
def offline_fetch(url,path,maximum,expected=None):
 data=path.read_bytes()
 if len(data)>maximum or (expected and hashlib.sha256(data).hexdigest()!=expected):raise ValueError('Offline frozen input drift')
 return data
preparation.fetch=offline_fetch
sys.argv=['prepare_public_transfer.py','--download']
preparation.main()
"""
    environment = {
        k: os.environ[k] for k in ["PATH", "LANG", "TMPDIR"] if k in os.environ
    }
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(project)],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
        timeout=180,
    )
    result = {
        "kind": "frozen-transfer-reconstruction/v1",
        "returncode": completed.returncode,
        "external_model_calls": 0,
        "network_downloads": 0,
        "frozen_environment_files_checked": len(frozen),
        "frozen_source_files_checked": len(sources),
        "node_dependency_policy": "Reuse existing installed dependencies; no package installation.",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "all_prepared_sources_match": False,
    }
    if completed.returncode == 0:
        expected = json.loads(
            (
                HERE / "snapshot/benchmarks/swe-lite-transfer/environments-v5.json"
            ).read_text()
        )
        current = json.loads(
            (project / "benchmarks/swe-lite-transfer/environments-v5.json").read_text()
        )
        result["prepared_cases"] = {
            name: current["cases"][name]["source_sha256"] == row["source_sha256"]
            for name, row in expected["cases"].items()
        }
        result["all_prepared_sources_match"] = all(result["prepared_cases"].values())
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in {"stdout", "stderr"}},
            ensure_ascii=False,
        )
    )
    raise SystemExit(
        0 if completed.returncode == 0 and result["all_prepared_sources_match"] else 1
    )


if __name__ == "__main__":
    main()
