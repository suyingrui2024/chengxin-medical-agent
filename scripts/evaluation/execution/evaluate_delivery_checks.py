"""Bounded synthetic conformance checks for real-model delivery-check use."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import ast
import hashlib
import json
from pathlib import Path
import threading

from scripts.benchmarks.benchmark_execution import BoundedGateway, check_artifacts, product_run
from medagent import __version__
from medagent.env_config import read_env, for_backend
from medagent.execution import ExecutionManager, safe_file

ROOT = Path(__file__).resolve().parents[3]
CASES = ["arithmetic-test", "versioned-api-test", "inspection-only"]
CALLS = 12


def fixture(name):
    if name == "versioned-api-test":
        implementation = "def signature(value, *, show_annotations=False):\n    return str(value) + (':int' if show_annotations else '')\n"
        test = "import unittest\nfrom project_api import signature\nclass NewTests(unittest.TestCase):\n    def test_signature(self):\n        self.assertEqual(signature(3, typehints=True), '3:int')\n"
        correct = test.replace("typehints=True", "show_annotations=True")
        implementation_path = "project_api.py"
        contract = "按当前 project_api.py 的真实函数签名，修正新增测试调用的错误参数名；不要修改实现以迁就测试。"
    else:
        implementation = "def add(a, b):\n    return a + b\n"
        test = "import unittest\nfrom calculator import add\nclass NewTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 2), 5)\n"
        correct = test.replace("add(2, 2), 5", "add(2, 2), 4")
        implementation_path = "calculator.py"
        contract = (
            "整数加法规范为 add(a,b)=a+b；只修正 test_add 的错误预期，保留对 add(2,2) 的 assertEqual 检查。"
        )
    command = "python3 -m unittest tests.test_new -v"
    files = {
        implementation_path: implementation,
        "tests/__init__.py": "",
        "tests/test_new.py": test,
    }
    if name == "inspection-only":
        prompt = f"这是非医疗合成工程验收，只检查并报告，不修改或新增任何文件，也不修复测试。不联网、不安装依赖。请调用 delivery_check，command 为 {command!r}，label 为 仅检查，required=false。按实际退出和输出报告结果；不要把检查失败说成通过。"
        expected = test
    else:
        prompt = f"这是非医疗合成工程验收，只操作当前工作区，不联网、不安装依赖。{contract}\n必须先用 delivery_check 原样运行 {command!r}，label 为 新增测试，required=true，取得实际失败后再修正；然后用完全相同的 delivery_check 命令复验。只允许改 tests/test_new.py 中的错误预期或参数名，不增删类、方法、导入、断言，不新增文件；{implementation_path} 和 tests/__init__.py 必须逐字保留。最终简短报告真实结果；无法完成就明确说明。"
        expected = correct
    return {
        "files": files,
        "prompt": prompt,
        "command": command,
        "implementation_path": implementation_path,
        "expected_ast": ast.dump(ast.parse(expected), include_attributes=False),
        "inspection_only": name == "inspection-only",
    }


def fingerprints():
    files = [
        ROOT / name for name in ["package.json", "package-lock.json", "pyproject.toml"]
    ]
    for name in ["src/medagent", "integrations/dsh", "desktop", "scripts"]:
        files.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".mjs", ".js", ".json", ".yml", ".css", ".html", ".svg"}
        )
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(files))
    }


def run_case(name, settings, output):
    case = fixture(name)
    output = Path(output).resolve()
    output.mkdir(parents=True)
    gateway = BoundedGateway(settings, CALLS, output / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=output / "workspaces")
    result = {
        "case": name,
        "checks": {},
        "prompt": case["prompt"],
        "files": case["files"],
    }
    session = directory = state = None
    try:
        session = manager.create(config)
        directory, state = session.directory, session.state_directory
        for path, value in case["files"].items():
            target = directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value)
        snapshot = product_run(session, case["prompt"], CALLS)
        result["snapshot"] = snapshot
        observations = [
            value for value in session.observations if value["tool"] == "delivery_check"
        ]
        result["observations"] = observations
        result["checks"]["task_completed"] = snapshot["status"] == "completed"
        result["checks"]["actual_check_observed"] = bool(observations)
        expected_key = hashlib.sha256(case["command"].encode()).hexdigest()
        result["checks"]["requested_command_used"] = bool(observations) and all(
            value.get("check_key") == expected_key for value in observations
        )
        if case["inspection_only"]:
            result["checks"].update(
                {
                    "failure_retained": bool(observations)
                    and observations[-1]["exit_code"] == 1,
                    "inspection_not_a_required_gate": bool(observations)
                    and all(
                        value.get("check_required") is False for value in observations
                    ),
                    "no_automatic_repair": not any(
                        event["type"] == "turn.check_repairing"
                        for event in snapshot["events"]
                    ),
                }
            )
        else:
            result["checks"].update(
                {
                    "initial_failure_observed": bool(observations)
                    and observations[0]["exit_code"] == 1,
                    "same_check_retested_successfully": len(observations) >= 2
                    and observations[-1]["exit_code"] == 0,
                    "acceptance_checks_remain_required": bool(observations)
                    and all(
                        value.get("check_required") is True for value in observations
                    ),
                }
            )
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["diagnostic"] = (
            str(error)
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
            .replace(gateway.token, "[REDACTED]")[:1000]
        )
        result["checks"]["scenario_finished"] = False
    finally:
        manager.close()
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        result["gateway"] = gateway.metrics()
    if directory is not None:
        try:
            preserved = (
                case["files"]
                if case["inspection_only"]
                else {
                    path: value
                    for path, value in case["files"].items()
                    if path != "tests/test_new.py"
                }
            )
            result["checks"]["protected_files_preserved"] = all(
                safe_file(directory, path)["text"] == value
                for path, value in preserved.items()
            )
            probe = r"""import ast,json,sys,unittest
