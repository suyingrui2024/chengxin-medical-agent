"""Synthetic-only native UI evaluation with a bounded, secret-holding gateway.

The application receives a disposable test token. Only this parent process
reads the real provider credential; the gateway is never part of the product.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from urllib import error, request
from urllib.parse import urlsplit

from medagent.env_config import for_backend, read_env


class EvaluationGateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings, maximum, token):
        super().__init__(("127.0.0.1", 0), GatewayHandler)
        self.settings, self.maximum, self.token = settings, maximum, token
        self.upstream = urlsplit(settings["MEDAGENT_LLM_BASE_URL"])
        self.lock = threading.Lock()
        self.calls = []
        self.injected_failures = 0
        self.fail_next = False

    def metrics(self):
        with self.lock:
            return {
                "attempted_calls": len(self.calls),
                "maximum_calls": self.maximum,
                "injected_failures": self.injected_failures,
                "calls": [dict(call) for call in self.calls],
            }


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def authenticated(self):
        expected = self.server.token
        supplied = self.headers.get("x-api-key", "")
        bearer = self.headers.get("authorization", "")
        return secrets.compare_digest(
            supplied.encode(), expected.encode()
        ) or secrets.compare_digest(bearer.encode(), ("Bearer " + expected).encode())

    def respond(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def failure(self, status):
        self.respond(
            status,
            b'{"type":"error","error":{"type":"api_error","message":"Evaluation gateway unavailable"}}',
        )

    def do_GET(self):
        if not self.authenticated():
            return self.failure(401)
        if self.path != "/__evaluation/metrics":
            return self.failure(404)
        self.respond(200, json.dumps(self.server.metrics()).encode())

    def do_POST(self):
        if not self.authenticated():
            return self.failure(401)
        if self.path == "/__evaluation/fail-next":
            with self.server.lock:
                self.server.fail_next = True
            return self.respond(200, b'{"armed":true}')
        prefix = self.server.upstream.path.rstrip("/")
        if self.path not in {prefix + "/messages", prefix + "/v1/messages"}:
            return self.failure(404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 512000:
                return self.failure(413)
        except ValueError:
            return self.failure(400)
        body = self.rfile.read(size)
        with self.server.lock:
            if self.server.fail_next:
                self.server.fail_next = False
                self.server.injected_failures += 1
                return self.failure(503)
            if len(self.server.calls) >= self.server.maximum:
                return self.failure(429)
            record = {"index": len(self.server.calls) + 1, "status": None}
            self.server.calls.append(record)
        started = time.perf_counter()
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower()
            in {
                "content-type",
                "accept",
                "anthropic-version",
                "anthropic-beta",
                "x-stainless-lang",
            }
        }
        credential = self.server.settings["MEDAGENT_LLM_API_KEY"]
        if self.headers.get("authorization"):
            headers["Authorization"] = "Bearer " + credential
        if self.headers.get("x-api-key"):
            headers["x-api-key"] = credential
        endpoint = (
            f"{self.server.upstream.scheme}://{self.server.upstream.netloc}{self.path}"
        )
        try:
            with request.urlopen(
                request.Request(endpoint, body, headers), timeout=40
            ) as upstream:
                data, status, mime = (
                    upstream.read(),
                    upstream.status,
                    upstream.headers.get("Content-Type", "application/json"),
                )
            data = data.replace(credential.encode(), b"[REDACTED]")
        except error.HTTPError as exc:
            status, data, mime = (
                exc.code,
                b'{"type":"error","error":{"type":"api_error","message":"Upstream request failed"}}',
                "application/json",
            )
        except Exception:
            status, data, mime = (
                502,
                b'{"type":"error","error":{"type":"api_error","message":"Upstream request failed"}}',
                "application/json",
            )
        with self.server.lock:
            record.update(
                status=status, duration_ms=round((time.perf_counter() - started) * 1000)
            )
        self.respond(status, data, mime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-calls", type=int, default=40)
    parser.add_argument("--max-total-calls", type=int, default=140)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=[
            "native_full_interview",
            "native_emergency_gate",
            "native_service_failure_recovery",
            "native_full_location_interview",
        ],
        default=[
            "native_full_interview",
            "native_emergency_gate",
            "native_service_failure_recovery",
            "native_full_location_interview",
        ],
    )
    args = parser.parse_args()
    if len(set(args.cases)) != len(args.cases):
        raise SystemExit("Case selection must not contain duplicates")
    if args.output.exists() or not 1 <= args.max_calls <= 50:
        raise SystemExit("Use a fresh output path and a call limit between 1 and 50")
    root = Path(__file__).resolve().parents[3]
    spent = 0
    for path in args.output.parent.glob("*.json"):
        try:
            prior = json.loads(path.read_text())
            if prior.get("synthetic_only") is True:
                spent += max(0, int(prior.get("attempted_calls", 0)))
        except (ValueError, OSError):
            continue
    remaining = min(args.max_calls, args.max_total_calls - spent)
    if remaining <= 0:
        raise SystemExit("Cumulative evaluation request limit reached")
    settings = for_backend(read_env(root / ".env"), "dsh")
    token = secrets.token_hex(32)
    gateway = EvaluationGateway(settings, remaining, token)
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifacts = args.output.with_suffix("")
    with tempfile.TemporaryDirectory(prefix="medagent-live-ui-") as profile:
        environment = {
            **os.environ,
            "MEDAGENT_DESKTOP_LIVE": "1",
            "MEDAGENT_BACKEND": "dsh",
            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
            "MEDAGENT_LLM_API_KEY": token,
            "MEDAGENT_LLM_MODEL": settings["MEDAGENT_LLM_MODEL"],
            "MEDAGENT_DESKTOP_PROFILE": profile,
            "MEDAGENT_E2E_GATEWAY": f"http://127.0.0.1:{gateway.server_port}",
            "MEDAGENT_E2E_TOKEN": token,
            "MEDAGENT_E2E_REPORT": str(args.output.resolve()),
            "MEDAGENT_E2E_ARTIFACTS": str(artifacts.resolve()),
            "MEDAGENT_E2E_CASES": json.dumps(args.cases),
        }
        environment.pop("ELECTRON_RUN_AS_NODE", None)
        environment.pop("MEDAGENT_DESKTOP_BINARY", None)
        if args.binary:
            environment["MEDAGENT_DESKTOP_BINARY"] = str(args.binary.resolve())
        result = None
        try:
            result = subprocess.run(
                [
                    "node",
                    "node_modules/@playwright/test/cli.js",
                    "test",
                    "--config",
                    "playwright.live.config.mjs",
                ],
                cwd=root,
                env=environment,
            )
        finally:
            gateway.shutdown()
            gateway.server_close()
            report = (
                json.loads(args.output.read_text())
                if args.output.exists()
                else {"cases": []}
            )
            report.update(
                synthetic_only=True,
                kind="native-live-ui-evaluation/v3",
                selected_cases=args.cases,
                backend="dsh",
                model=settings["MEDAGENT_LLM_MODEL"],
                **gateway.metrics(),
            )
            report["source_sha256"] = {
                str(path.relative_to(root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted((root / "src/medagent").rglob("*"))
                if path.is_file()
                and path.suffix
                in {".py", ".js", ".json", ".txt", ".svg", ".html", ".css"}
            }
            report["test_files_sha256"] = {
                relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                for relative in (
                    "scripts/evaluation/medical/test_live_desktop.py",
                    "tests/live/workflow.spec.mjs",
                    "tests/live/location-workflow.mjs",
                    "playwright.live.config.mjs",
                )
            }
            report["passed"] = (
                result is not None
                and result.returncode == 0
                and {case["id"] for case in report["cases"]} == set(args.cases)
                and len(report["cases"]) == len(args.cases)
                and all(case["passed"] for case in report["cases"])
            )
            encoded = json.dumps(report, ensure_ascii=False, indent=2)
            if settings["MEDAGENT_LLM_API_KEY"] in encoded:
                raise RuntimeError(
                    "Credential detected; refusing to save evaluation report"
                )
            args.output.write_text(encoded + "\n")
            print(
                json.dumps(
                    {
                        "passed": report["passed"],
                        "attempted_calls": report["attempted_calls"],
                        "injected_failures": report["injected_failures"],
                    }
                ),
                flush=True,
            )
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
