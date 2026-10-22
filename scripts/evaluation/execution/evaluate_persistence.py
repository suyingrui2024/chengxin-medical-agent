"""Bounded real-provider cold resume, with a first-request context witness."""

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))


import argparse
import hashlib
import io
import json
from pathlib import Path
import secrets
import threading

from scripts.benchmarks.benchmark_execution import (
    BoundedGateway,
    BenchmarkGatewayHandler,
    product_run,
    check_artifacts,
)
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager, safe_file


class ContextWitness(BenchmarkGatewayHandler):
    def do_POST(self):
        if not self.authenticated():
            return super().do_POST()
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 512000:
            return self.failure(413)
        raw = self.rfile.read(size)
        try:
            body = json.loads(raw)
        except ValueError:
            return self.failure(400)
        with self.server.lock:
            if (
                self.server.phase == "resume"
                and self.server.first_resume_context is None
            ):
                self.server.first_resume_context = self.server.marker in json.dumps(
                    body.get("messages", []), ensure_ascii=False
                )
        original = self.rfile
        self.rfile = io.BytesIO(raw)
        try:
            super().do_POST()
        finally:
            self.rfile.close()
            self.rfile = original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=20)
    args = parser.parse_args()
    output = args.output.resolve()
    if (
        output.suffix != ".json"
        or output.exists()
        or output.with_suffix("").exists()
        or not 8 <= args.max_calls <= 24
    ):
        raise SystemExit("Use a fresh .json path and an 8–24 call bound")
    prior = sum(
        json.loads(p.read_text()).get("attempted_calls", 0)
        for p in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 48:
        raise SystemExit("Cumulative persistence evaluation cap (48) exhausted")
    root = Path(__file__).resolve().parents[3]
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts = output.with_suffix("")
    artifacts.mkdir(parents=True)
    gateway = BoundedGateway(settings, args.max_calls, artifacts / "requests.json")
    gateway.phase = "initial"
    gateway.marker = "project-" + secrets.token_hex(8)
    gateway.first_resume_context = None
    gateway.RequestHandlerClass = ContextWitness
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    first = ExecutionManager(root=artifacts / "workspaces", source_root=root)
    second = None
    report = {
        "kind": "real-provider-cold-resume/v1",
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_calls": args.max_calls,
        "checks": {},
        "snapshots": [],
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    report["source_sha256"] = {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for base in [root / "src/medagent", root / "integrations/dsh"]
        for p in base.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
    }
    frozen = artifacts / "snapshot"
    for name, expected in report["source_sha256"].items():
        data = (root / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    session = None
    try:
        session = first.create(config)
        snapshot = product_run(
            session,
            "这是合成工程验收。项目代号是 "
            + gateway.marker
            + '，只记在会话中，不要写入文件。请创建独立可运行的 calc.py，使用 Decimal 计算1.25×1000，脚本自身运行时写出 result.json，内容为 {"result":"1250"}。实际运行并核对；只需这两个文件。最终简短说明并复述代号，不要子任务和联网。',
            min(12, args.max_calls - 4),
        )
        report["snapshots"].append(snapshot)
        report["checks"]["initial_completed"] = snapshot["status"] == "completed"
        original_process = session.process
        identity = session.id
        first.close()
        report["checks"]["old_process_exited"] = (
            original_process is not None and original_process.poll() is not None
        )
        second = ExecutionManager(root=artifacts / "workspaces", source_root=root)
        before = len(gateway.calls)
        restored = second.open(identity, {})
        report["checks"]["offline_restored_without_model_call"] = (
            restored.process is None
            and len(gateway.calls) == before
            and not restored.execution_configured
        )
        report["checks"]["same_workspace"] = (
            restored.directory == session.directory and restored.id == identity
        )
        report["checks"]["original_artifact_retained"] = json.loads(
            safe_file(restored.directory, "result.json")["text"]
        ) == {"result": "1250"}
        previous_token = gateway.token
        gateway.token = secrets.token_hex(32)
        config["MEDAGENT_LLM_API_KEY"] = gateway.token
        second.open(identity, config)
        gateway.phase = "resume"
        gateway.begin_turn(min(12, args.max_calls - len(gateway.calls)))
        continuation = product_run(
            restored,
            "继续原项目：使用刚才会话里已有的项目代号，不要新编代号。实际再运行 calc.py 核对结果，然后保存 continuation.json，字段 project_marker 为原代号、result 为实际字符串结果1250。读取核对后简短结束，不要额外报告。",
            min(12, args.max_calls - len(gateway.calls)),
        )
        report["snapshots"].append(continuation)
        report["checks"]["first_resumed_request_contains_original_conversation"] = (
            gateway.first_resume_context is True
        )
        report["checks"]["continued_completed"] = continuation["status"] == "completed"
        report["checks"]["new_runtime_process"] = (
            restored.process is not None
            and restored.process.pid != original_process.pid
        )
        report["checks"]["continuation_artifact"] = json.loads(
            safe_file(restored.directory, "continuation.json")["text"]
        ) == {"project_marker": gateway.marker, "result": "1250"}
        index = b"".join(
            p.read_bytes() for p in (artifacts / "workspaces/.index").glob("*.json")
        )
        report["checks"]["credentials_not_persisted"] = (
            previous_token.encode() not in index
            and gateway.token.encode() not in index
            and settings["MEDAGENT_LLM_API_KEY"].encode() not in index
        )
        session = restored
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["checks"]["all_steps_finished"] = False
    finally:
        first.close()
        if second:
            second.close()
        if session is not None:
            try:
                report["independent_verification"] = check_artifacts(
                    session.directory,
                    session.state_directory,
                    "import json,subprocess,sys\nfrom pathlib import Path\nPath('/workspace/result.json').unlink(missing_ok=True)\np=subprocess.run([sys.executable,'/workspace/calc.py'],cwd='/workspace',capture_output=True,text=True,timeout=5)\nassert p.returncode==0,p.stderr\nassert json.load(open('/workspace/result.json'))=={'result':'1250'}\nprint('independent execution rebuilt the requested output')\n",
                )
                report["checks"]["independent_code_execution"] = report[
                    "independent_verification"
                ]["passed"]
            except Exception as exc:
                report["checks"]["independent_code_execution"] = False
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        report["gateway"] = gateway.metrics()
        report["attempted_calls"] = len(gateway.calls)
        report["checks"]["within_bound"] = len(gateway.calls) <= args.max_calls
        report["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
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
        ),
        flush=True,
    )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
