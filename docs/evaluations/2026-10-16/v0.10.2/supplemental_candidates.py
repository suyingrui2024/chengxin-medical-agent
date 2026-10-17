"""Uniform, separately reported candidate audit; never changes the original model report."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

from candidate_tree import scan, rebuild


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    archive = Path(__file__).resolve().parent
    root = archive.parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original_snapshot = args.output.with_suffix(".original-report.json")
    if args.output.exists() or original_snapshot.exists():
        raise SystemExit("Preserve the previous audit; use a fresh output")
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    from public_test_sample import run_code, code_for, parse, classify
    from public_execution_cases import protected
    from execution_task_contract import CONTRACT, regression_file

    source_bytes = args.report.read_bytes()
    source = json.loads(source_bytes)
    preflight = json.loads((archive / "preflight-05.json").read_text())
    environments = json.loads(
        (root / "benchmarks/swe-lite-test-sample/environments-v5.json").read_text()
    )
    frozen = {
        name: digest(archive / name)
        for name in [
            "candidate_tree.py",
            "supplemental_candidates.py",
            "supplemental-plan.json",
        ]
    }
    report = {
        "kind": "uniform-candidate-supplement/v1",
        "original_report_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "original_report_snapshot": str(original_snapshot),
        "original_completed_runs": len(source["runs"]),
        "planned_runs": len(source["plan"]),
        "supplemental_source_sha256": frozen,
        "model_requests": 0,
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    original_snapshot.write_bytes(source_bytes)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    save()
    for row in source["runs"]:
        if any(digest(archive / name) != expected for name, expected in frozen.items()):
            raise ValueError("Supplemental implementation changed")
        identity = row["case"]
        result = {
            key: row[key] for key in ["case", "repetition", "arm", "runtime_completed"]
        }
        result.update(
            {
                "original_source_correct": row["source_correct"],
                "original_source_readable": row["checks"].get("source_readable"),
                "source_assessment": "unavailable",
                "source_correct": None,
            }
        )
        try:
            folder = (
                args.report.with_suffix("")
                / f"r{row['repetition']}-{identity}-{row['arm']}"
            )
            directory = (
                Path(row["snapshots"][0]["directory"])
                if row["arm"] == "product"
                else folder / "workspace"
            )
            tree = scan(directory)
            baseline = environments["cases"][identity]["source_sha256"]
            if (
                row.get("candidate_sha256") is not None
                and tree["files"] != row["candidate_sha256"]
            ):
                raise ValueError("Known regular-file candidate snapshot changed")
            result["candidate_tree"] = tree
            added = (
                set(tree["files"]) | set(tree["links"]) | set(tree["special"])
            ) - set(baseline)
            result["file_checks"] = {
                "source_readable": True,
                "upstream_tests_and_config_preserved": all(
                    tree["files"].get(name) == value
                    for name, value in baseline.items()
                    if protected(name)
                ),
                "no_new_protected_files": not any(protected(name) for name in added),
                "no_new_test_control_files": not any(
                    Path(name).name in CONTRACT["forbidden_new_basenames"]
                    for name in added
                ),
                "new_tests_follow_contract": not any(
                    (
                        Path(name).name.startswith("test_")
                        or Path(name).name.endswith("_test.py")
                        or Path(name).name == "test.py"
                    )
                    and name.endswith(".py")
                    and not regression_file(name)
                    for name in added
                ),
                "python_source_changed": any(
                    name.endswith(".py")
                    and not protected(name)
                    and baseline.get(name) != value
                    for name, value in tree["files"].items()
                ),
            }
            instance = json.loads(
                (
                    root
                    / "benchmarks/swe-lite-test-sample"
                    / identity
                    / "instance.json"
                ).read_text()
            )
            with tempfile.TemporaryDirectory(
                prefix="chengxin-supplemental-candidate-"
            ) as raw:
                temporary = Path(raw)
                workspace = rebuild(
                    root / environments["cases"][identity]["prepared_source"],
                    directory,
                    tree,
                    baseline,
                    temporary / "workspace",
                    protected,
                    CONTRACT["forbidden_new_basenames"],
                )
                checked = classify(
                    parse(
                        run_code(
                            workspace,
                            temporary / "state",
                            code_for(
                                instance,
                                "candidate",
                                preflight["cases"][identity]["test_id_map"],
                            ),
                        )
                    )
                )
            result["verification"] = checked
            if not checked.get("setup_error") and checked.get("runtime_completed"):
                result["source_correct"] = checked["passed"]
                result["source_assessment"] = (
                    "passed" if checked["passed"] else "failed"
                )
            result["source_and_file_checks_passed"] = (
                result["source_correct"] is True
                and all(result["file_checks"].values())
                and row["checks"]["request_bound_respected"]
                and row["checks"]["all_provider_responses_ok"]
            )
        except (OSError, ValueError, KeyError, TimeoutError) as error:
            result["diagnostic"] = {
                "type": type(error).__name__,
                "message": str(error)[:1500],
            }
        report["runs"].append(result)
        save()
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in [
                        "case",
                        "arm",
                        "repetition",
                        "original_source_readable",
                        "source_assessment",
                    ]
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
