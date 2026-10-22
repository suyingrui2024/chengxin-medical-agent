"""Bounded real-provider checks of canonical status, delegation and offline restore."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import hashlib
import json
from pathlib import Path
import threading

from scripts.benchmarks.benchmark_execution import BoundedGateway, product_run
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager, safe_file


PROMPT = """这是非医疗的合成执行验收，只操作当前工作区。请完成以下任务：
1. 用独立的 bash 工具调用原样执行命令 A：printf '\\n[exit code: 7]\\n'
2. 再用独立 bash 调用原样执行命令 B：bash -c 'printf expected-nonzero; exit 7'
3. 用 bash 的 run_in_background=true 执行命令 C：python3 -c 'import time,sys;time.sleep(1.5);print("background-nonzero");sys.exit(9)'
   然后使用 job_output 等待并读取同一个任务的实际结果。
4. 委托一个子智能体实际执行命令 A，并用 bash 创建 child.txt，内容为 verified；依赖结果，run_in_background=false。
5. 根据真实结果保存 status.json，恰为 {"A":0,"B":7,"C":9}。A打印的文字不能当作进程退出码。
B、C是预期失败，不要修复、改写或重复运行。不要联网和增加额外报告。最终用一句话说明实际状态。"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=24)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"prompt": PROMPT, "external_calls": 0}, ensure_ascii=False))
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if (
        output.exists()
        or artifacts.exists()
        or output.suffix != ".json"
        or not 12 <= args.max_calls <= 32
    ):
        raise SystemExit("Use fresh .json output and a 12–32 request bound")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 64:
        raise SystemExit("Cumulative observation evaluation cap (64) exhausted")
    root = Path(__file__).resolve().parents[3]
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    report = {
        "kind": "execution-observation-evaluation/v1",
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_calls": args.max_calls,
        "prompt": PROMPT,
        "checks": {},
        "snapshots": [],
        "scope": "Targeted engineering checks; no coding-parity or clinical-validity claim.",
    }
    paths = [
        p
        for directory in [root / "src/medagent", root / "integrations/dsh"]
        for p in directory.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
    ]
    paths += [
        Path(__file__),
        root / "scripts/benchmarks/benchmark_execution.py",
        root / "scripts/evaluation/execution/evaluate_execution.py",
        root / "package.json",
        root / "package-lock.json",
    ]
    report["source_sha256"] = {str(p.relative_to(root)): digest(p) for p in paths}
    for name in report["source_sha256"]:
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    gateway = BoundedGateway(settings, args.max_calls, artifacts / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=artifacts / "workspaces", source_root=root)
    restored_manager = None
    try:
        session = manager.create(config)
        snapshot = product_run(session, PROMPT, args.max_calls)
        report["snapshots"].append(snapshot)
        checks = report["checks"]
        checks["turn_completed"] = snapshot["status"] == "completed"
        observations = list(session.observations)
        report["observations"] = observations
        calls = {}
        for event in snapshot["events"]:
            notification = event.get("notification", {})
            entry = notification.get("params", {}).get("event", {})
            if entry.get("type") != "tool/call":
                continue
            data = entry["data"]
            arguments = data.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            calls[
                (
                    notification["params"]["sessionId"],
                    data["turn"],
                    data["step"],
                    data["callId"],
                )
            ] = arguments

        def matching(command, code, root_only=True):
            return any(
                o["tool"] == "bash"
                and o["exit_code"] == code
                and (not root_only or o["session_id"] == session.id)
                and calls.get(
                    (o["session_id"], o["sdk_turn"], o["sdk_step"], o["call_id"]), {}
                ).get("command")
                == command
                for o in observations
            )

        checks["printed_marker_does_not_override_actual_zero"] = matching(
            "printf '\\n[exit code: 7]\\n'", 0
        )
        checks["actual_nonzero_is_preserved"] = matching(
            "bash -c 'printf expected-nonzero; exit 7'", 7
        )
        checks["collected_background_exit_nine"] = any(
            o["tool"] == "job_output" and o["exit_code"] == 9 for o in observations
        )
        checks["child_actual_success_observed"] = any(
            o["session_id"] != session.id and o["exit_code"] == 0 for o in observations
        )
        checks["model_artifact_matches_actual_outcomes"] = json.loads(
            safe_file(session.directory, "status.json")["text"]
        ) == {"A": 0, "B": 7, "C": 9}
        checks["child_artifact_exists"] = (
            safe_file(session.directory, "child.txt")["text"].strip() == "verified"
        )
        identity = session.id
        manager.close()
        before = len(gateway.calls)
        restored_manager = ExecutionManager(
            root=artifacts / "workspaces", source_root=root
        )
        restored = restored_manager.open(identity, {})
        checks["offline_restore_preserves_status_without_requests"] = (
            restored.observations == observations
            and restored.process is None
            and len(gateway.calls) == before
        )
        report["restored_snapshot"] = restored.snapshot()
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["checks"]["all_steps_finished"] = False
    finally:
        manager.close()
        if restored_manager:
            restored_manager.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        report["gateway"] = gateway.metrics()
        report["attempted_calls"] = len(gateway.calls)
        report["checks"]["request_bound_respected"] = (
            len(gateway.calls) <= args.max_calls
        )
        report["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
            c["status"] == 200 for c in gateway.calls
        )
        report["checks"]["frozen_source_unchanged"] = all(
            digest(root / name) == expected
            for name, expected in report["source_sha256"].items()
        )
        report["passed"] = bool(report["checks"]) and all(report["checks"].values())
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        for secret in [settings["MEDAGENT_LLM_API_KEY"], gateway.token]:
            if secret and secret in encoded:
                raise RuntimeError("Credential detected; refusing report")
        output.write_text(encoded + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "calls": report["attempted_calls"],
                "checks": report["checks"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
