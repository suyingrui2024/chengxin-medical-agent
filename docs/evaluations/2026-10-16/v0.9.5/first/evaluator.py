"""Bounded real-provider native patient confirmation evaluation; synthetic only."""
import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
import subprocess
import threading

from benchmark_execution import BoundedGateway, sanitized_environment
from medagent.env_config import for_backend, read_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-calls", type=int, default=24)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    artifacts = output.with_suffix("")
    if (
        output.exists()
        or artifacts.exists()
        or output.suffix != ".json"
        or not 12 <= args.max_calls <= 32
    ):
        raise SystemExit("Use a fresh JSON output and 12–32 request bound")
    prior = sum(
        json.loads(path.read_text()).get("attempted_calls", 0)
        for path in output.parent.rglob("requests.json")
    )
    if prior + args.max_calls > 64:
        raise SystemExit("Cumulative patient calculation evaluation cap (64) exhausted")
    root = Path(__file__).resolve().parents[1]
    binary = args.binary.resolve() if args.binary else None
    if binary and not binary.is_file():
        raise SystemExit("Desktop binary does not exist")
    settings = for_backend(read_env(root / ".env"), "dsh")
    artifacts.mkdir(parents=True)
    gateway = BoundedGateway(settings, args.max_calls, artifacts / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    report = {
        "kind": "real-native-patient-calculation-evaluation/v1",
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "maximum_calls": args.max_calls,
        "binary": str(binary) if binary else "source",
        "checks": {},
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    report["source_sha256"] = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for base in [root / "src/medagent", root / "integrations/dsh"]
        for path in base.rglob("*")
        if path.is_file()
        and path.suffix
        in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg", ".txt"}
    }
    for name, expected in report["source_sha256"].items():
        data = (root / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        target = artifacts / "snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    fixture = root / "tests/live/patient-calculation.mjs"
    report["native_driver_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
    (artifacts / "native-driver.mjs").write_bytes(fixture.read_bytes())
    env = {
        **sanitized_environment(),
        **{
            key: os.environ[key]
            for key in ["DISPLAY", "XAUTHORITY"]
            if key in os.environ
        },
        "MEDAGENT_DESKTOP_PROFILE": str(artifacts / "profile"),
        "MEDAGENT_EXECUTION_ROOT": str(artifacts / "workspaces"),
        "MEDAGENT_DESKTOP_LIVE": "1",
        "MEDAGENT_BACKEND": "dsh",
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_MODEL": settings["MEDAGENT_LLM_MODEL"],
        "MEDAGENT_LLM_API_KEY": gateway.token,
        "MEDAGENT_PATIENT_EVAL_OUTPUT": str(artifacts / "native.json"),
    }
    if binary:
        env["MEDAGENT_DESKTOP_BINARY"] = str(binary)
    try:
        child = subprocess.Popen(
            ["node", str(fixture)],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            child.wait(timeout=360)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            raise
        report["native"] = json.loads((artifacts / "native.json").read_text())
        report["checks"].update(report["native"]["checks"])
        report["checks"]["native_driver_exited_successfully"] = child.returncode == 0
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["checks"]["all_steps_finished"] = False
    finally:
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        report["gateway"] = gateway.metrics()
        report["attempted_calls"] = len(gateway.calls)
        report["checks"]["within_bound"] = len(gateway.calls) <= args.max_calls
        report["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
            call["status"] == 200 for call in gateway.calls
        )
        report["passed"] = all(report["checks"].values())
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Credential found; refusing report")
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
