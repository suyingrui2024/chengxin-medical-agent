"""Additional public DEV pairs with explicit byte-level file contracts."""

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
    preflight,
    source_inventory,
    tool_difference,
)
from scripts.benchmarks.execution_task_contract import CONTRACT, compliance, prompt_for
from medagent import __version__
from medagent.env_config import for_backend, read_env
from scripts.benchmarks.public.public_execution_cases import load_cases, sha256


FIXTURES = ROOT / "benchmarks/swe-lite-dev-expanded"
KIND = "paired-public-explicit-contracts/v1"


def frozen_inventory():
    result = source_inventory()
    for path in FIXTURES.rglob("*.json"):
        result[str(path.relative_to(ROOT))] = sha256(path.read_bytes())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--max-total-calls", type=int, default=384)
    parser.add_argument("--order-seed", type=int, default=20261017)
    args = parser.parse_args()
    manifest, cases = load_cases(FIXTURES)
    if not args.live and not args.preflight:
        print(
            json.dumps(
                {"cases": list(cases), "contract": CONTRACT, "external_calls": 0},
                ensure_ascii=False,
            )
        )
        return
    output = args.output.resolve()
    artifact_root = output.with_suffix("")
    if (
        output.suffix != ".json"
        or output.exists()
        or artifact_root.exists()
        or not 1 <= args.repetitions <= 3
        or not 48 <= args.max_total_calls <= 512
    ):
        raise SystemExit(
            "Use fresh .json output, 1–3 repetitions and a 48–512 cumulative call bound"
        )
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if args.live and prior + 2 * CALLS_PER_ARM > args.max_total_calls:
        raise SystemExit("Insufficient cumulative budget for a complete pair")
    artifact_root.mkdir(parents=True)
    report = {
        "kind": KIND,
        "runtime_version": __version__,
        "public_repository_fixtures_only": True,
        "patient_data_used": False,
        "scope": "Four additional manually selected public DEV tasks. Explicit file contract; not an unknown holdout, official SWE-bench score or clinical validation. Never attribute gains over legacy prompts solely to runtime improvements.",
        "manifest_sha256": sha256((FIXTURES / "manifest.json").read_bytes()),
        "dataset_parquet_sha256": manifest["dataset_parquet_sha256"],
        "fixtures": str(FIXTURES.relative_to(ROOT)),
        "contract": CONTRACT,
        "contract_sha256": sha256(json.dumps(CONTRACT, sort_keys=True).encode()),
        "prompts": {name: prompt_for(case) for name, case in cases.items()},
        "prompts_sha256": {
            name: sha256(prompt_for(case).encode()) for name, case in cases.items()
        },
        "repetitions": args.repetitions,
        "maximum_calls": args.max_total_calls,
        "calls_per_arm": CALLS_PER_ARM,
        "order_seed": args.order_seed,
        "prior_directory_calls": prior,
        "attempted_calls": 0,
        "runs": [],
        "source_sha256": frozen_inventory(),
        "full_dsh_parity_proven": False,
        "shell_conditions": {
            "product": {
                "default_wait_ms": 10000,
                "maximum_wait_ms": 30000,
                "default_pipefail": True,
                "job_wait_ms": 10000,
                "maximum_job_wait_ms": 30000,
            },
            "official-sdk": {
                "default_wait_ms": 60000,
                "maximum_wait_ms": 600000,
                "default_pipefail": False,
                "job_wait_ms": 30000,
                "maximum_job_wait_ms": 600000,
            },
        },
    }
    for name, digest in report["source_sha256"].items():
        data = (ROOT / name).read_bytes()
        if sha256(data) != digest:
            raise RuntimeError("Source changed during freeze")
        target = artifact_root / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    settings = {}

    def save():
        report["summary"] = summarize(report["runs"])
        report["tool_inventory"] = tool_difference(report["runs"])
        report["attempted_calls"] = sum(
            json.loads(p.read_text()).get("attempted_calls", 0)
            for p in artifact_root.rglob("requests.json")
        )
        report["source_checks"] = {
            arm: {
                "attempts": len([r for r in report["runs"] if r["arm"] == arm]),
                "tested": len(
                    [
                        r
                        for r in report["runs"]
                        if r["arm"] == arm and "independent_verification" in r
                    ]
                ),
                "passed": len(
                    [
                        r
                        for r in report["runs"]
                        if r["arm"] == arm
                        and r.get("independent_verification", {}).get("passed")
                    ]
                ),
            }
            for arm in ["product", "official-sdk"]
        }
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings and settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Refusing credential in report")
        temporary = output.with_suffix(".tmp")
        temporary.write_text(encoded + "\n")
        temporary.replace(output)

    save()
    try:
        report["preflight"] = preflight(cases, artifact_root / "preflight")
        save()
        if not args.live:
            print(
                json.dumps(
                    {
                        "preflight_passed": True,
                        "cases": list(cases),
                        "external_calls": 0,
                    }
                )
            )
            return
        settings = for_backend(read_env(ROOT / ".env"), "dsh")
        report["model"] = settings["MEDAGENT_LLM_MODEL"]
        randomizer = random.Random(args.order_seed)
        for repetition in range(1, args.repetitions + 1):
            for name, case in cases.items():
                if frozen_inventory() != report["source_sha256"]:
                    raise RuntimeError("Frozen runtime, evaluator or fixtures changed")
                if (
                    args.max_total_calls - prior - report["attempted_calls"]
                    < 2 * CALLS_PER_ARM
                ):
                    report["stopped_reason"] = "insufficient_budget_for_full_pair"
                    return
                arms = ["product", "official-sdk"]
                randomizer.shuffle(arms)
                for arm in arms:
                    directory = artifact_root / f"r{repetition}-{name}-{arm}"
                    record = evaluate(
                        arm,
                        case,
                        settings,
                        directory,
                        prompt=prompt_for(case),
                        source_check=compliance,
                    )
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
                                "source_passed": record.get(
                                    "independent_verification", {}
                                ).get("passed"),
                                "checks": record["checks"],
                                "calls": record["gateway"]["attempted_calls"],
                                "total_calls": report["attempted_calls"],
                            }
                        ),
                        flush=True,
                    )
        report["all_planned_pairs_completed"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        raise
    finally:
        report["frozen_source_unchanged_at_end"] = (
            frozen_inventory() == report["source_sha256"]
        )
        save()


if __name__ == "__main__":
    main()
