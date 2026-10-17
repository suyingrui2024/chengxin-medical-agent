"""Canonical observations and preparation jobs survive real SDK lifecycle edges."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from medagent.execution import ExecutionBusy, ExecutionManager
from medagent.execution_observations import validate_observation


class ObservationContractTests(unittest.TestCase):
    def test_invalid_status_payloads_cannot_enter_history_as_valid_observations(self):
        value = {
            "schema": "execution-observation/v1",
            "epoch": str(uuid4()),
            "sequence": 1,
            "session_id": "root",
            "call_id": "shell",
            "sdk_turn": 0,
            "sdk_step": 1,
            "call_sequence": 2,
            "tool": "bash",
            "state": "completed",
            "exit_code": 0,
            "signal": None,
            "job_id": None,
            "job_kind": None,
            "wait_ms": None,
            "turn_id": str(uuid4()),
        }
        validated = validate_observation(value)
        self.assertEqual(validate_observation(validated), validated)
        for changes in [
            {"exit_code": True},
            {"state": []},
            {"tool": {}},
            {"sequence": 0},
            {"wait_ms": float("nan")},
            {"wait_ms": 10**1000},
            {"observation_id": "forged"},
            {"clinical_fact": "unreviewed"},
            {"turn_id": "unknown"},
        ]:
            with self.subTest(fields=list(changes)):
                with self.assertRaises(ValueError):
                    validate_observation({**value, **changes})


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class PreparingJobTests(unittest.TestCase):
    def test_preparing_job_retains_wakeup_budget_and_can_be_cancelled(self):
        requests = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                step = len(requests)
                if step in {1, 5}:
                    filename = "ready.txt" if step == 1 else "must-not-appear.txt"
                    content = {
                        "type": "tool_use",
                        "id": "prepare",
                        "name": "bash",
                        "input": {
                            "command": "# PREPARATION_DELAY\n"
                            + f"printf ready > {filename}",
                            "description": "Prepare a cancellable background command",
                            "run_in_background": True,
                        },
                    }
                elif step == 3:
                    content = {
                        "type": "tool_use",
                        "id": "verify",
                        "name": "bash",
                        "input": {
                            "command": "test -f ready.txt && printf verified > after-ready.txt",
                            "description": "Verify completed background job after wakeup",
                        },
                    }
                else:
                    content = {
                        "type": "text",
                        "text": "Background launched."
                        if step in {2, 6}
                        else "Verified.",
                    }
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"preparation-{step}",
                            "type": "message",
                            "role": "assistant",
                            "model": "deepseek-flash",
                            "content": [],
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": content,
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "tool_use"
                            if content["type"] == "tool_use"
                            else "end_turn",
                        },
                        "usage": {"output_tokens": 10},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                    for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                source = root / "source"
                original = Path(__file__).resolve().parents[1]
                # Delay only this fixture's executor; ship no production test flags.
                for name in ["src", "integrations"]:
                    shutil.copytree(
                        original / name,
                        source / name,
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )
                (source / "node_modules").symlink_to(
                    original / "node_modules", target_is_directory=True
                )
                shell = source / "integrations/dsh/execution-shell.mjs"
                text = shell.read_text()
                self.assertEqual(text.count("execute(spec) {"), 1)
                shell.write_text(
                    text.replace(
                        "execute(spec) {",
                        """async execute(spec) {
    if (spec.command.includes('PREPARATION_DELAY')) {
      await new Promise(resolve => setTimeout(resolve, 2500));
      spec.signal?.throwIfAborted();
    }
""",
                    )
                )
                manager = ExecutionManager(root=root / "workspaces", source_root=source)
                try:
                    session = manager.create(
                        {
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                            "MEDAGENT_LLM_API_KEY": "preparation-fixture-only",
                            "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        }
                    )
                    self.session = session
                    session.start_turn(
                        "Start background preparation and verify after wakeup.", 8
                    )
                    self.wait_for(
                        lambda: session.status == "completed"
                        and any(
                            j["status"] == "running" for j in session.snapshot()["jobs"]
                        )
                    )
                    with self.assertRaises(ExecutionBusy):
                        manager.release(session.id)
                    self.assertFalse((session.directory / "ready.txt").exists())
                    self.wait_for(
                        lambda: (session.directory / "after-ready.txt").exists()
                    )
                    self.wait_for(lambda: len(requests) >= 4)
                    self.assertEqual(len(requests), 4)
                    self.wait_for(
                        lambda: any(
                            e["type"] == "workspace.quiescent"
                            for e in session.snapshot()["events"]
                        )
                    )
                    session.start_turn(
                        "Start another background command, to be stopped while preparing.",
                        6,
                    )
                    self.wait_for(
                        lambda: session.status == "completed"
                        and any(
                            j["status"] == "running" for j in session.snapshot()["jobs"]
                        )
                    )
                    self.assertEqual(len(requests), 6)
                    session.cancel()
                    self.wait_for(lambda: session.status == "cancelled")
                    time.sleep(2.8)
                    self.assertFalse(
                        (session.directory / "must-not-appear.txt").exists()
                    )
                    self.assertEqual(len(requests), 6)
                    self.assertTrue(session.process.poll() is None)
                finally:
                    manager.close()
        finally:
            server.shutdown()
            server.server_close()

    def wait_for(self, condition):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.03)
        snapshot = self.session.snapshot()
        self.fail(
            "Real SDK lifecycle condition did not settle: "
            + json.dumps(
                {
                    "status": snapshot["status"],
                    "error": snapshot.get("error"),
                    "jobs": snapshot["jobs"],
                    "events": snapshot["events"][-8:],
                },
                ensure_ascii=False,
            )[-6000:]
        )
