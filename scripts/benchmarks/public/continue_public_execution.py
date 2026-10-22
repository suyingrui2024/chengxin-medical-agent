"""Finish planned public DEV pairs without replacing the original report."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import json
from pathlib import Path
import random

from scripts.benchmarks.benchmark_execution import ROOT, summarize
from scripts.benchmarks.public.benchmark_public_execution import (
    CALLS_PER_ARM,
    evaluate,
    source_inventory,
    tool_difference,
)
from medagent.env_config import for_backend, read_env
from scripts.benchmarks.public.public_execution_cases import load_cases, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-total-calls", type=int, default=384)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Pass --live explicitly to contact the configured real model")
    source, output = args.input.resolve(), args.output.resolve()
    artifact_root = output.with_suffix("")
    if (
        source.parent != output.parent
        or output.exists()
        or artifact_root.exists()
        or not 48 <= args.max_total_calls <= 512
    ):
        raise SystemExit(
            "Use fresh output in the same cumulative-budget directory, with cap 48–512"
        )
    original_bytes = source.read_bytes()
    report = json.loads(original_bytes)
    if (
        report.get("kind") != "paired-public-dev-execution/v1"
        or report.get("stopped_reason") != "insufficient_budget_for_full_pair"
    ):
        raise SystemExit(
            "Input must be a terminal report stopped at a complete pair boundary"
        )
    current = source_inventory()
    own = str(Path(__file__).resolve().relative_to(ROOT))
    if any(
        current.get(name) != digest for name, digest in report["source_sha256"].items()
    ) or set(current) - set(report["source_sha256"]) - {own}:
        raise SystemExit(
            "Runtime, fixtures or existing evaluators changed; start a separate experiment"
        )
    _, cases = load_cases()
    for record in report["runs"]:
        name = f"r{record['repetition']}-{record['case']}-{record['arm']}"
        record.setdefault(
            "artifact_directory", str((source.with_suffix("") / name).relative_to(ROOT))
        )
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    if settings["MEDAGENT_LLM_MODEL"] != report["model"]:
        raise SystemExit("Configured model changed")
    artifact_root.mkdir()
    (artifact_root / "input-report.json").write_bytes(original_bytes)
    for name, digest in current.items():
        target = artifact_root / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / name).read_bytes()
        if sha256(data) != digest:
            raise RuntimeError("Source changed during snapshot")
        target.write_bytes(data)
    report.update(
        continued_from_sha256=sha256(original_bytes),
        initial_maximum_calls=report["maximum_calls"],
        maximum_calls=args.max_total_calls,
        source_sha256=current,
    )
    report.pop("stopped_reason", None)

    def spent():
        return sum(
            json.loads(path.read_text()).get("attempted_calls", 0)
            for path in output.parent.rglob("requests.json")
        )

    report["calls_before_continuation"] = spent()

    def save():
        report["summary"] = summarize(report["runs"])
        report["tool_inventory"] = tool_difference(report["runs"])
        report["attempted_calls"] = spent()
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Refusing credential in report")
        temporary = output.with_suffix(".tmp")
        temporary.write_text(encoded + "\n")
        temporary.replace(output)

    randomizer = random.Random(report["order_seed"])
    try:
        for repetition in range(1, report["repetitions"] + 1):
            for name, case in cases.items():
                arms = ["product", "official-sdk"]
                randomizer.shuffle(arms)
                existing = [
                    r["arm"]
                    for r in report["runs"]
                    if r["case"] == name and r["repetition"] == repetition
                ]
                if set(existing) == set(arms) and len(existing) == 2:
                    continue
                if existing:
                    raise RuntimeError("Partial pair cannot be silently rerun")
                if spent() + 2 * CALLS_PER_ARM > args.max_total_calls:
                    report["stopped_reason"] = "insufficient_budget_for_full_pair"
                    return
                for arm in arms:
                    if source_inventory() != current:
                        raise RuntimeError("Frozen source changed during continuation")
                    directory = artifact_root / f"r{repetition}-{name}-{arm}"
                    record = evaluate(arm, case, settings, directory)
                    record.update(
                        case=name,
                        repetition=repetition,
                        artifact_directory=str(directory.relative_to(ROOT)),
                    )
                    report["runs"].append(record)
                    save()
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "repetition": repetition,
                                "arm": arm,
                                "passed": record["passed"],
                                "calls": record["gateway"]["attempted_calls"],
                                "total_calls": report["attempted_calls"],
                            }
                        ),
                        flush=True,
                    )
        report["all_planned_pairs_completed"] = True
    finally:
        save()


if __name__ == "__main__":
    main()
