"""Bounded real-model conformance: inject a synthetic edit after an actual check."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time

from scripts.benchmarks.benchmark_execution import (
    BoundedGateway,
    BenchmarkGatewayHandler,
    check_artifacts,
    product_run,
)
from scripts.evaluation.execution.evaluate_delivery_checks import fingerprints
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager, open_workspace_directory, safe_file

ROOT = Path(__file__).resolve().parents[3]
COMMAND = "python3 -m unittest tests.test_add -v"
KEY = hashlib.sha256(COMMAND.encode()).hexdigest()
GOOD = "def add(a, b):\n    return a + b\n"
BAD = "def add(a, b):\n    return a + b + 1\n"
FILES = {
    "calculator.py": GOOD,
    "tests/__init__.py": "",
    "tests/test_add.py": "import unittest\nfrom calculator import add\nclass Tests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 4)\n",
}
PROMPT = f"这是非医疗合成工程任务。不联网、不安装依赖、不委托子任务。检查整数加法模块，规范为add(a,b)=a+b；如发现实现问题，只修正calculator.py，三个已有文件之外不新增任何文件，tests目录必须逐字保留。请先通过delivery_check原样运行{COMMAND!r}，label为加法检查，required=true，watch为['calculator.py','tests']。按真实结果完成必要修复并用同命令、同范围复验，通过后简短收尾。"


def final_provider_response(body):
    try:
        return json.loads(body).get("stop_reason") == "end_turn"
    except (ValueError, AttributeError):
        pass
    for line in body.decode("utf-8").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except ValueError:
            continue
        if (
            event.get("type") == "message_delta"
            and event.get("delta", {}).get("stop_reason") == "end_turn"
        ):
            return True
    return False


class FaultHandler(BenchmarkGatewayHandler):
    def respond(self, status, body, content_type="application/json"):
        # Preserve the real provider frame. Inject only after its actual final
        # response has arrived, before the runtime receives that response.
        if status == 200 and final_provider_response(body):
            with self.server.injection_lock:
                if not self.server.injection and self.server.session:
                    proof = None
                    deadline = time.monotonic() + 2
                    while proof is None and time.monotonic() < deadline:
                        proof = next(
                            (
                                v
                                for v in reversed(
                                    list(self.server.session.observations)
                                )
                                if v.get("tool") == "delivery_check"
                                and v.get("check_key") == KEY
                                and v.get("exit_code") == 0
                                and (v.get("check_snapshot") or {}).get("complete")
                            ),
                            None,
                        )
                        if proof is None:
                            time.sleep(0.02)
                    if proof:
                        session = self.server.session
                        directory = open_workspace_directory(
                            session.directory, session.directory_identity
                        )
                        try:
                            fd = os.open(
                                "calculator.py",
                                os.O_RDWR | os.O_NOFOLLOW,
                                dir_fd=directory,
                            )
                            with os.fdopen(fd, "r+b") as stream:
                                before = stream.read(128001)
                                if len(before) > 128000:
                                    raise ValueError(
                                        "Synthetic source exceeds fixture bound"
                                    )
                                stream.seek(0)
                                stream.truncate()
                                stream.write(BAD.encode())
                                stream.flush()
                                os.fsync(stream.fileno())
                            self.server.injection = {
                                "kind": "owned-synthetic-file-edit",
                                "boundary": "actual-final-response-before-runtime",
                                "after_actual_check_call_id": proof["call_id"],
                                "before_sha256": hashlib.sha256(before).hexdigest(),
                                "after_sha256": hashlib.sha256(
                                    BAD.encode()
                                ).hexdigest(),
                                "upstream_requests_before_forward": len(
                                    self.server.calls
                                ),
                                "final_response_sha256": hashlib.sha256(
                                    body
                                ).hexdigest(),
                            }
                        finally:
                            os.close(directory)
        super().respond(status, body, content_type)


def run_case(settings, output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    gateway = BoundedGateway(settings, 12, output / "requests.json")
    gateway.injection, gateway.session = None, None
    gateway.injection_lock = threading.Lock()
    gateway.RequestHandlerClass = FaultHandler
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=output / "workspaces")
    result = {
        "kind": "synthetic-post-check-edit/v2",
        "prompt": PROMPT,
        "initial_files": FILES,
        "checks": {},
    }
    session = directory = None
    try:
        session = manager.create(config)
        directory = session.directory
        for path, text in FILES.items():
            file = directory / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text)
        gateway.session = session
        snapshot = product_run(session, PROMPT, 12)
        values = [v for v in session.observations if v.get("tool") == "delivery_check"]
        freshness = [e for e in snapshot["events"] if e["type"] == "delivery.freshness"]
        result.update(
            snapshot=snapshot,
            observations=values,
            injection=gateway.injection,
            freshness=freshness,
        )
        result["checks"].update(
            task_completed=snapshot["status"] == "completed",
            actual_post_check_injection=gateway.injection is not None,
            stale_result_detected=any(e["freshness"] == "stale" for e in freshness),
            one_bounded_repair=sum(
                e["type"] == "turn.check_repairing" for e in snapshot["events"]
            )
            == 1,
            final_file_version_current=bool(freshness)
            and freshness[-1]["freshness"] == "current",
            same_command_actually_retested=len(values) >= 2
            and all(v.get("check_key") == KEY for v in values)
            and values[-1]["exit_code"] == 0,
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
    if directory is not None:
        result["checks"]["tests_preserved"] = all(
            safe_file(directory, path)["text"] == text
            for path, text in FILES.items()
            if path.startswith("tests/")
        )
        probe = """import sys,unittest
from pathlib import Path
sys.path.insert(0,'/workspace')
from calculator import add
for a,b in [(0,0),(-7,3),(12,-40),(10**100, 10**99),(-11,-19)]:
    assert add(a,b)==a+b
suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_add')
assert suite.countTestCases()==1
assert unittest.TextTestRunner().run(suite).wasSuccessful()
assert {str(p.relative_to('/workspace')) for p in Path('/workspace').rglob('*') if p.is_file() and '__pycache__' not in p.parts}=={'calculator.py','tests/__init__.py','tests/test_add.py'}
print('independent arithmetic and file contract passed')
"""
        result["independent_verification"] = check_artifacts(
            directory, output / "verification-state", probe
        )
        result["checks"]["independent_artifact_contract"] = result[
            "independent_verification"
        ]["passed"]
        restored_manager = ExecutionManager(root=output / "workspaces")
        try:
            restored = restored_manager.open(session.id, {})
            result["checks"]["offline_history_matches"] = (
                list(restored.observations) == list(session.observations)
                and restored.process is None
            )
        finally:
            restored_manager.close()
    result["checks"]["bounded_requests"] = len(gateway.calls) <= 12
    result["checks"]["all_provider_http200"] = bool(gateway.calls) and all(
        v["status"] == 200 for v in gateway.calls
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
                    "maximum_external_calls": 12,
                    "synthetic_fault": "calculator.py changes after an actual successful check",
                    "external_model_requests": 0,
                }
            )
        )
        return
    output = args.output.resolve()
    if output.exists() or output.with_suffix("").exists():
        raise SystemExit("Fresh output path required")
    output.parent.mkdir(parents=True, exist_ok=True)
    frozen = fingerprints()
    source = output.with_suffix("") / "source"
    for name in frozen:
        destination = source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    report = {"version": __version__, "source_sha256": frozen, "case": None}
    try:
        report["case"] = run_case(
            for_backend(read_env(ROOT / ".env"), "dsh"), output.with_suffix("") / "case"
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
