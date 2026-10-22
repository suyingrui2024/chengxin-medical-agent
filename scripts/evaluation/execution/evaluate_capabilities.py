"""Bounded real-provider validation of registered tools, delegation and cold receipts."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import hashlib
import json
from pathlib import Path
import secrets
import threading

from scripts.benchmarks.benchmark_execution import BoundedGateway, product_run
from scripts.evaluation.execution.evaluate_persistence import ContextWitness
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager


def tool_calls(snapshot):
    return [
        (
            event["notification"]["params"].get("sessionId"),
            event["notification"]["params"]["event"].get("data", {}),
        )
        for event in snapshot["events"]
        if event.get("type") == "runtime.event"
        and event.get("notification", {}).get("method") == "session.event"
        and event["notification"]["params"]["event"].get("type") == "tool/call"
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=32)
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        output.suffix != ".json"
        or output.exists()
        or output.with_suffix("").exists()
        or not 24 <= args.max_calls <= 32
    ):
        raise SystemExit("Use a fresh .json path and a 24–32 call limit")
    prior = sum(
        json.loads(path.read_text()).get("attempted_calls", 0)
        for path in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 64:
        raise SystemExit("Cumulative capability evaluation cap (64) exhausted")
    root = Path(__file__).resolve().parents[3]
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts = output.with_suffix("")
    artifacts.mkdir(parents=True)
    gateway = BoundedGateway(settings, args.max_calls, artifacts / "requests.json")
    gateway.RequestHandlerClass = ContextWitness
    gateway.phase, gateway.marker, gateway.first_resume_context = (
        "initial",
        "capability-" + secrets.token_hex(8),
        None,
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    report = {
        "kind": "registered-capability-evaluation/v1",
        "synthetic_only": True,
        "maximum_calls": args.max_calls,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "checks": {},
        "snapshots": [],
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    report["source_sha256"] = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for base in [root / "src/medagent", root / "integrations/dsh"]
        for path in base.rglob("*")
        if path.is_file()
        and path.suffix
        in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
    }
    for name, expected in report["source_sha256"].items():
        data = (root / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manager, second = (
        ExecutionManager(root=artifacts / "workspaces", source_root=root),
        None,
    )
    try:
        session = manager.create(config)
        initial = product_run(
            session,
            "合成研发验收，项目代号" + gateway.marker + "。明确输入是1.25 kg和0.0005 L。"
            "先查询注册能力。为验证错误修正，请先实际调用注册工具尝试将1.25 kg换算成mL；缺密度时应拒绝。"
            "观察错误后改为把1.25 kg换成g。另外委托一个子智能体通过同一注册工具把0.0005 L换成mL。"
            "这只是工具错误恢复测试，不是医疗建议。不要执行脚本、联网或写文件，不要重复复核，计算回执足够，最后简短说明输入仍待核对。",
            16,
        )
        report["snapshots"].append(initial)
        receipts = initial["calculations"]
        report["checks"]["initial_completed"] = initial["status"] == "completed"
        report["checks"]["actual_rejection_observed"] = any(
            e["type"] == "capability.failed"
            and e.get("error", {}).get("code") == "calculation_rejected"
            for e in initial["events"]
        )
        report["checks"]["correct_registered_results"] = {
            ("1250", "g"),
            ("0.5", "mL"),
        } <= {(r["data"]["value"], r["data"]["unit"]) for r in receipts}
        report["checks"]["child_called_registered_tool"] = any(
            identity != session.id and data.get("name") == "medical_calculate"
            for identity, data in tool_calls(initial)
        )
        report["checks"]["no_adhoc_code_or_search"] = all(
            data.get("name") not in {"bash", "web_search", "web_fetch"}
            for _, data in tool_calls(initial)
        )
        report["checks"]["inputs_remain_unconfirmed"] = bool(receipts) and all(
            r["input_status"] == "unconfirmed"
            and r["quality"] == "not_assessed"
            and r["evidence"]["scope"] == "quantity_and_source_unit_only"
            for r in receipts
        )
        process, identity = session.process, session.id
        manager.close()
        report["checks"]["old_process_exited"] = (
            process is not None and process.poll() is not None
        )
        second = ExecutionManager(root=artifacts / "workspaces", source_root=root)
        before = len(gateway.calls)
        restored = second.open(identity, {})
        report["checks"]["offline_receipts_preserved"] = (
            restored.calculations == receipts
            and len(gateway.calls) == before
            and restored.process is None
        )
        second.open(identity, config)
        gateway.phase = "resume"
        gateway.begin_turn(8)
        continued = product_run(
            restored, "继续原项目，复述原项目代号，再用注册工具把此前用户明确提供的体积换算成mL，不需要重新提问或写脚本。", 8
        )
        report["snapshots"].append(continued)
        added = continued["calculations"][len(receipts) :]
        report["checks"]["cold_resume_completed"] = (
            continued["status"] == "completed"
            and gateway.first_resume_context is True
            and gateway.marker in continued["final_response"]
        )
        report["checks"]["old_user_input_reused_with_original_evidence"] = any(
            r["data"]["value"] == "0.5"
            and r["data"]["unit"] == "mL"
            and r["evidence"]["source_turn_id"] == initial["turn_id"]
            for r in added
        )
        second.close()
        gateway.begin_turn(max(1, args.max_calls - len(gateway.calls)))
        manager = ExecutionManager(root=artifacts / "workspaces", source_root=root)
        missing = manager.create(config)
        empty = product_run(
            missing, "我想做单位换算，但尚未提供原始数值、原单位或目标单位。请告诉我缺什么；不要替我设置示例数值，不要执行脚本。", 4
        )
        report["snapshots"].append(empty)
        report["checks"]["missing_inputs_not_invented"] = (
            empty["status"] == "completed"
            and not empty["calculations"]
            and bool(empty["final_response"].strip())
        )
        index = b"".join(
            path.read_bytes()
            for path in (artifacts / "workspaces/.index").glob("*.json")
        )
        report["checks"]["credentials_not_persisted"] = all(
            secret.encode() not in index
            for secret in [gateway.token, settings["MEDAGENT_LLM_API_KEY"]]
        )
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["checks"]["all_steps_finished"] = False
    finally:
        manager.close()
        if second:
            second.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        report["gateway"] = gateway.metrics()
        report["attempted_calls"] = len(gateway.calls)
        report["checks"]["within_bound"] = len(gateway.calls) <= args.max_calls
        report["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
            item["status"] == 200 for item in gateway.calls
        )
        report["passed"] = all(report["checks"].values())
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Credential detected; report not written")
        output.write_text(encoded + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report["checks"],
                "calls": report["attempted_calls"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
