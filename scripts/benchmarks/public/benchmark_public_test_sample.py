"""Preregistered public TEST pairs: same sources, task, model, calls and time budget."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import threading
import time

from scripts.benchmarks.benchmark_execution import Baseline, BoundedGateway
from scripts.benchmarks.execution_task_contract import prompt_for, CONTRACT
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager
from scripts.benchmarks.public.public_execution_cases import protected
from scripts.benchmarks.public.public_test_sample import (
    ROOT,
    FIXTURES,
    inventory,
    compliance,
    run_code,
    code_for,
    parse,
    classify,
)

POLICY = "本次无人值守，只提供原问题、仓库代码和文件契约。请依据这些信息修复；不能确定的冲突应在结果中说明，不修改受保护文件或检索上游答案。"
CALLS = 20
SECONDS = 600


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_inventory():
    paths = [ROOT / "package.json", ROOT / "package-lock.json"]
    for name in [
        "src/medagent",
        "integrations/dsh",
        "scripts",
        "benchmarks/swe-lite-test-sample",
    ]:
        paths += [
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".js", ".mjs", ".json", ".yml", ".html", ".css", ".svg", ".txt"}
        ]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def task_prompt(instance):
    # Same file contract generator used by the independent compliance checker.
    return (
        prompt_for({"metadata": instance})
        + f"\n\n[共同执行条件] 主任务、子任务和辅助搜索合计最多 {CALLS} 次模型请求，总时限 {SECONDS} 秒。必须使用 python3 benchmark_run_tests.py <测试路径>，它会选择该题固定解释器和依赖；不要假定系统 python3 与历史环境完全相同。\n[可提供的全部澄清信息] {POLICY}"
    )


def evaluate(arm, instance, environment, mapping, settings, output, prompt):
    output.mkdir(parents=True)
    gateway = BoundedGateway(
        settings, CALLS, output / "requests.json", capture_tools=True
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = runner = directory = None
    result = {
        "arm": arm,
        "checks": {},
        "maximum_calls": CALLS,
        "time_budget_seconds": SECONDS,
        "snapshots": [],
        "clarification_policy": POLICY,
    }
    started = time.monotonic()
    try:
        source = ROOT / environment["prepared_source"]
        if inventory(source) != environment["source_sha256"]:
            raise ValueError("Prepared fixture drift")
        if arm == "product":
            manager = ExecutionManager(root=output / "workspaces", source_root=ROOT)
            runner = manager.create(config)
            directory = runner.directory
            state = runner.state_directory
        else:
            directory = output / "workspace"
            state = output / "state"
            directory.mkdir()
            state.mkdir()
        shutil.copytree(source, directory, dirs_exist_ok=True)
        if arm == "official-sdk":
            runner = Baseline(
                directory,
                state,
                config,
                entry_point=ROOT / "scripts/benchmarks/public/execution_test_baseline.mjs",
                time_budget_seconds=SECONDS,
            )
            runner.receive("ready", 30)
            snapshot = runner.run(prompt, CALLS)
        else:
            runner.start_turn(prompt, CALLS, SECONDS)
            deadline = time.monotonic() + SECONDS + 15
            answered = set()
            events = {}
            while time.monotonic() < deadline:
                snapshot = runner.snapshot(after=max(events, default=0))
                events.update({e["sequence"]: e for e in snapshot["events"]})
                for q in snapshot.get("clarifications", []):
                    if q["status"] == "pending" and q["id"] not in answered:
                        answered.add(q["id"])
                        runner.answer_clarification(q["id"], q["turn_id"], POLICY)
                if (
                    snapshot["status"] not in {"starting", "running", "cancelling"}
                    and not runner.has_pending_clarification()
                ):
                    break
                time.sleep(0.1)
            else:
                runner.cancel()
                raise TimeoutError("Product time budget exceeded")
            snapshot = runner.snapshot()
            events.update({e["sequence"]: e for e in snapshot["events"]})
            snapshot["events"] = [events[k] for k in sorted(events)]
        result["snapshots"].append(snapshot)
        result["runtime_completed"] = snapshot["status"] == "completed"
    except Exception as error:
        result["runtime_completed"] = False
        result["error_type"] = type(error).__name__
        result["diagnostic"] = (
            str(error)
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
            .replace(gateway.token, "[REDACTED]")[:1500]
        )
    finally:
        if manager:
            manager.close()
        elif runner:
            runner.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
        result["execution_duration_ms"] = round((time.monotonic() - started) * 1000)
    result["checks"]["request_bound_respected"] = len(gateway.calls) <= CALLS
    result["checks"]["all_provider_responses_ok"] = bool(gateway.calls) and all(
        call["status"] == 200 for call in gateway.calls
    )
    try:
        if directory is not None:
            checks = compliance(directory, environment["source_sha256"])
            result["checks"].update(checks)
            if checks.get("source_readable"):
                current = inventory(directory)
                result["candidate_sha256"] = current
                verification = output / "verification-source"
                shutil.copytree(ROOT / environment["prepared_source"], verification)
                # Source correctness is separate from byte-protection compliance:
                # always restore frozen tests/config/dependencies before applying the oracle.
                for name in set(current) | set(environment["source_sha256"]):
                    if (
                        protected(name)
                        or name == ".benchmark_dependencies"
                        or Path(name).name in CONTRACT["forbidden_new_basenames"]
                    ):
                        continue
                    if current.get(name) == environment["source_sha256"].get(name):
                        continue
                    target = verification / name
                    if name not in current:
                        target.unlink(missing_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(directory / name, target)
                result["independent_verification"] = classify(
                    parse(
                        run_code(
                            verification,
                            output / "verification-state",
                            code_for(instance, "candidate", mapping),
                        )
                    )
                )
    except (OSError, ValueError, KeyError, TimeoutError) as error:
        result["verification_error"] = {
            "type": type(error).__name__,
            "diagnostic": str(error)[:1500],
        }
        result["checks"]["independent_verification_available"] = False
    result["source_correct"] = result.get("independent_verification", {}).get(
        "passed", False
    )
    result["source_and_contract_passed"] = (
        result["source_correct"]
        and bool(result["checks"])
        and all(result["checks"].values())
    )
    result["end_to_end_completed"] = (
        result["source_and_contract_passed"] and result["runtime_completed"]
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repetitions", type=int, default=2, choices=[1, 2])
    parser.add_argument("--order-seed", type=int, default=2026101602)
    parser.add_argument("--max-total-calls", type=int, default=512)
    args = parser.parse_args()
    selected = json.loads((FIXTURES / "selection.json").read_text())
    environments = json.loads((FIXTURES / "environments-v5.json").read_text())
    preflight = json.loads(args.preflight.read_text())
    if (
        preflight.get("selected_ids") != selected["selected_ids"]
        or not preflight.get("all_six_qualified")
        or not all(
            preflight["cases"][name].get("qualified")
            for name in selected["selected_ids"]
        )
    ):
        raise SystemExit(
            "All six preregistered tasks must qualify before paid evaluation; no replacements"
        )
    if preflight["environment_manifest_sha256"] != digest(
        FIXTURES / "environments-v5.json"
    ):
        raise SystemExit("Preflight environment drift")
    randomizer = random.Random(args.order_seed)
    plan = []
    for repetition in range(1, args.repetitions + 1):
        for identity in selected["selected_ids"]:
            arms = ["product", "official-sdk"]
            randomizer.shuffle(arms)
            plan += [
                {"case": identity, "repetition": repetition, "arm": arm} for arm in arms
            ]
    if not args.live:
        print(
            json.dumps(
                {
                    "plan": plan,
                    "maximum_planned_requests": len(plan) * CALLS,
                    "external_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if (
        output.exists()
        or artifacts.exists()
        or output.suffix != ".json"
        or not len(plan) * CALLS <= args.max_total_calls <= 512
    ):
        raise SystemExit(
            "Use fresh output and sufficient hard request budget, maximum512"
        )
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + len(plan) * CALLS > args.max_total_calls:
        raise SystemExit(
            "Insufficient cumulative directory budget for the complete plan"
        )
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    instances = {
        name: json.loads((FIXTURES / name / "instance.json").read_text())
        for name in selected["selected_ids"]
    }
    report = {
        "kind": "paired-preregistered-public-test/v1",
        "runtime_version": __version__,
        "scope": "Six randomly selected public historical TEST tasks; local environment adaptation, not an uncontaminated holdout, official score or proof of overall parity.",
        "selection": selected,
        "preflight_sha256": digest(args.preflight),
        "environment_manifest_sha256": digest(FIXTURES / "environments-v5.json"),
        "model": settings["MEDAGENT_LLM_MODEL"],
        "plan": plan,
        "calls_per_arm": CALLS,
        "seconds_per_arm": SECONDS,
        "maximum_calls": args.max_total_calls,
        "prior_directory_calls": prior,
        "prompts": {
            name: task_prompt(instance) for name, instance in instances.items()
        },
        "source_sha256": source_inventory(),
        "runs": [],
        "attempted_calls": 0,
        "full_dsh_parity_proven": False,
        "clinical_validation_complete": False,
    }
    for name, expected in report["source_sha256"].items():
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source drift during freeze")
        target.write_bytes(data)

    def save():
        report["attempted_calls"] = sum(
            row["gateway"]["attempted_calls"] for row in report["runs"]
        )
        report["frozen_source_unchanged"] = (
            source_inventory() == report["source_sha256"]
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise ValueError("Credential detected; refusing report")
        output.write_text(encoded + "\n")

    save()
    try:
        for task in plan:
            if source_inventory() != report["source_sha256"]:
                raise ValueError("Frozen source/fixtures changed")
            name = task["case"]
            folder = artifacts / f"r{task['repetition']}-{name}-{task['arm']}"
            result = evaluate(
                task["arm"],
                instances[name],
                environments["cases"][name],
                preflight["cases"][name]["test_id_map"],
                settings,
                folder,
                report["prompts"][name],
            )
            result.update(task)
            report["runs"].append(result)
            save()
            print(
                json.dumps(
                    {
                        **task,
                        "source_correct": result["source_correct"],
                        "source_and_contract_passed": result[
                            "source_and_contract_passed"
                        ],
                        "runtime_completed": result["runtime_completed"],
                        "calls": result["gateway"]["attempted_calls"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    finally:
        save()


if __name__ == "__main__":
    main()
