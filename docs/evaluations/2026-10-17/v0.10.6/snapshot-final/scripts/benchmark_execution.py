"""Paired synthetic comparison: product execution vs independently driven official SDK.

The suite is frozen before running either arm. Hidden checks execute only after
the candidate runtime has closed, inside a fresh filesystem namespace.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import random
import secrets
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib import error, request
from uuid import uuid4

from execution_benchmark_cases import CASES
from test_live_desktop import EvaluationGateway, GatewayHandler
from medagent.env_config import for_backend, read_env
from medagent.execution import ExecutionManager, safe_file

ROOT = Path(__file__).resolve().parents[1]
KIND = "paired-execution-benchmark/v1"
RUNNING = {"starting", "running", "cancelling"}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sanitized_environment():
    env = {
        key: os.environ[key] for key in ("PATH", "LANG", "TMPDIR") if key in os.environ
    }
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        value = os.environ.get(key, "")
        parsed = urlsplit(value)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            env[key] = value
    env["NO_PROXY"] = "127.0.0.1,localhost,::1"
    return env


class BoundedGateway(EvaluationGateway):
    """Same external call accounting for both arms, journaled before forwarding."""

    def __init__(self, settings, maximum, journal, capture_tools=False):
        # The base gateway reserves a call under this lock before contacting API.
        super().__init__(settings, maximum, secrets.token_hex(32))
        self.total_maximum = maximum
        self.journal = journal
        self.capture_tools = capture_tools
        self.persist_lock = threading.Lock()
        self.RequestHandlerClass = BenchmarkGatewayHandler

    def begin_turn(self, maximum):
        with self.lock:
            self.maximum = min(self.total_maximum, len(self.calls) + maximum)

    def persist(self):
        with self.persist_lock:
            record = self.metrics()
            temporary = self.journal.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, indent=2) + "\n")
            temporary.replace(self.journal)


class BenchmarkGatewayHandler(GatewayHandler):
    def do_POST(self):
        if not self.authenticated():
            return self.failure(401)
        prefix = self.server.upstream.path.rstrip("/")
        if self.path not in {prefix + "/messages", prefix + "/v1/messages"}:
            return self.failure(404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 512000:
                return self.failure(413)
            body = self.rfile.read(size)
            payload = json.loads(body)
            if (
                type(payload.get("max_tokens")) is not int
                or not 1 <= payload["max_tokens"] <= 8192
            ):
                return self.failure(400)
        except (ValueError, TypeError, AttributeError):
            return self.failure(400)
        with self.server.lock:
            if len(self.server.calls) >= self.server.maximum:
                return self.failure(429)
            record = {"index": len(self.server.calls) + 1, "status": None}
            if self.server.capture_tools:
                record["tool_names"] = sorted(
                    {
                        tool["name"]
                        for tool in payload.get("tools", [])
                        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                    }
                )
            self.server.calls.append(record)
        self.server.persist()  # A crash cannot silently forget an upstream attempt.
        started = time.perf_counter()
        credential = self.server.settings["MEDAGENT_LLM_API_KEY"]
        headers = {
            k: v
            for k, v in self.headers.items()
            if k.lower()
            in {
                "content-type",
                "accept",
                "anthropic-version",
                "anthropic-beta",
                "x-stainless-lang",
            }
        }
        if self.headers.get("authorization"):
            headers["Authorization"] = "Bearer " + credential
        if self.headers.get("x-api-key"):
            headers["x-api-key"] = credential
        endpoint = (
            f"{self.server.upstream.scheme}://{self.server.upstream.netloc}{self.path}"
        )

        class NoRedirect(request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        try:
            with request.build_opener(NoRedirect).open(
                request.Request(endpoint, body, headers), timeout=60
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
                b'{"type":"error","error":{"type":"api_error","message":"Upstream failed"}}',
                "application/json",
            )
        except Exception:
            status, data, mime = (
                502,
                b'{"type":"error","error":{"type":"api_error","message":"Upstream failed"}}',
                "application/json",
            )
        with self.server.lock:
            record.update(
                status=status, duration_ms=round((time.perf_counter() - started) * 1000)
            )
        self.server.persist()
        try:
            self.respond(status, data, mime)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.server.persist()


class Baseline:
    def __init__(
        self, directory, state, settings, *, entry_point=None, time_budget_seconds=180
    ):
        if type(time_budget_seconds) is not int or not 1 <= time_budget_seconds <= 1800:
            raise ValueError("Invalid baseline time budget")
        self.time_budget_seconds = time_budget_seconds
        directory, state = directory.resolve(), state.resolve()
        self.directory, self.state_directory = directory, state
        self.process = subprocess.Popen(
            ["node", str(entry_point or ROOT / "scripts/execution_baseline.mjs")],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=sanitized_environment(),
        )
        self.records = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        self.send(
            {
                "method": "configure",
                "id": str(uuid4()),
                "workspace": str(directory),
                "state": str(state),
                "endpoint": settings["MEDAGENT_LLM_BASE_URL"],
                "token": settings["MEDAGENT_LLM_API_KEY"],
                "model": settings["MEDAGENT_LLM_MODEL"],
                **({"time_budget_seconds": time_budget_seconds} if entry_point else {}),
            }
        )

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 4_000_000:
                    self.records.put({"type": "error", "error_type": "OversizedEvent"})
                    break
                self.records.put(json.loads(line))
        finally:
            self.records.put({"type": "error", "error_type": "ProcessExited"})

    def send(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def receive(self, expected, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.records.get(timeout=max(0.01, deadline - time.monotonic()))
            if record["type"] == expected:
                return record
            if record["type"] == "error":
                raise RuntimeError(record.get("diagnostic", record["error_type"]))
        raise TimeoutError("Baseline deadline exceeded")

    def run(self, prompt, _maximum):
        self.send({"method": "run", "text": prompt})
        return self.receive("result", self.time_budget_seconds + 10)

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
        self.process.stdout.close()


def seed_project(directory, case):
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in case["files"].items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    # Identical bytes in every copy; no arm/order hint is available to candidate.
    (directory / "fixture_identity.json").write_text(
        '{"fixture":"frozen-synthetic-v1"}\n'
    )


def check_artifacts(directory, state, probe):
    state.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["node", str(ROOT / "integrations/dsh/check-artifacts.mjs")],
        input=json.dumps(
            {"workspace": str(directory), "state": str(state), "code": probe}
        ),
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        return json.loads(result.stdout)
    except ValueError:
        return {
            "passed": False,
            "exit_code": result.returncode,
            "error": "Verifier did not return JSON",
            "stderr": result.stderr[-4000:],
        }


def product_run(session, prompt, maximum):
    session.start_turn(prompt, maximum)
    deadline = time.monotonic() + 190
    while time.monotonic() < deadline:
        snapshot = session.snapshot()
        if snapshot["status"] not in RUNNING:
            return snapshot
        time.sleep(0.1)
    session.cancel()
    raise TimeoutError("Product deadline exceeded")


def child_started(snapshots, arm):
    if arm == "product":
        return any(
            event.get("notification", {}).get("method") == "subagent.started"
            for snapshot in snapshots
            for event in snapshot.get("events", [])
        )
    return any(
        event.get("method") == "subagent.started"
        for snapshot in snapshots
        for event in snapshot.get("notifications", [])
    )


def checked_file(directory, name):
    try:
        return safe_file(directory, name)["text"]
    except (ValueError, OSError):
        return None


def evaluate_arm(arm, case, settings, output, allowance):
    output.mkdir(parents=True)
    gateway = BoundedGateway(settings, allowance, output / "requests.json")
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    config = {
        **settings,
        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{gateway.server_port}{gateway.upstream.path}",
        "MEDAGENT_LLM_API_KEY": gateway.token,
    }
    manager = runner = None
    record = {"arm": arm, "snapshots": [], "checks": {}, "maximum_calls": allowance}
    started = time.perf_counter()
    try:
        if arm == "product":
            manager = ExecutionManager(root=output / "workspaces", source_root=ROOT)
            runner = manager.create(config)
            directory, state = runner.directory, runner.state_directory
        else:
            directory, state = output / "workspace", output / "state"
            directory.mkdir(mode=0o700)
            state.mkdir(mode=0o700)
        seed_project(directory, case)
        if arm == "official-sdk":
            runner = Baseline(directory, state, config)
            runner.receive("ready", 30)
        initial_process = None
        for prompt, maximum in [(case["prompt"], 24), (case["followup"], 8)]:
            gateway.begin_turn(maximum)
            result = (
                product_run(runner, prompt, maximum)
                if arm == "product"
                else runner.run(prompt, maximum)
            )
            record["snapshots"].append(result)
            if initial_process is None:
                initial_process = runner.process
            elif runner.process is not initial_process:
                raise RuntimeError("Runtime process was replaced between turns")
        record["checks"]["completed_both_turns"] = len(
            record["snapshots"]
        ) == 2 and all(s["status"] == "completed" for s in record["snapshots"])
        record["checks"]["same_process_continued"] = runner.process is initial_process
    except Exception as exc:
        record["error_type"] = type(exc).__name__
        record["diagnostic"] = (
            str(exc)
            .replace(settings["MEDAGENT_LLM_API_KEY"], "[REDACTED]")
            .replace(gateway.token, "[REDACTED]")[:2000]
        )
        record["checks"]["completed_both_turns"] = False
    finally:
        if manager:
            manager.close()
        elif runner:
            runner.close()
        # No candidate process may race or inspect the independent verifier.
        record["execution_duration_ms"] = round((time.perf_counter() - started) * 1000)
        gateway.shutdown()
        gateway.server_close()
        gateway.persist()
        record["gateway"] = gateway.metrics()
    record["checks"]["request_budget_respected"] = len(gateway.calls) <= allowance
    record["checks"]["no_provider_errors"] = bool(gateway.calls) and all(
        c["status"] == 200 for c in gateway.calls
    )
    record["checks"]["original_tests_preserved"] = all(
        checked_file(directory, name) == value
        for name, value in case["files"].items()
        if name.startswith("tests/")
    )
    record["checks"]["fixture_preserved"] = (
        checked_file(directory, "fixture_identity.json")
        == '{"fixture":"frozen-synthetic-v1"}\n'
    )
    record["checks"]["required_files_changed"] = all(
        checked_file(directory, name) is not None
        and checked_file(directory, name) != case["files"][name]
        for name in case["changed"]
    )
    if case["child_required"]:
        record["checks"]["official_child_started"] = child_started(
            record["snapshots"], arm
        )
    record["independent_verification"] = check_artifacts(
        directory, state, case["probe"]
    )
    record["checks"]["hidden_artifact_checks"] = record["independent_verification"][
        "passed"
    ]
    record["passed"] = all(record["checks"].values())
    record["artifact_hashes"] = {}
    for path in directory.rglob("*"):
        if (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size <= 128000
            and "__pycache__" not in path.parts
        ):
            relative = str(path.relative_to(directory))
            content = checked_file(directory, relative)
            if content is not None:
                record["artifact_hashes"][relative] = digest(content.encode())
    return record


def summarize(records):
    result = {}
    for arm in ("product", "official-sdk"):
        subset = [r for r in records if r["arm"] == arm]
        result[arm] = {
            "attempts": len(subset),
            "passed": sum(r["passed"] for r in subset),
            "requests": sum(r["gateway"]["attempted_calls"] for r in subset),
            "execution_duration_ms": sum(r["execution_duration_ms"] for r in subset),
        }
    result["paired_outcomes"] = {
        "both_passed": 0,
        "product_only": 0,
        "official_only": 0,
        "neither_passed": 0,
    }
    pairs = {}
    for record in records:
        pairs.setdefault((record["case"], record["repetition"]), {})[
            record["arm"]
        ] = record["passed"]
    for pair in pairs.values():
        if len(pair) != 2:
            continue
        key = (
            "both_passed"
            if all(pair.values())
            else "product_only"
            if pair["product"]
            else "official_only"
            if pair["official-sdk"]
            else "neither_passed"
        )
        result["paired_outcomes"][key] += 1
    return result


def suite_manifest():
    return {
        name: {
            "files": {p: digest(s.encode()) for p, s in c["files"].items()},
            "prompt": digest(c["prompt"].encode()),
            "followup": digest(c["followup"].encode()),
            "probe": digest(c["probe"].encode()),
        }
        for name, c in CASES.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--max-total-calls", type=int, default=192)
    parser.add_argument("--order-seed", type=int, default=20261016)
    parser.add_argument("--cases", default=",".join(CASES))
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly contact the configured real model",
    )
    args = parser.parse_args()
    args.output = args.output.resolve()
    names = args.cases.split(",")
    if not args.live:
        print(json.dumps({"manifest": suite_manifest(), "live": False}, indent=2))
        return
    if (
        args.output.exists()
        or args.output.with_suffix("").exists()
        or not 1 <= args.repetitions <= 5
        or not 32 <= args.max_total_calls <= 512
        or any(n not in CASES for n in names)
        or len(set(names)) != len(names)
    ):
        raise SystemExit(
            "Use fresh output, known unique cases, 1–5 repetitions, and a 32–512 total call cap"
        )
    settings = for_backend(read_env(ROOT / ".env"), "dsh")
    report = {
        "kind": KIND,
        "synthetic_only": True,
        "model": settings["MEDAGENT_LLM_MODEL"],
        "dsh_version": "0.2.0-rc.2",
        "repetitions": args.repetitions,
        "order_seed": args.order_seed,
        "maximum_calls": args.max_total_calls,
        "attempted_calls": 0,
        "manifest": suite_manifest(),
        "runs": [],
        "full_dsh_parity_proven": False,
        "scope": "Three synthetic coding projects; matched user prompts, filesystem, model and call limits. Product persona/budget advice differ from official SDK default persona. Not a general superiority or clinical study.",
    }
    report["source_sha256"] = {
        str(path.relative_to(ROOT)): digest(path.read_bytes())
        for directory in [
            ROOT / "src/medagent",
            ROOT / "integrations/dsh",
            ROOT / "scripts",
        ]
        for path in directory.rglob("*")
        if path.is_file()
        and path.suffix
        in {".py", ".mjs", ".js", ".yml", ".css", ".json", ".html", ".svg"}
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    artifact_root = args.output.with_suffix("")
    artifact_root.mkdir()
    # Freeze manifests/source before the first API call. No secret or .env copied.
    snapshot = artifact_root / "snapshot"
    for name, expected in report["source_sha256"].items():
        data = (ROOT / name).read_bytes()
        assert digest(data) == expected
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    randomizer = random.Random(args.order_seed)
    spent = 0
    for path in args.output.parent.rglob("requests.json"):
        prior = json.loads(path.read_text())
        spent += prior.get("attempted_calls", 0)
    report["prior_directory_calls"] = spent

    def save():
        report["summary"] = summarize(report["runs"])
        report["attempted_calls"] = sum(
            json.loads(path.read_text()).get("attempted_calls", 0)
            for path in artifact_root.rglob("requests.json")
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if settings["MEDAGENT_LLM_API_KEY"] in encoded:
            raise RuntimeError("Credential detected, refusing report")
        args.output.write_text(encoded + "\n")

    try:
        for repetition in range(args.repetitions):
            for name in names:
                arms = ["product", "official-sdk"]
                randomizer.shuffle(arms)
                # Reserve enough for a full pair; never shorten only the second arm.
                if args.max_total_calls - spent - report["attempted_calls"] < 64:
                    report["stopped_reason"] = "insufficient_budget_for_full_pair"
                    return
                for arm in arms:
                    record = evaluate_arm(
                        arm,
                        CASES[name],
                        settings,
                        artifact_root / f"r{repetition + 1}-{name}-{arm}",
                        32,
                    )
                    record.update(case=name, repetition=repetition + 1)
                    report["runs"].append(record)
                    save()
                    print(
                        json.dumps(
                            {
                                "case": name,
                                "repetition": repetition + 1,
                                "arm": arm,
                                "passed": record["passed"],
                                "checks": record["checks"],
                                "calls": record["gateway"]["attempted_calls"],
                                "total_calls": report["attempted_calls"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        report["all_planned_pairs_completed"] = True
    finally:
        save()


if __name__ == "__main__":
    main()
