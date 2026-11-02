"""A truncated model reply can resume once, inside the original task's limits."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK"
)
class ExecutionRecoveryTests(unittest.TestCase):
    def test_truncation_recovers_once_with_same_session_and_shared_budget(self):
        self.exercise(
            "recover",
            maximum=4,
            expected_status="completed",
            expected_calls=3,
            recoveries=1,
        )

    def test_truncation_does_not_get_free_calls_after_budget_exhaustion(self):
        self.exercise(
            "recover",
            maximum=1,
            expected_status="failed",
            expected_calls=1,
            recoveries=0,
        )

    def test_repeated_truncation_stops_after_one_recovery(self):
        self.exercise(
            "repeat",
            maximum=4,
            expected_status="failed",
            expected_calls=2,
            recoveries=1,
        )

    def test_provider_failure_is_not_retried_as_truncation(self):
        self.exercise(
            "service-error",
            maximum=4,
            expected_status="failed",
            expected_calls=1,
            recoveries=0,
        )

    def exercise(self, mode, maximum, expected_status, expected_calls, recoveries):
        observed = []
        owner = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(body)
                step = len(observed)
                if mode == "service-error":
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(
                        b'{"type":"error","error":{"type":"api_error","message":"fixture"}}'
                    )
                    return
                truncated = step == 1 or mode == "repeat"
                content = {
                    "type": "text",
                    "text": "partial" if truncated else "Saved and verified.",
                }
                stop = "max_tokens" if truncated else "end_turn"
                if not truncated and step == 2:
                    owner.assertIn(
                        "RECOVERY_ORIGINAL_TASK", json.dumps(body["messages"])
                    )
                    content = {
                        "type": "tool_use",
                        "id": "recovered-code",
                        "name": "bash",
                        "input": {
                            "command": "python3 -c \"from pathlib import Path; Path('recovered.txt').write_text(str(1250))\"",
                            "description": "Execute original task after output truncation",
                        },
                    }
                    stop = "tool_use"
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"fixture-{step}",
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
                        "delta": {"stop_reason": stop},
                        "usage": {"output_tokens": 8192 if truncated else 10},
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

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                manager = ExecutionManager(root=Path(raw))
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "recovery-fixture-not-real",
                        "MEDAGENT_LLM_MODEL": "deepseek-flash",
                    }
                )
                try:
                    session.start_turn(
                        "RECOVERY_ORIGINAL_TASK: execute code to save 1250 in recovered.txt and verify it.",
                        maximum,
                    )
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        snapshot = session.snapshot()
                        if snapshot["status"] not in {
                            "starting",
                            "running",
                            "cancelling",
                        }:
                            break
                        time.sleep(0.05)
                    self.assertEqual(
                        snapshot["status"], expected_status, snapshot.get("error")
                    )
                    self.assertEqual(len(observed), expected_calls)
                    self.assertEqual(
                        snapshot["turns"][-1]["model_calls"], expected_calls
                    )
                    self.assertEqual(
                        sum(e["type"] == "turn.recovering" for e in snapshot["events"]),
                        recoveries,
                    )
                    if expected_status == "completed":
                        self.assertEqual(
                            (session.directory / "recovered.txt").read_text(), "1250"
                        )
                        roots = {
                            e["notification"]["params"]["sessionId"]
                            for e in snapshot["events"]
                            if e.get("notification", {}).get("method")
                            == "session.event"
                        }
                        self.assertEqual(roots, {session.id})
                    else:
                        self.assertFalse((session.directory / "recovered.txt").exists())
                finally:
                    manager.close()
        finally:
            model.shutdown()
            model.server_close()
