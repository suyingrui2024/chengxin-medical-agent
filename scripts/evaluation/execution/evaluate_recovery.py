"""Induce a genuine provider output limit once, then verify bounded recovery."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import hashlib
import io
import json
from pathlib import Path
import threading

from scripts.benchmarks.benchmark_execution import (
    BoundedGateway,
    BenchmarkGatewayHandler,
    check_artifacts,
    product_run,
)
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager, safe_file


ARTIFACT_PROBE = """import json,subprocess,sys
from pathlib import Path
assert Path('/workspace/calc.py').is_file()
Path('/workspace/result.json').unlink(missing_ok=True)
result=subprocess.run([sys.executable,'/workspace/calc.py'],cwd='/workspace',capture_output=True,text=True,timeout=5)
assert result.returncode==0,result.stderr
assert json.loads(result.stdout)=={'result':'1250'},result.stdout
assert json.load(open('/workspace/result.json'))=={'result':'1250'}
print('independent execution regenerated and verified requested result')
"""


class FirstOutputLimitHandler(BenchmarkGatewayHandler):
    """Change only the first upstream request limit; never fabricate a response."""

    def do_POST(self):
        prefix = self.server.upstream.path.rstrip("/")
        if not self.authenticated() or self.path not in {
            prefix + "/messages",
            prefix + "/v1/messages",
        }:
            return super().do_POST()
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 512000:
                return self.failure(413)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                return self.failure(400)
        except (ValueError, TypeError):
            return self.failure(400)
        with self.server.lock:
            inject = not self.server.limit_applied
            if inject:
                self.server.limit_applied = True
        if inject:
            payload["max_tokens"] = self.server.first_output_limit
        encoded = json.dumps(payload).encode()
        original = self.rfile
        self.rfile = io.BytesIO(encoded)
        self.headers.replace_header("Content-Length", str(len(encoded)))
        try:
            super().do_POST()
        finally:
            self.rfile.close()
            self.rfile = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=8)
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        output.suffix != ".json"
        or output.exists()
        or output.with_suffix("").exists()
        or not 3 <= args.max_calls <= 12
    ):
        raise SystemExit("Use a fresh .json report path and a 3–12 call limit")
    root = Path(__file__).resolve().parents[3]
    settings = for_backend(read_env(root / ".env"), "dsh")
    directory = output.with_suffix("")
    directory.mkdir(parents=True)
    spent = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if spent + args.max_calls > 24:
        raise SystemExit("Recovery evaluation directory budget exhausted (24)")
    gateway = BoundedGateway(settings, args.max_calls, directory / "requests.json")
    gateway.limit_applied, gateway.first_output_limit = False, 32
    gateway.RequestHandlerClass = FirstOutputLimitHandler
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = ExecutionManager(root=directory / "workspaces", source_root=root)
    session = None
    report = {
        "kind": "real-provider-truncation-recovery/v1",
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_calls": args.max_calls,
        "first_upstream_max_tokens": 32,
        "response_rewritten": False,
        "checks": {},
        "source_sha256": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for base in [root / "src/medagent", root / "integrations/dsh"]
            for p in base.rglob("*")
            if p.is_file()
            and p.suffix
            in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
        },
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    try:
        session = manager.create(config)
        report["snapshot"] = product_run(
            session,
            'RECOVERY_REAL_PROVIDER_SYNTHETIC：这是合成工程验收。请实际创建 calc.py，使用 Decimal 把1.25乘1000得到1250；运行这个脚本，将程序实际得到的 {"result":"1250"} 保存为 result.json，读取核对后简短结束。只需这两个文件，不要子任务、联网或额外报告。',
            args.max_calls,
        )
        events = report["snapshot"]["events"]
        reasons = [
            e.get("notification", {})
            .get("params", {})
            .get("event", {})
            .get("data", {})
            .get("reason", {})
            .get("kind")
            for e in events
        ]
        report["checks"]["provider_actually_truncated"] = "max-tokens" in reasons
        report["checks"]["one_recovery"] = (
            sum(e["type"] == "turn.recovering" for e in events) == 1
        )
        report["checks"]["task_completed"] = report["snapshot"]["status"] == "completed"
        try:
            report["checks"]["actual_artifact"] = json.loads(
                safe_file(session.directory, "result.json")["text"]
            ) == {"result": "1250"}
        except (ValueError, OSError):
            report["checks"]["actual_artifact"] = False
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["checks"]["task_completed"] = False
    finally:
        manager.close()
        if session is not None:
            try:
                report["independent_verification"] = check_artifacts(
                    session.directory, session.state_directory, ARTIFACT_PROBE
                )
            except Exception as exc:
                report["independent_verification"] = {
                    "passed": False,
                    "error_type": type(exc).__name__,
                }
            report["checks"]["independent_code_execution"] = report[
                "independent_verification"
            ]["passed"]
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        report["gateway"] = gateway.metrics()
        report["attempted_calls"] = len(gateway.calls)
        report["checks"]["within_original_budget"] = (
            len(gateway.calls) <= args.max_calls
        )
        report["checks"]["all_upstream_success"] = bool(gateway.calls) and all(
            c["status"] == 200 for c in gateway.calls
        )
        report["passed"] = all(report["checks"].values())
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Credential detected, refusing report")
        output.write_text(encoded + "\n")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report["checks"],
                "calls": report["attempted_calls"],
            },
            ensure_ascii=False,
        )
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
