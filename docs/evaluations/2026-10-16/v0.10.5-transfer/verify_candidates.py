"""Recheck archived implementations and new tests in a restored, credential-free project."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=HERE / "complete.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--negative", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("Fresh replay output required")
    source = json.loads((HERE / "source-files.json").read_text())
    for name, sha in source.items():
        # Re-materialization intentionally rewrites its environment manifest;
        # validate source inventories separately after reconstruction.
        if name.endswith("/environments-v5.json"):
            continue
        if (
            not (root / name).is_file()
            or hashlib.sha256((root / name).read_bytes()).hexdigest() != sha
        ):
            raise SystemExit("Restored source drift: " + name)
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    from benchmark_public_transfer import regression_code, file_checks
    from benchmark_wait_policy import rebuild, regular_bytes
    from public_test_sample import inventory, run_code, code_for, parse, classify
    from public_execution_cases import protected
    from execution_task_contract import CONTRACT, regression_file

    report = json.loads(args.report.read_text())
    preflight = json.loads((HERE / "preflight.json").read_text())
    environments = json.loads(
        (
            HERE / "snapshot/benchmarks/swe-lite-transfer/environments-v5.json"
        ).read_text()
    )
    result = {
        "kind": "frozen-transfer-candidate-replay/v1",
        "negative_control": args.negative,
        "upstream_model_requests": 0,
        "scope": "Reconstruct frozen candidates and execute independent upstream and generated-test oracles. Does not replay model reasoning, tools or root completion.",
        "runs": [],
    }
    selected = report["selection"]["selected_ids"]
    rows = [{"case": name} for name in selected] if args.negative else report["runs"]
    for row in rows:
        name = row["case"]
        environment = environments["cases"][name]
        baseline = environment["source_sha256"]
        prepared = root / environment["prepared_source"]
        if inventory(prepared) != baseline:
            raise SystemExit("Restored prepared source drift: " + name)
        instance = json.loads(
            (
                HERE / "snapshot/benchmarks/swe-lite-transfer" / name / "instance.json"
            ).read_text()
        )
        with tempfile.TemporaryDirectory(prefix="transfer-candidate-replay-") as raw:
            temporary = Path(raw)
            if args.negative:
                workspace = temporary / "workspace"
                shutil.copytree(prepared, workspace)
                contract_matches = True
                added = []
            else:
                run = HERE / "runs" / f"r{row['repetition']}-{name}-{row['arm']}"
                overlay = json.loads((run / "overlay.json").read_text())
                tree = row["candidate_tree"]
                expected_changes = {
                    path: sha
                    for path, sha in tree["files"].items()
                    if baseline.get(path) != sha
                }
                if (
                    overlay["files"] != expected_changes
                    or overlay["links"] != tree["links"]
                    or overlay["special"] != tree["special"]
                ):
                    raise SystemExit("Candidate overlay metadata drift")
                if overlay["deleted"] != sorted(
                    set(baseline) - set(tree["files"]) - set(tree["links"])
                ):
                    raise SystemExit("Candidate deletion drift")
                for path, sha in expected_changes.items():
                    if (
                        hashlib.sha256(
                            regular_bytes(run / "candidate" / path)
                        ).hexdigest()
                        != sha
                    ):
                        raise SystemExit("Candidate byte drift: " + path)
                contract_matches = file_checks(tree, baseline) == row["file_checks"]
                workspace = rebuild(
                    prepared,
                    run / "candidate",
                    tree,
                    baseline,
                    temporary / "workspace",
                    protected,
                    CONTRACT["forbidden_new_basenames"],
                )
                added = sorted(
                    path
                    for path in tree["files"]
                    if path not in baseline and regression_file(path)
                )
            oracle = classify(
                parse(
                    run_code(
                        workspace,
                        temporary / "state",
                        code_for(
                            instance,
                            "candidate",
                            preflight["cases"][name]["test_id_map"],
                        ),
                    )
                )
            )
            if added:
                extra = parse(
                    run_code(
                        workspace,
                        temporary / "regression-state",
                        regression_code(added),
                    )
                )
                nodes = extra.get("collected", [])
                outcomes = extra.get("outcomes", {})
                extra["passed"] = (
                    extra.get("runtime_completed") is True
                    and extra.get("pytest_exit_code") == 0
                    and bool(nodes)
                    and all(outcomes.get(node) == "passed" for node in nodes)
                    and all(
                        any(node.startswith(path + "::") for node in nodes)
                        for path in added
                    )
                )
            else:
                extra = {"passed": True, "collected": []}
            if args.negative:
                checks = {
                    "original_defect_rejected": oracle.get("passed") is False
                    and oracle.get("original_bug_reproduced") is True,
                    "oracle_transport_completed": oracle.get("runtime_completed")
                    is True,
                }
            else:
                expected = row["verification"]
                expected_extra = row["added_regressions"]
                checks = {
                    "file_contract_recomputed": contract_matches,
                    "same_implementation_verdict": oracle.get("passed")
                    is row["implementation_passed"],
                    "same_pytest_exit": oracle.get("pytest_exit_code")
                    == expected.get("pytest_exit_code"),
                    "same_upstream_node_outcomes": oracle.get("outcomes")
                    == expected.get("outcomes"),
                    "same_added_test_verdict": extra["passed"]
                    is expected_extra["passed"],
                    "same_added_test_collection": sorted(extra.get("collected", []))
                    == sorted(expected_extra.get("collected", [])),
                    "same_added_test_outcomes": extra.get("outcomes")
                    == expected_extra.get("outcomes"),
                }
            entry = {k: row[k] for k in ["case", "repetition", "arm"] if k in row}
            entry.update(
                checks=checks,
                passed=all(checks.values()),
                oracle=oracle,
                added_regressions=extra,
            )
            result["runs"].append(entry)
            result["passed"] = len(result["runs"]) == len(rows) and all(
                r["passed"] for r in result["runs"]
            )
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in entry.items()
                        if k not in {"oracle", "added_regressions"}
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    raise SystemExit(0 if result.get("passed") else 1)


if __name__ == "__main__":
    main()
