"""Bounded synthetic API conformance for rejected proposals and accepted long checks."""

import argparse
import hashlib
import json
from pathlib import Path
import threading

from benchmark_execution import BoundedGateway, product_run
from evaluate_delivery_checks import fingerprints
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager

ROOT = Path(__file__).resolve().parents[1]
INVALID = "printf SHOULD_NOT_RUN > forbidden.txt"
VALID = "python3 -c 'assert 2+2==4'"
PROMPT = f"这是非医疗合成接口验收，不联网、不安装依赖，不创建其它文件。先实际调用delivery_check负例：label为参数负例，command为{INVALID!r}，timeout_ms=1800001，required=true，watch=['.']。该值超过工具上限，应该被拒绝且命令不能执行；需要实际工具回执，不用自然语言代替。观察拒绝后，再调用delivery_check：label为实际检查，command为{VALID!r}，timeout_ms=240000，required=true，watch=['.']。第二次在本轮原时限内运行，通过后用一句话报告。不要重跑第一个被拒绝的提议，也不要通过更换成会创建forbidden.txt的命令来掩盖它。"


def run_case(settings, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    gateway = BoundedGateway(settings, 6, output / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=output / "workspaces")
    session = None
    result = {
        "kind": "check-input-recovery-conformance/v1",
        "prompt": PROMPT,
        "checks": {},
    }
    try:
        session = manager.create(config)
        snapshot = product_run(session, PROMPT, 6)
        result["snapshot"] = snapshot
        observed = [
            v for v in session.observations if v.get("tool") == "delivery_check"
        ]
        result["observations"] = observed
        calls = []
        for event in snapshot["events"]:
            value = event.get("notification", {}).get("params", {}).get("event", {})
            if (
                value.get("type") == "tool/call"
                and value.get("data", {}).get("name") == "delivery_check"
            ):
                args = value["data"]["arguments"]
                args = json.loads(args) if isinstance(args, str) else args
                calls.append({**args, "actual_call_id": value["data"]["callId"]})
        valid = [
            v
            for v in observed
            if v.get("check_key") == hashlib.sha256(VALID.encode()).hexdigest()
        ]
        result["checks"].update(
            completed=snapshot["status"] == "completed",
            actual_negative_call=any(
                v.get("command") == INVALID and v.get("timeout_ms") == 1800001
                for v in calls
            ),
            rejected_proposal_has_no_job=any(
                v.get("state") == "failed"
                and v.get("job_id") is None
                and not v.get("check_key")
                and any(
                    c.get("actual_call_id") == v.get("call_id")
                    and c.get("command") == INVALID
                    and c.get("timeout_ms") == 1800001
                    for c in calls
                )
                for v in observed
            ),
            rejected_command_never_executed=not (
                session.directory / "forbidden.txt"
            ).exists(),
            actual_valid_long_limit=any(
                v.get("command") == VALID and v.get("timeout_ms") == 240000
                for v in calls
            ),
            actual_check_exit_zero=bool(valid) and valid[-1]["exit_code"] == 0,
            current_file_version=any(
                e["type"] == "delivery.freshness"
                and e.get("check_key") == hashlib.sha256(VALID.encode()).hexdigest()
                and e.get("freshness") == "current"
                for e in snapshot["events"]
            ),
            no_extra_repair=not any(
                e["type"] == "turn.check_repairing" for e in snapshot["events"]
            ),
        )
    except Exception as error:
        result["checks"]["scenario_finished"] = False
        result["error_type"] = type(error).__name__
    finally:
        manager.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
    if session:
        restored = ExecutionManager(root=output / "workspaces")
        try:
            cold = restored.open(session.id, {})
            result["checks"]["offline_history_matches"] = (
                list(cold.observations) == list(session.observations)
                and cold.process is None
            )
        finally:
            restored.close()
    result["checks"]["bounded_requests"] = len(gateway.calls) <= 6
    result["checks"]["all_http200"] = bool(gateway.calls) and all(
        c["status"] == 200 for c in gateway.calls
    )
    result["passed"] = all(result["checks"].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "planned_cases": 1,
                    "maximum_external_calls": 6,
                    "external_model_requests": 0,
                }
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.exists() or artifacts.exists():
        raise SystemExit("Fresh output required")
    frozen = fingerprints()
    source = artifacts / "source"
    for name, sha in frozen.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError("Source drift during freeze")
    report = {"version": __version__, "source_sha256": frozen, "case": None}
    try:
        report["case"] = run_case(
            for_backend(read_env(ROOT / ".env"), "dsh"), artifacts / "case"
        )
    finally:
        report["source_unchanged_during_calls"] = fingerprints() == frozen
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": report["case"]["passed"],
                "actual_requests": report["case"]["gateway"]["attempted_calls"],
                "checks": report["case"]["checks"],
                "source_unchanged_during_calls": report[
                    "source_unchanged_during_calls"
                ],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(
        0 if report["case"]["passed"] and report["source_unchanged_during_calls"] else 1
    )


if __name__ == "__main__":
    main()
