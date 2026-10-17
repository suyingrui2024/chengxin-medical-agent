"""Actual wall-clock checks for explicit waits, cancellation and shared deadlines."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager

STAGE = Path(__file__).resolve().parents[1]


def wait_until(function, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = function()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Expected state did not arrive within its bounded wait")


class Fixture:
    def __init__(self, source, directory, seconds=34):
        self.calls = []
        self.arrivals = []
        self.wait_entered = threading.Event()
        self.outputs = []
        owner = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.calls.append(body)
                owner.arrivals.append(time.monotonic())
                serialized = json.dumps(body)
                index = len(owner.calls)
                if index == 1:
                    content = {
                        "type": "tool_use",
                        "id": "launch",
                        "name": "bash",
                        "input": {
                            "command": "python3 -u -c \"import time;from pathlib import Path;print('WAIT_STARTED',flush=True);time.sleep("
                            + str(seconds)
                            + ");Path('finished.txt').write_text('done');print('WAIT_FINISHED')\"",
                            "description": "Start one actual background command",
                            "run_in_background": True,
                        },
                    }
                elif index == 2:
                    match = re.search(r"\b(bash-\d+)\b", serialized)
                    if not match:
                        content = {
                            "type": "text",
                            "text": "Fixture failed: background job id missing",
                        }
                    else:
                        content = {
                            "type": "tool_use",
                            "id": "wait",
                            "name": "job_output",
                            "input": {
                                "job_id": match.group(1),
                                "wait": True,
                                "timeout_ms": 120000,
                            },
                        }
                        owner.wait_entered.set()
                else:
                    owner.outputs.append(
                        "\n".join(
                            block.get("text", "")
                            for message in body.get("messages", [])
                            for item in message.get("content", [])
                            if isinstance(item, dict)
                            and item.get("type") == "tool_result"
                            for block in (
                                item.get("content")
                                if isinstance(item.get("content"), list)
                                else [{"text": item.get("content", "")}]
                            )
                            if isinstance(block, dict)
                        )
                    )
                    content = {
                        "type": "text",
                        "text": "Fixture stopped after the one requested wait.",
                    }
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"wait-{index}",
                            "type": "message",
                            "role": "assistant",
                            "model": "fixture",
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
                            else "end_turn"
                        },
                        "usage": {"output_tokens": 10},
                    },
                    {"type": "message_stop"},
                ]
                payload = "".join(
                    "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                    for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=self.model.serve_forever, daemon=True).start()
        self.manager = ExecutionManager(root=directory, source_root=source)
        self.session = self.manager.create(
            {
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{self.model.server_port}/anthropic",
                "MEDAGENT_LLM_API_KEY": "wait-local-fixture-only",
                "MEDAGENT_LLM_MODEL": "fixture",
            }
        )

    def start(self, seconds=90):
        self.session.start_turn(
            "Run one background command and explicitly wait for its result.", 4, seconds
        )

    def terminal(self):
        value = self.session.snapshot()
        return (
            value
            if value["status"] not in {"starting", "running", "cancelling"}
            else None
        )

    def waiting_tool_active(self):
        pending = set()
        for entry in self.session.snapshot()["events"]:
            notification = entry.get("notification", {})
            if notification.get("method") != "session.event":
                continue
            event = notification.get("params", {}).get("event", {})
            data = event.get("data", {})
            if event.get("type") == "tool/call" and data.get("name") == "job_output":
                pending.add(data.get("callId"))
            elif event.get("type") == "tool/result":
                pending.discard(data.get("message", {}).get("toolCallId"))
        return bool(pending)

    def close(self):
        self.manager.close()
        self.model.shutdown()
        self.model.server_close()


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class ExplicitWaitTests(unittest.TestCase):
    def test_same_three_requests_collect_a_34_second_job_only_with_explicit_long_wait(
        self,
    ):
        evidence = []
        with tempfile.TemporaryDirectory() as raw:
            control = Path(raw) / "control-source"
            for name in ["src", "integrations"]:
                shutil.copytree(
                    STAGE / name,
                    control / name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                )
            (control / "node_modules").symlink_to(
                (STAGE / "node_modules").resolve(), target_is_directory=True
            )
            patch = control / "integrations/dsh/execution.patch.yml"
            config = patch.read_text()
            if config.count("maxWaitTimeoutMs: 600000") != 1:
                raise AssertionError(
                    "Explicit wait policy changed; review the historical control"
                )
            patch.write_text(
                config.replace("maxWaitTimeoutMs: 600000", "maxWaitTimeoutMs: 30000")
            )
            for label, source, expected in [
                ("old", control, False),
                ("candidate", STAGE, True),
            ]:
                fixture = Fixture(source, Path(raw) / label)
                try:
                    fixture.start()
                    snapshot = wait_until(fixture.terminal, 50)
                    self.assertEqual(
                        snapshot["status"], "completed", snapshot.get("error")
                    )
                    self.assertEqual(len(fixture.calls), 3)
                    self.assertEqual(
                        (fixture.session.directory / "finished.txt").is_file(), expected
                    )
                    self.assertEqual("WAIT_FINISHED" in fixture.outputs[-1], expected)
                    duration = fixture.arrivals[2] - fixture.arrivals[1]
                    self.assertGreaterEqual(duration, 32 if expected else 28)
                    self.assertLess(duration, 45 if expected else 33)
                    evidence.append(
                        {
                            "configuration": label,
                            "model_calls": len(fixture.calls),
                            "wait_seconds": round(duration, 2),
                            "actual_artifact_collected": expected,
                        }
                    )
                finally:
                    fixture.close()
        print(json.dumps({"explicit_wait_comparison": evidence}), flush=True)

    def test_user_cancel_interrupts_one_long_tool_wait_and_preserves_process(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(STAGE, Path(raw))
            try:
                fixture.start()
                self.assertTrue(fixture.wait_entered.wait(10))
                wait_until(fixture.waiting_tool_active, 3)
                time.sleep(0.1)
                pid = fixture.session.process.pid
                started = time.monotonic()
                fixture.session.cancel()
                snapshot = wait_until(fixture.terminal, 6)
                duration = time.monotonic() - started
                self.assertEqual(snapshot["status"], "cancelled", snapshot)
                self.assertFalse((fixture.session.directory / "finished.txt").exists())
                self.assertEqual(fixture.session.process.pid, pid)
                fixture.session.start_turn("Continue after cancelled wait.", 2, 10)
                self.assertEqual(
                    wait_until(fixture.terminal, 12)["status"], "completed"
                )
                self.assertEqual(fixture.session.process.pid, pid)
                print(
                    json.dumps(
                        {
                            "cancel_wait_seconds": round(duration, 2),
                            "same_process_continued": True,
                        }
                    ),
                    flush=True,
                )
            finally:
                fixture.close()

    def test_task_deadline_still_cancels_the_long_tool_wait_and_its_command(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = Fixture(STAGE, Path(raw))
            try:
                fixture.start(4)
                self.assertTrue(fixture.wait_entered.wait(10))
                wait_until(fixture.waiting_tool_active, 3)
                snapshot = wait_until(fixture.terminal, 10)
                self.assertEqual(snapshot["status"], "cancelled")
                self.assertEqual(snapshot["error"], "time_budget_exhausted")
                self.assertFalse((fixture.session.directory / "finished.txt").exists())
                self.assertEqual(len(fixture.calls), 2)
                print(
                    json.dumps(
                        {
                            "deadline_interrupted_wait": True,
                            "shared_task_seconds": 4,
                            "calls": len(fixture.calls),
                        }
                    ),
                    flush=True,
                )
            finally:
                fixture.close()


if __name__ == "__main__":
    unittest.main()
