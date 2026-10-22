"""Adapted SWE-bench Lite DEV comparison; not an official SWE-bench score."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import json
from pathlib import Path
import random
import shutil
import threading
import time

from scripts.benchmarks.benchmark_execution import Baseline, BoundedGateway, ROOT, product_run, summarize
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager
from medagent import __version__
from scripts.benchmarks.public.public_execution_cases import (
    FIXTURES,
    frozen_source_check,
    inventory,
    load_cases,
    seed,
    sha256,
    verify,
)

KIND = "paired-public-dev-execution/v1"
CALLS_PER_ARM = 24


def prompt_for(case):
    return (
        "请在当前真实开源仓库中解决以下缺陷，实际修改实现、执行相关测试并检查结果。\n"
        "保留已有测试、配置、依赖、LICENSE 和 benchmark_run_tests.py；可以新建普通回归测试文件。\n"
        "固定的 Python 环境和依赖已准备好，使用 /usr/bin/python3 benchmark_run_tests.py <测试路径> 运行 pytest；"
        "不安装或替换依赖。只根据本地代码、问题描述和实际测试完成修复，勿检索上游答案或参考补丁。\n"
        "任务结束时简要说明修改及实际检查，不扩大到其他功能。\n\n" + case["metadata"]["problem_statement"]
    )


def preflight(cases, output):
    output = output.resolve()
    output.mkdir(parents=True)
    results = {}
    for name, case in cases.items():
        results[name] = {}
        for reference in [False, True]:
            variant = "reference" if reference else "original"
            directory = output / name / variant
            seed(directory, case)
            results[name][variant] = verify(
                directory, output / name / (variant + "-state"), case, reference
            )
        (output / "report.json").write_text(json.dumps(results, indent=2) + "\n")
        if not (
            results[name]["original"].get("original_bug_reproduced")
            and results[name]["reference"].get("passed")
        ):
            raise RuntimeError("Public task preflight failed: " + name)
    return results


def evaluate(
    arm, case, settings, output, *, prompt=None, source_check=frozen_source_check
):
    output.mkdir(parents=True)
    gateway = BoundedGateway(
        settings, CALLS_PER_ARM, output / "requests.json", capture_tools=True
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = runner = directory = original = None
    result = {"arm": arm, "snapshots": [], "checks": {}, "maximum_calls": CALLS_PER_ARM}
    started = time.perf_counter()
    try:
        if arm == "product":
            manager = ExecutionManager(root=output / "workspaces", source_root=ROOT)
            runner = manager.create(config)
            directory, state = runner.directory, runner.state_directory
        else:
            directory, state = output / "workspace", output / "state"
            directory.mkdir(mode=0o700)
            state.mkdir(mode=0o700)
        original = seed(directory, case)
        if arm == "official-sdk":
            runner = Baseline(directory, state, config)
            runner.receive("ready", 30)
        gateway.begin_turn(CALLS_PER_ARM)
        snapshot = (
            product_run(
                runner,
                prompt if prompt is not None else prompt_for(case),
                CALLS_PER_ARM,
            )
            if arm == "product"
            else runner.run(
                prompt if prompt is not None else prompt_for(case), CALLS_PER_ARM
            )
        )
        result["snapshots"].append(snapshot)
        result["checks"]["runtime_completed"] = snapshot["status"] == "completed"
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["diagnostic"] = (
            str(exc)
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
            .replace(gateway.token, "[REDACTED]")[:2000]
        )
        result["checks"]["runtime_completed"] = False
    finally:
        if manager:
            manager.close()
        elif runner:
            runner.close()
        result["execution_duration_ms"] = round((time.perf_counter() - started) * 1000)
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
    # Close the entire candidate tree before mounting an independent test copy.
    result["checks"].update(
        request_budget_respected=len(gateway.calls) <= CALLS_PER_ARM,
        no_provider_errors=bool(gateway.calls)
        and all(c["status"] == 200 for c in gateway.calls),
    )
    if directory is not None and original is not None:
        result["checks"].update(source_check(directory, original))
        if all(
            result["checks"].get(name)
            for name in [
                "source_readable",
                "upstream_tests_and_config_preserved",
                "no_new_test_control_files",
            ]
        ):
            verification = output / "verification-workspace"
            shutil.copytree(
                directory,
                verification,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
            result["independent_verification"] = verify(
                verification, output / "verification-state", case
            )
            result["artifact_sha256"] = inventory(directory)
    result["checks"]["public_task_tests"] = result.get(
        "independent_verification", {}
    ).get("passed", False)
    result["passed"] = all(result["checks"].values())
    return result


def tool_difference(records):
    tools = {}
    for arm in ["product", "official-sdk"]:
        tools[arm] = set().union(
            *(
                set(c.get("tool_names", []))
                for r in records
                if r["arm"] == arm
                for c in r["gateway"]["calls"]
            )
        )
    return {
        "shared": sorted(tools["product"] & tools["official-sdk"]),
        "product_only": sorted(tools["product"] - tools["official-sdk"]),
        "official_only": sorted(tools["official-sdk"] - tools["product"]),
    }


def source_inventory():
    paths = [ROOT / "package.json", ROOT / "package-lock.json"]
    for name in [
        "src/medagent",
        "integrations/dsh",
        "scripts",
        "benchmarks/swe-lite-dev",
    ]:
        paths.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".mjs", ".js", ".yml", ".json", ".html", ".css", ".svg", ".txt"}
        )
    return {
        str(path.relative_to(ROOT)): sha256(path.read_bytes()) for path in sorted(paths)
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--max-total-calls", type=int, default=256)
    parser.add_argument("--order-seed", type=int, default=20261016)
    args = parser.parse_args()
    output = args.output.resolve()
    artifact_root = output.with_suffix("")
    if (
        output.exists()
        or artifact_root.exists()
        or not 1 <= args.repetitions <= 5
        or not 48 <= args.max_total_calls <= 512
    ):
        raise SystemExit(
            "Use fresh output, 1–5 repetitions, and a 48–512 cumulative call cap"
        )
    manifest, cases = load_cases()
    output.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir()
    report = {
        "kind": KIND,
        "live": args.live,
        "public_development_tasks": True,
        "no_user_patient_data": True,
        "public_repository_test_fixtures": True,
        "official_swe_bench_score": False,
        "full_dsh_parity_proven": False,
        "scope": "Four manually selected public DEV tasks; adapted fixed system Python/pytest in bubblewrap, not official Docker harness. Same configured model, source, user prompt, total task time and call limits. Product persona, budget advice, registered medical tools, foreground wait limits and default pipefail adapter differ. The SDK baseline keeps the pinned upstream shell defaults. Network is available; the prompt prohibits fetching upstream solutions. These are known development tasks, not an unseen holdout or clinical study.",
        "product_version": __version__,
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
        "manifest_sha256": sha256((FIXTURES / "manifest.json").read_bytes()),
        "dsh_version": "0.2.0-rc.2",
        "repetitions": args.repetitions,
        "order_seed": args.order_seed,
        "maximum_calls": args.max_total_calls,
        "calls_per_arm": CALLS_PER_ARM,
        "attempted_calls": 0,
        "runs": [],
        "source_sha256": source_inventory(),
        "prompts_sha256": {
            name: sha256(prompt_for(case).encode()) for name, case in cases.items()
        },
    }
    for name, digest in report["source_sha256"].items():
        data = (ROOT / name).read_bytes()
        if sha256(data) != digest:
            raise RuntimeError("Source changed during freeze")
        target = artifact_root / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    report["preflight"] = preflight(cases, artifact_root / "preflight")
    settings = for_backend(read_env(ROOT / ".env"), "dsh") if args.live else {}
    if args.live:
        report["model"] = settings["MEDAGENT_LLM_MODEL"]
    prior = sum(
        json.loads(path.read_text()).get("attempted_calls", 0)
        for path in output.parent.rglob("requests.json")
        if artifact_root not in path.parents
    )
    report["prior_directory_calls"] = prior

    def save():
        report["summary"] = summarize(report["runs"])
        report["tool_inventory"] = tool_difference(report["runs"])
        report["attempted_calls"] = sum(
            json.loads(path.read_text()).get("attempted_calls", 0)
            for path in artifact_root.rglob("requests.json")
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.live and settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Refusing to write credential")
        temporary = output.with_suffix(".tmp")
        temporary.write_text(encoded + "\n")
        temporary.replace(output)

    save()
    if not args.live:
        print(
            json.dumps({"preflight_passed": True, "cases": list(cases), "live": False})
        )
        return
    randomizer = random.Random(args.order_seed)
    try:
        for repetition in range(1, args.repetitions + 1):
            for name, case in cases.items():
                if source_inventory() != report["source_sha256"]:
                    raise RuntimeError(
                        "Frozen evaluator/runtime changed during evaluation"
                    )
                if (
                    args.max_total_calls - prior - report["attempted_calls"]
                    < 2 * CALLS_PER_ARM
                ):
                    report["stopped_reason"] = "insufficient_budget_for_full_pair"
                    return
                arms = ["product", "official-sdk"]
                randomizer.shuffle(arms)
                for arm in arms:
                    record = evaluate(
                        arm,
                        case,
                        settings,
                        artifact_root / f"r{repetition}-{name}-{arm}",
                    )
                    record.update(case=name, repetition=repetition)
                    report["runs"].append(record)
                    save()
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "repetition": repetition,
                                "arm": arm,
                                "passed": record["passed"],
                                "checks": record["checks"],
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
