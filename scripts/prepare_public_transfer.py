"""Prepare the fixed transfer selection, reusing only verified immutable dependencies."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import materialize_public_test_sample as materializer
from prepare_public_execution import extract_source
from prepare_public_test_sample import fetch

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "benchmarks/swe-lite-transfer"
CACHE = ROOT / ".runtime/public-transfer"
SHARED = ROOT / ".runtime/public-test-sample"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    selection = json.loads((FIXTURES / "selection.json").read_text())
    if not args.download:
        print(
            json.dumps(
                {
                    "selected_ids": selection["selected_ids"],
                    "new_sources": 4,
                    "external_model_calls": 0,
                    "dependency_policy": "reuse only hash-verified prior wheels and pinned historical interpreter",
                }
            )
        )
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    shared_plan = SHARED / "inputs-v3.json"
    prior = json.loads(shared_plan.read_text())
    plan_path = CACHE / "inputs-v3.json"
    plan = (
        json.loads(plan_path.read_text())
        if plan_path.exists()
        else {
            "kind": "public-transfer-environment-inputs/v1",
            "selection_sha256": digest(FIXTURES / "selection.json"),
            "shared_dependency_plan_sha256": digest(shared_plan),
            "wheel_versions": prior["wheel_versions"],
            "wheels": prior["wheels"],
            "cases": {},
            "shared_cache_policy": "Read-only reuse of prior verified wheel files and pinned interpreter archive; no upstream Python source replaced by dependency wheel.",
        }
    )
    if plan["selection_sha256"] != digest(FIXTURES / "selection.json") or plan[
        "shared_dependency_plan_sha256"
    ] != digest(shared_plan):
        raise ValueError("Frozen selection or shared plan changed")

    def save():
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")

    save()
    for record in plan["wheels"].values():
        if (
            Path(record["filename"]).name != record["filename"]
            or digest(SHARED / "wheels" / record["filename"]) != record["sha256"]
        ):
            raise ValueError("Shared wheel integrity failed")
    interpreter = json.loads((SHARED / "python39.json").read_text())
    if digest(SHARED / "python" / interpreter["name"]) != interpreter["sha256"]:
        raise ValueError("Pinned interpreter changed")
    for name in ["wheels", "python"]:
        destination = CACHE / name
        if destination.is_symlink():
            if destination.resolve() != (SHARED / name).resolve():
                raise ValueError("Unexpected shared cache target")
        elif destination.exists():
            raise ValueError("Unexpected cache directory")
        else:
            destination.symlink_to(SHARED / name, target_is_directory=True)
    shutil.copy2(SHARED / "python39.json", CACHE / "python39.json")
    for identity in selection["selected_ids"]:
        instance = json.loads((FIXTURES / identity / "instance.json").read_text())
        expected = selection["instances_sha256"][identity]
        if (
            hashlib.sha256(
                json.dumps(instance, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            != expected
        ):
            raise ValueError("Selected instance drift")
        url = f"https://codeload.github.com/{instance['repo']}/tar.gz/{instance['base_commit']}"
        archive = CACHE / "archives" / (identity + ".tar.gz")
        data = fetch(
            url, archive, 80000000, plan["cases"].get(identity, {}).get("sha256")
        )
        source = CACHE / "sources" / identity
        if not source.exists():
            import tempfile

            source.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                dir=source.parent, prefix="extract-"
            ) as raw:
                staged = Path(raw) / "source"
                staged.mkdir()
                extract_source(data, staged)
                staged.replace(source)
        plan["cases"][identity] = {
            "url": url,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "source": str(source.relative_to(ROOT)),
        }
        save()
        print(json.dumps({"source": identity, "archive_bytes": len(data)}), flush=True)
    materializer.FIXTURES = FIXTURES
    materializer.CACHE = CACHE
    sys.argv = [
        "materialize_public_test_sample.py",
        "--input-plan",
        str(plan_path),
        "--environment-revision",
        "v5",
    ]
    materializer.main()


if __name__ == "__main__":
    main()
