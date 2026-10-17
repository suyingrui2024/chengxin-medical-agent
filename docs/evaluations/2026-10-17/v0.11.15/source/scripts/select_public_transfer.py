"""Preregister unseen-to-project tasks within the four existing repository adapters."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
REPOSITORIES = [
    "matplotlib/matplotlib",
    "pallets/flask",
    "sphinx-doc/sphinx",
    "sympy/sympy",
]
PARQUET_SHA256 = "7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4"


def select(rows, excluded, seed):
    if len(rows) != 300 or len({row["instance_id"] for row in rows}) != 300:
        raise ValueError("Expected complete unique 300-row public TEST population")
    generator = random.Random(seed)
    chosen, sizes = [], {}
    for repo in REPOSITORIES:
        eligible = sorted(
            (
                row
                for row in rows
                if row["repo"] == repo and row["instance_id"] not in excluded
            ),
            key=lambda row: row["instance_id"],
        )
        if not eligible:
            raise ValueError("Empty repository stratum")
        sizes[repo] = len(eligible)
        generator.shuffle(eligible)
        chosen.append(eligible[0])
    return chosen, sizes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2026101605)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh selection directory required")
    data = ROOT / ".runtime/public-benchmark-research"
    if (
        hashlib.sha256((data / "test.parquet").read_bytes()).hexdigest()
        != PARQUET_SHA256
    ):
        raise SystemExit("Pinned parquet drift")
    rows_path = data / "test-rows.json"
    rows = json.loads(rows_path.read_text())
    excluded = set()
    exposure = {}
    for path in sorted((ROOT / "benchmarks").glob("**/instance.json")):
        row = json.loads(path.read_text())
        excluded.add(row["instance_id"])
        exposure[str(path.relative_to(ROOT))] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    chosen, sizes = select(rows, excluded, args.seed)
    manifest = {
        "kind": "public-transfer-selection/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "princeton-nlp/SWE-bench_Lite",
        "split": "test",
        "population": 300,
        "dataset_parquet_sha256": PARQUET_SHA256,
        "rows_json_sha256": hashlib.sha256(rows_path.read_bytes()).hexdigest(),
        "seed": args.seed,
        "strata": REPOSITORIES,
        "eligible_counts_after_exclusions": sizes,
        "algorithm": "One per repository, sorted IDs then one shared Python Random(seed).shuffle; fixed repository order.",
        "eligibility_reason": "Existing four repository environment adapters, fixed before inspecting selected issues or outcomes.",
        "excluded_previous_instance_ids": sorted(excluded),
        "exposure_manifest_sha256": exposure,
        "selected_ids": [r["instance_id"] for r in chosen],
        "selected_repositories": [r["repo"] for r in chosen],
        "replacement_policy": "No substitutions; retain unsupported environments and failed preflights. No paid runs until every selected oracle qualifies.",
        "instances_sha256": {
            r["instance_id"]: hashlib.sha256(
                json.dumps(r, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            for r in chosen
        },
        "scope": "New to this project's recorded fixtures, public historical data with possible model contamination; stratified subset of supported repositories, not full-population random sampling, official score or proof of general parity.",
        "planned_comparison": {
            "arms": ["product", "official-sdk"],
            "repetitions": 2,
            "calls_per_arm": 20,
            "seconds_per_arm": 600,
            "maximum_upstream_requests": 320,
            "order_seed": 2026101606,
        },
    }
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
            {
                "selected_ids": manifest["selected_ids"],
                "eligible_counts": sizes,
                "excluded": len(excluded),
                "external_model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
