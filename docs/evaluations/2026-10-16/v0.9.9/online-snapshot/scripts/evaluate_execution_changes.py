"""Bounded real-model exercise of file review, child tools and offline restore."""

import argparse
import hashlib
import json
from pathlib import Path
import threading
import time

from benchmark_execution import BoundedGateway, check_artifacts
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager, safe_file


FILES = {
    "calculator.py": "def add(a, b):\n    return a - b\n",
    "tests/test_existing.py": (
        "import unittest\nfrom calculator import add\n"
        "class Existing(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    ),
    "run_checks.py": (
        "from pathlib import Path\nimport unittest\n"
        "Path('tests/transient-output.txt').write_text('disposable test output\\n')\n"
        "result = unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests'))\n"
        "raise SystemExit(0 if result.wasSuccessful() else 1)\n"
    ),
}
PROMPT = """这是非医疗的合成工程验收。只操作当前工作区，不联网。
修复 calculator.py 的 add(a,b)，使其对整数进行正确加法。原有 tests/test_existing.py 与
run_checks.py 必须逐字保持不变。实际运行 python3 run_checks.py；新增回归测试只能写入
regressions/test_*.py，并实际执行。既有检查会在 tests 下产生一次性输出文件。
使用 workspace_changes 核对本轮真实变更，确认并清除本轮产生的一次性输出，保留原文件。
委托一个子智能体做独立复核（run_in_background=false）：必须亲自调用 workspace_changes
查看 calculator.py 差异、验证加法，保存 review.txt。子任务不要重跑 run_checks.py。
结束前再次调用 workspace_changes 核对结果。不要生成其它报告，最终简短说明实际完成情况。"""
PROBE = """import sys, unittest
sys.path.insert(0, '/workspace')
from calculator import add
for a,b in [(2,3),(-5,2),(0,0),(10**100,7)]:
    assert add(a,b) == a+b, (a,b)
existing = unittest.defaultTestLoader.discover('/workspace/tests', pattern='test_*.py')
assert existing.countTestCases() > 0, 'no existing tests'
assert unittest.TextTestRunner().run(existing).wasSuccessful(), 'existing tests failed'
suite = unittest.defaultTestLoader.discover('/workspace/regressions', pattern='test_*.py')
assert suite.countTestCases() > 0, 'no regression tests'
result = unittest.TextTestRunner().run(suite)
assert result.wasSuccessful(), 'regressions failed'
print('independent addition and regression checks passed')
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=20)
    args = parser.parse_args()
    if not args.live:
        print(json.dumps({"prompt": PROMPT, "external_calls": 0}, ensure_ascii=False))
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.suffix != ".json" or output.exists() or artifacts.exists():
        raise SystemExit("Use a fresh .json output path")
    if not 12 <= args.max_calls <= 24:
        raise SystemExit("Request bound must be 12–24")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 48:
        raise SystemExit("Cumulative file-review evaluation cap (48) exhausted")
    root = Path(__file__).resolve().parents[1]
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    paths = [
        p
        for directory in [root / "src/medagent", root / "integrations/dsh"]
        for p in directory.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
    ] + [
        Path(__file__),
        root / "scripts/benchmark_execution.py",
        root / "package.json",
        root / "package-lock.json",
    ]
    report = {
        "kind": "execution-change-evaluation/v1",
        "synthetic_only": True,
        "scope": "Targeted engineering exercise, not overall DSH parity or clinical validation.",
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_calls": args.max_calls,
        "time_budget_seconds": 600,
        "prompt": PROMPT,
        "seed_files": FILES,
        "source_sha256": {str(p.relative_to(root)): digest(p) for p in paths},
        "checks": {},
    }
    for name in report["source_sha256"]:
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    gateway = BoundedGateway(
        settings, args.max_calls, artifacts / "requests.json", capture_tools=True
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=artifacts / "workspaces", source_root=root)
    restored_manager = None
    directory = None
    try:
        session = manager.create(config)
        directory = session.directory
        for name, text in FILES.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        session.start_turn(PROMPT, args.max_calls, 600)
        deadline = time.monotonic() + 610
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if snapshot["status"] not in {"running", "starting", "cancelling"}:
                break
            time.sleep(0.2)
        else:
            session.cancel()
            raise TimeoutError("Evaluation deadline exceeded")
        report["snapshot"] = snapshot
        checks = report["checks"]
        checks["turn_completed"] = snapshot["status"] == "completed"
        checks["selected_budget_retained"] = (
            snapshot["turns"][-1]["time_budget_seconds"] == 600
        )
        tool_calls = []
        for event in snapshot.get("events", []):
            notification = event.get("notification", {})
            params = notification.get("params", {})
            entry = params.get("event", {})
            if entry.get("type") == "tool/call":
                tool_calls.append({"session": params.get("sessionId"), **entry["data"]})
        report["tool_calls"] = tool_calls

        def arguments(call):
            value = call.get("arguments", {})
            return json.loads(value) if isinstance(value, str) else value

        checks["original_runner_actually_invoked"] = any(
            call.get("name") == "bash"
            and "run_checks.py" in arguments(call).get("command", "")
            for call in tool_calls
        )
        checks["child_actually_executed_check"] = any(
            call.get("name") == "bash" and call.get("session") != session.id
            for call in tool_calls
        )
        for label, child in [
            ("root_reviewed_actual_changes", False),
            ("child_reviewed_actual_changes", True),
        ]:
            checks[label] = any(
                call.get("name") == "workspace_changes"
                and (call.get("session") != session.id) == child
                for call in tool_calls
            )
        checks["protected_files_byte_identical"] = all(
            safe_file(directory, name)["text"] == FILES[name]
            for name in ["tests/test_existing.py", "run_checks.py"]
        )
        checks["temporary_test_output_removed"] = not (
            directory / "tests/transient-output.txt"
        ).exists()
        checks["child_review_saved"] = bool(
            safe_file(directory, "review.txt")["text"].strip()
        )
        changes = session.changes(path="calculator.py")
        report["changes"] = changes
        checks[
            "host_diff_contains_real_before_and_after"
        ] = "-    return a - b" in changes["detail"]["diff"] and any(
            e["path"] == "calculator.py" and e["state"] == "modified"
            for e in changes["entries"]
        )
        identity = session.id
        manager.close()
        before = len(gateway.calls)
        restored_manager = ExecutionManager(
            root=artifacts / "workspaces", source_root=root
        )
        restored = restored_manager.open(identity, {})
        checks["offline_restore_preserves_diff_without_requests"] = (
            restored.changes(path="calculator.py")["detail"] == changes["detail"]
            and restored.process is None
            and len(gateway.calls) == before
        )
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
        if directory:
            report["independent_verification"] = check_artifacts(
                directory, artifacts / "verification-state", PROBE
            )
            report["checks"]["independent_implementation_and_regressions"] = (
                report["independent_verification"].get("passed") is True
            )
        report["checks"]["request_bound_respected"] = (
            len(gateway.calls) <= args.max_calls
        )
        report["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
            c["status"] == 200 for c in gateway.calls
        )
        report["checks"]["tool_available_in_actual_requests"] = any(
            "workspace_changes" in c.get("tool_names", []) for c in gateway.calls
        )
        report["checks"]["frozen_source_unchanged"] = all(
            digest(root / name) == sha for name, sha in report["source_sha256"].items()
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
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
