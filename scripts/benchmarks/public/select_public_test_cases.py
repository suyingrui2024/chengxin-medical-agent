"""Preregister a deterministic public TEST sample before environment adaptation."""

import argparse
import hashlib
import json
from pathlib import Path
import random
from datetime import datetime, timezone


def selection(rows, dataset_sha256, seed, count):
    if not isinstance(rows, list) or len(rows) != 300:
        raise ValueError("Expected the complete 300-row SWE-bench Lite test split")
    identities = [row["instance_id"] for row in rows]
    if len(set(identities)) != len(rows) or not 1 <= count <= len(rows):
        raise ValueError("Duplicate IDs or invalid sample size")
    ordered = sorted(rows, key=lambda row: row["instance_id"])
    random.Random(seed).shuffle(ordered)
    chosen = ordered[:count]
    return {
        "kind": "public-test-selection/v1",
        "dataset": "princeton-nlp/SWE-bench_Lite",
        "split": "test",
        "population": len(rows),
        "dataset_parquet_sha256": dataset_sha256,
        "seed": seed,
        "algorithm": "Python random.Random(seed).shuffle, after sorting by instance_id",
        "count": count,
        "selected_ids": [row["instance_id"] for row in chosen],
        "selected_repositories": [row["repo"] for row in chosen],
        "selection_filters": [],
        "replacement_policy": "No substitutions. Record environment failures and unsupported cases.",
        "scope": "Public historical TEST sample; not an uncontaminated holdout or official score.",
        "instances_sha256": {
            row["instance_id"]: hashlib.sha256(
                json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            for row in chosen
        },
    }, chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026101601)
    parser.add_argument("--count", type=int, default=6)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a fresh selection directory")
    parquet_digest = hashlib.sha256(args.parquet.read_bytes()).hexdigest()
    if (
        parquet_digest
        != "7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4"
    ):
        raise SystemExit("Unexpected pinned public TEST parquet")
    manifest, chosen = selection(
        json.loads(args.rows.read_text()), parquet_digest, args.seed, args.count
    )
    manifest["rows_json_sha256"] = hashlib.sha256(args.rows.read_bytes()).hexdigest()
    manifest["created_at"] = datetime.now(timezone.utc).isoformat()
    args.output.mkdir(parents=True)
    (args.output / "selection.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    for row in chosen:
        folder = args.output / row["instance_id"]
        folder.mkdir()
        (folder / "instance.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"selected": manifest["selected_ids"], "external_model_calls": 0},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
