"""Bounded real-model exercise of detecting and resolving a concrete requirement conflict."""

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
    "alias_rule.py": "def alias_warning(has_join):\n    return 'Avoid alias'\n",
    "issue.md": "无 JOIN 时不再告警；有 JOIN 时将告警文案改为 Avoid table aliases。\n",
    "tests/test_existing.py": "import unittest\nfrom alias_rule import alias_warning\nclass Existing(unittest.TestCase):\n    def test_without_join(self):\n        self.assertEqual(alias_warning(False), 'Avoid table aliases')\n    def test_with_join(self):\n        self.assertEqual(alias_warning(True), 'Avoid table aliases')\n",
}
REGRESSION_DIRECTORY = "regressions"
PROMPT = (
    "这是非医疗的合成工程任务。请完成 issue.md 中的修复，已有 tests/test_existing.py 必须逐字不变并实际运行。"
    "检查现有文件后再修改；关键行为要求冲突时先向我核对，常规实现由你决定。"
    f"必须新增并实际运行至少一个 unittest.TestCase 回归测试，文件限定 {REGRESSION_DIRECTORY}/test_*.py；不是可选项。"
    "结束前核对真实变更，不增加额外报告。"
)
ANSWER = "以现有验收为准：没有 JOIN 也保留告警，只将文案改为 Avoid table aliases；已有测试逐字保持不变。"
PROBE = """import sys, unittest
sys.path.insert(0, '/workspace')
from alias_rule import alias_warning
assert alias_warning(False) == 'Avoid table aliases'
assert alias_warning(True) == 'Avoid table aliases'
for folder in ['tests', REGRESSION_DIRECTORY]:
    path = '/workspace/' + folder
    suite = unittest.TestLoader().discover(path, pattern='test_*.py', top_level_dir=path)
    assert suite.countTestCases() > 0, folder
    assert unittest.TextTestRunner().run(suite).wasSuccessful(), folder
print('clarified behavior and existing/regression tests passed')
""".replace(
    "REGRESSION_DIRECTORY", repr(REGRESSION_DIRECTORY)
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=16)
    args = parser.parse_args()
    if not args.live:
        print(
            json.dumps(
                {
                    "prompt": PROMPT,
                    "simulated_user_answer": ANSWER,
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
        or not 8 <= args.max_calls <= 16
    ):
        raise SystemExit("Use fresh .json output and an 8–16 request bound")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 32:
        raise SystemExit("Cumulative clarification evaluation cap (32) exhausted")
    root = Path(__file__).resolve().parents[1]
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    paths = [
        p
        for name in ["src/medagent", "integrations/dsh"]
        for p in (root / name).rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".mjs", ".js", ".css", ".html", ".svg", ".json", ".yml"}
    ]
    paths += [
        Path(__file__),
        root / "scripts/benchmark_execution.py",
        root / "package.json",
        root / "package-lock.json",
    ]
    report = {
        "kind": "execution-clarification-evaluation/v2",
        "task_contract": {
            "must_add_regressions": True,
            "regression_directory": REGRESSION_DIRECTORY,
            "framework": "unittest.TestCase",
        },
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "prompt": PROMPT,
        "simulated_user_answer": ANSWER,
        "seed_files": FILES,
        "probe": PROBE,
        "maximum_calls": args.max_calls,
        "checks": {},
        "source_sha256": {str(p.relative_to(root)): digest(p) for p in paths},
        "scope": "One synthetic conflict with prerecorded user reply; not overall DSH parity or clinical validation.",
    }
    for name in report["source_sha256"]:
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    gateway = BoundedGateway(
        settings, args.max_calls, artifacts / "requests.json", capture_tools=True
    )
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    manager = ExecutionManager(root=artifacts / "workspaces", source_root=root)
    directory = None
    restored_manager = None
    answered = set()
    before_answer = []
    try:
        config = {
            **settings,
            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
            "MEDAGENT_LLM_API_KEY": gateway.token,
        }
        session = manager.create(config)
        directory = session.directory
        for name, text in FILES.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        session.start_turn(PROMPT, args.max_calls, 180)
        deadline = time.monotonic() + 190
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            for value in snapshot["clarifications"]:
                if value["status"] == "pending" and value["id"] not in answered:
                    before_answer.append(
                        safe_file(directory, "alias_rule.py")["text"]
                        == FILES["alias_rule.py"]
                    )
                    answered.add(value["id"])
                    session.answer_clarification(value["id"], value["turn_id"], ANSWER)
            if (
                snapshot["status"] not in {"starting", "running", "cancelling"}
                and not session.has_pending_clarification()
            ):
                break
            time.sleep(0.1)
        else:
            session.cancel()
            raise TimeoutError("Evaluation deadline exceeded")
        report["snapshot"] = session.snapshot()
        report["changes"] = session.changes(path="alias_rule.py")
        checks = report["checks"]
        checks["turn_completed"] = report["snapshot"]["status"] == "completed"
        checks["one_concrete_question_answered"] = len(answered) == 1 and all(
            q["status"] == "answered" for q in session.clarifications
        )
        checks["no_source_edit_before_clarification"] = bool(before_answer) and all(
            before_answer
        )
        checks["protected_existing_test_and_issue_unchanged"] = all(
            safe_file(directory, name)["text"] == FILES[name]
            for name in ["tests/test_existing.py", "issue.md"]
        )
        checks["only_one_host_turn"] = len(session.turns) == 1
        checks["live_tool_available"] = any(
            "request_clarification" in call.get("tool_names", [])
            for call in gateway.calls
        )
        identity = session.id
        ledger = list(session.clarifications)
        manager.close()
        before = len(gateway.calls)
        restored_manager = ExecutionManager(
            root=artifacts / "workspaces", source_root=root
        )
        restored = restored_manager.open(identity, {})
        checks["offline_restore_preserves_answers_without_requests"] = (
            restored.clarifications == ledger
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
            result = check_artifacts(directory, artifacts / "verification-state", PROBE)
            report["independent_verification"] = result
            report["checks"]["clarified_behavior_and_tests_pass"] = (
                result.get("passed") is True
            )
        report["checks"]["request_bound_respected"] = (
            len(gateway.calls) <= args.max_calls
        )
        report["checks"]["all_provider_responses_ok"] = bool(gateway.calls) and all(
            c["status"] == 200 for c in gateway.calls
        )
        report["checks"]["frozen_source_unchanged"] = all(
            digest(root / name) == sha for name, sha in report["source_sha256"].items()
        )
        report["passed"] = all(report["checks"].values())
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if any(
            secret and secret in encoded
            for secret in [settings["MEDAGENT_LLM_API_KEY"], gateway.token]
        ):
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
