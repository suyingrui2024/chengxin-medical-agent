"""Report preregistered dimensions separately; no post-hoc replacement of scores."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Fresh summary required")
    report = json.loads(args.input.read_text())
    if (
        not report.get("plan_complete")
        or len(report["runs"]) != len(report["plan"])
        or report.get("active")
    ):
        raise SystemExit("Complete fixed plan required")
    actual = sum(row["gateway"]["attempted_calls"] for row in report["runs"])
    if actual != report["attempted_calls"]:
        raise SystemExit("Request accounting mismatch")
    runs = report["runs"]
    by_case = {}
    totals = {}
    for arm in report["selection"]["planned_comparison"]["arms"]:
        selected = [r for r in runs if r["arm"] == arm]
        generated = [
            r for r in selected if r.get("added_regressions", {}).get("collected")
        ]
        totals[arm] = {
            "runs": len(selected),
            "implementation_passed": sum(
                r["implementation_passed"] is True for r in selected
            ),
            "implementation_unverified": sum(
                r["implementation_passed"] is None for r in selected
            ),
            "file_contract_passed": sum(
                bool(r.get("file_checks")) and all(r["file_checks"].values())
                for r in selected
            ),
            "normal_root_completion": sum(r["runtime_completed"] for r in selected),
            "source_and_contract_passed": sum(
                r["source_and_contract_passed"] for r in selected
            ),
            "end_to_end_completed": sum(r["end_to_end_completed"] for r in selected),
            "runs_with_collected_generated_tests": len(generated),
            "runs_with_generated_tests_all_passed": sum(
                r["added_regressions"]["passed"] for r in generated
            ),
            "actual_upstream_requests": sum(
                r["gateway"]["attempted_calls"] for r in selected
            ),
            "execution_duration_ms_sum": sum(
                r["execution_duration_ms"] for r in selected
            ),
        }
    for case in report["selection"]["selected_ids"]:
        by_case[case] = {}
        for arm in totals:
            selected = sorted(
                (r for r in runs if r["case"] == case and r["arm"] == arm),
                key=lambda r: r["repetition"],
            )
            by_case[case][arm] = [
                {
                    k: r[k]
                    for k in [
                        "repetition",
                        "implementation_passed",
                        "runtime_completed",
                        "source_and_contract_passed",
                        "end_to_end_completed",
                    ]
                }
                | {
                    "calls": r["gateway"]["attempted_calls"],
                    "file_contract_passed": bool(r.get("file_checks"))
                    and all(r["file_checks"].values()),
                    "new_regression_count": len(
                        r.get("added_regressions", {}).get("collected", [])
                    ),
                    "new_regressions_passed": r.get("added_regressions", {}).get(
                        "passed"
                    ),
                    "root_error": r["snapshots"][0].get(
                        "error",
                        r["snapshots"][0].get(
                            "error_type", r["snapshots"][0].get("reason")
                        ),
                    )
                    if r["snapshots"]
                    else r.get("execution_error"),
                }
                for r in selected
            ]
    paired = {}
    for metric in ["implementation_passed", "end_to_end_completed"]:
        count = {
            "product_only": 0,
            "sdk_only": 0,
            "both": 0,
            "neither": 0,
            "unassessed": 0,
        }
        for case in report["selection"]["selected_ids"]:
            for repetition in range(
                1, report["selection"]["planned_comparison"]["repetitions"] + 1
            ):
                values = {
                    r["arm"]: r[metric]
                    for r in runs
                    if r["case"] == case and r["repetition"] == repetition
                }
                product, sdk = values["product"], values["official-sdk"]
                key = (
                    "unassessed"
                    if product is None or sdk is None
                    else "both"
                    if product and sdk
                    else "product_only"
                    if product
                    else "sdk_only"
                    if sdk
                    else "neither"
                )
                count[key] += 1
        paired[metric] = count
    result = {
        "kind": "public-transfer-result-summary/v1",
        "scope": report["scope"],
        "actual_requests": actual,
        "maximum_requests": report["maximum_calls"],
        "all_frozen_source_checks_passed": report.get("frozen_source_unchanged"),
        "totals": totals,
        "by_case": by_case,
        "paired": paired,
        "statistical_scope": "Four task clusters, two repeats each; repeats are not assumed independent. No general parity, clinical, monetary cost or full-population claim.",
        "primary_scores_modified": False,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"totals": totals, "paired": paired, "actual_requests": actual},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
