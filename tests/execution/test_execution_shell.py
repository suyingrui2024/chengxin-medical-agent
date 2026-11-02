"""Actual official SDK checks for foreground handoff and pipeline exit status."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class ExecutionShellTests(unittest.TestCase):
    def test_pipeline_failure_is_preserved_and_oversized_wait_hands_off_same_job(self):
        observed, arrival, failures = [], [], []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(body)
                arrival.append(time.monotonic())
                step = len(observed)
                serialized = json.dumps(body)
                try:
                    if step == 1:
                        content = {
                            "type": "tool_use",
                            "id": "pipeline",
                            "name": "bash",
                            "input": {
                                "command": "python3 -c \"import sys;print('expected failure');sys.exit(7)\" | tail -n 1",
                                "description": "Observe failing test pipeline exit status",
                            },
                        }
                    elif step == 2:
                        if "[exit code: 7]" not in serialized:
                            raise AssertionError(
                                "The failing pipeline was reported as successful: "
                                + json.dumps(
                                    [
                                        item
                                        for message in body.get("messages", [])
                                        for item in message.get("content", [])
                                        if isinstance(item, dict)
                                        and item.get("type") == "tool_result"
                                    ]
                                )[-2000:]
                            )
                        content = {
                            "type": "tool_use",
                            "id": "long-command",
                            "name": "bash",
                            "input": {
                                "command": "python3 -u -c \"import time;from pathlib import Path;print('long-task-started',flush=True);time.sleep(32);Path('slow.txt').write_text('finished');print('long-task-finished')\"",
                                "description": "Run bounded foreground handoff check",
                                "timeoutMs": 600000,
                            },
                        }
                    elif step == 3:
                        if "still running after 30000ms" not in serialized:
                            raise AssertionError(
                                "Oversized foreground wait was not capped"
                            )
                        identity = re.search(
                            r"moved to background job ([^\]\s]+)", serialized
                        ).group(1)
                        if not 28 <= arrival[2] - arrival[1] < 40:
                            raise AssertionError(
                                "Foreground did not hand off within its real wait bound"
                            )
                        content = {
                            "type": "tool_use",
                            "id": "collect-job",
                            "name": "job_output",
                            "input": {
                                "job_id": identity,
                                "wait": True,
                                "timeout_ms": 600000,
                            },
                        }
                    elif step == 4:
                        if (
                            "long-task-finished" not in serialized
                            or "[status: completed" not in serialized
                        ):
                            raise AssertionError(
                                "Original background job was not collected successfully"
                            )
                        content = {
                            "type": "tool_use",
                            "id": "verify",
                            "name": "bash",
                            "input": {
                                "command": "python3 -c \"from pathlib import Path;assert Path('slow.txt').read_text()=='finished';Path('checked.txt').write_text('verified')\"",
                                "description": "Verify original job artifact without relaunch",
                            },
                        }
                    else:
                        content = {
                            "type": "text",
                            "text": "Verified the expected pipeline failure and completed the same long job.",
                        }
                except Exception as exc:
                    failures.append(str(exc))
                    content = {"type": "text", "text": "Fixture assertion failed."}
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"timing-{step}",
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

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                manager = ExecutionManager(root=Path(raw))
                try:
                    session = manager.create(
                        {
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                            "MEDAGENT_LLM_API_KEY": "shell-fixture-only",
                            "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        }
                    )
                    session.start_turn(
                        "Verify shell failure, long-job handoff and collection.", 8
                    )
                    deadline = time.monotonic() + 65
                    while time.monotonic() < deadline:
                        snapshot = session.snapshot()
                        if snapshot["status"] not in {
                            "starting",
                            "running",
                            "cancelling",
                        }:
                            break
                        time.sleep(0.05)
                    self.assertFalse(failures, failures)
                    self.assertEqual(
                        snapshot["status"], "completed", snapshot.get("error")
                    )
                    self.assertEqual(len(observed), 5)
                    self.assertEqual(
                        (session.directory / "checked.txt").read_text(), "verified"
                    )
                    self.assertEqual(
                        sum(
                            "time.sleep(32)" in json.dumps(e.get("notification", {}))
                            and e.get("notification", {})
                            .get("params", {})
                            .get("event", {})
                            .get("type")
                            == "tool/call"
                            for e in snapshot["events"]
                        ),
                        1,
                    )
                finally:
                    manager.close()
        finally:
            model.shutdown()
            model.server_close()


if __name__ == "__main__":
    unittest.main()