from pathlib import Path
config=json.loads(CONFIG)
sys.path.insert(0,'/workspace')
test=Path('/workspace/tests/test_new.py').read_text()
assert ast.dump(ast.parse(test),include_attributes=False)==config['expected_ast'],'test structure or expected value differs from the explicit minimal-edit contract'
files={str(p.relative_to('/workspace')) for p in Path('/workspace').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}}
assert files==set(config['files']),'unrequested new or missing files'
suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_new')
assert suite.countTestCases()==1,'missing or extra test cases'
result=unittest.TextTestRunner().run(suite)
assert result.wasSuccessful() is (not config['inspection_only']),'test outcome differs from requested scope'
print('independent delivery conformance check completed')
""".replace(
                "CONFIG",
                repr(
                    json.dumps(
                        {
                            "expected_ast": case["expected_ast"],
                            "files": list(case["files"]),
                            "inspection_only": case["inspection_only"],
                        }
                    )
                ),
            )
            verification = check_artifacts(
                directory, output / "verification-state", probe
            )
            result["independent_verification"] = verification
            result["checks"]["independent_artifact_contract"] = verification["passed"]
            restored_manager = ExecutionManager(root=output / "workspaces")
            try:
                restored = restored_manager.open(session.id, {})
                result["checks"]["offline_check_history_matches"] = [
                    value
                    for value in restored.observations
                    if value["tool"] == "delivery_check"
                ] == result.get("observations", []) and restored.process is None
            finally:
                restored_manager.close()
        except Exception as error:
            result["verification_error"] = {
                "type": type(error).__name__,
                "diagnostic": str(error)[:1000],
            }
            result["checks"]["independent_artifact_contract"] = False
    result["checks"]["bounded_requests"] = len(gateway.calls) <= CALLS
    result["checks"]["all_provider_http200"] = bool(gateway.calls) and all(
        call["status"] == 200 for call in gateway.calls
    )
    result["passed"] = bool(result["checks"]) and all(result["checks"].values())
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
                    "cases": CASES,
                    "maximum_calls_per_case": CALLS,
                    "maximum_plan_calls": CALLS * len(CASES),
                    "maximum_cumulative_directory_calls": 72,
                    "external_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if output.suffix != ".json" or output.exists() or artifacts.exists():
        raise SystemExit("Fresh JSON output required")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + len(CASES) * CALLS > 72:
        raise SystemExit("Cumulative delivery evaluation budget exhausted")
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    frozen = fingerprints()
    for name, expected in frozen.items():
        data = (ROOT / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Source drift during freeze")
        target = artifacts / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    report = {
        "kind": "delivery-check-live-conformance/v1",
        "runtime_version": __version__,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_requests_per_case": CALLS,
        "maximum_plan_requests": CALLS * len(CASES),
        "prior_directory_calls": prior,
        "source_sha256": frozen,
        "scope": "Explicit workflow and minimal-edit synthetic conformance; no held-out coding accuracy, cost-efficiency superiority or clinical validation claim. Automatic premature-finish repair is separately tested with deterministic local responses.",
        "runs": [],
    }

    def save():
        report["source_unchanged"] = fingerprints() == frozen
        report["attempted_calls"] = sum(
            row["gateway"]["attempted_calls"] for row in report["runs"]
        )
        report["passed"] = (
            len(report["runs"]) == len(CASES)
            and all(row["passed"] for row in report["runs"])
            and report["source_unchanged"]
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise ValueError("Credential detected; refusing report")
        output.write_text(encoded + "\n")

    save()
    try:
        for name in CASES:
            if fingerprints() != frozen:
                raise ValueError("Frozen execution source changed")
            result = run_case(name, settings, artifacts / name)
            report["runs"].append(result)
            save()
            print(
                json.dumps(
                    {
                        "case": name,
                        "passed": result["passed"],
                        "calls": result["gateway"]["attempted_calls"],
                    }
                ),
                flush=True,
            )
    finally:
        save()
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
