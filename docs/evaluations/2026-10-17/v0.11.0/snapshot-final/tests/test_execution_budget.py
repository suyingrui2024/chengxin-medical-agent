"""A selected deadline stops real work and survives offline history restore."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager, ExecutionError


class BudgetValidationTests(unittest.TestCase):
    def test_invalid_budgets_do_not_submit_or_launch_a_model(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
                        "MEDAGENT_LLM_API_KEY": "fixture",
                        "MEDAGENT_LLM_MODEL": "fixture",
                    }
                )
                for value in [0, -1, 1801, True, 1.5, None, "600"]:
                    with self.subTest(value=value):
                        with self.assertRaises(ExecutionError):
                            session.start_turn(
                                "Must not launch", time_budget_seconds=value
                            )
                self.assertIsNone(session.process)
                self.assertEqual(session.turns, [])
            finally:
                manager.close()


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class BudgetRuntimeTests(unittest.TestCase):
    def test_deadline_cancels_process_then_long_budget_continues_same_session(self):
        self.exercise(False)

    def test_deadline_stops_background_after_main_reply_and_persists_cancelled_state(
        self,
    ):
        self.exercise(True)

    def exercise(self, background):
        requests = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                step = len(requests)
                if step == 1:
                    tool = {
                        "name": "bash",
                        "input": {
                            "command": "python3 -c \"import time;from pathlib import Path;time.sleep(8);Path('late.txt').write_text('must not happen')\"",
                            "description": "Run a process beyond the selected three-second budget",
                        },
                    }
                    if background:
                        tool["input"]["run_in_background"] = True
                elif step == 2 + int(background):
                    tool = {
                        "name": "bash",
                        "input": {
                            "command": "printf resumed > resumed.txt",
                            "description": "Continue with an explicitly selected ten-minute budget",
                        },
                    }
                else:
                    tool = None
                content = (
                    {"type": "tool_use", "id": f"budget-{step}", **tool}
                    if tool
                    else {"type": "text", "text": "Resumed and saved."}
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"budget-{step}",
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
                        "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
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
                manager = ExecutionManager(root=Path(raw))
                try:
                    session = manager.create(
                        {
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                            "MEDAGENT_LLM_API_KEY": "budget-fixture-only",
                            "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        }
                    )
                    session.start_turn(
                        "Stop this process at the selected deadline.", 4, 3
                    )
                    first = self.wait(session, "cancelled")
                    self.assertEqual(first["status"], "cancelled", first.get("error"))
                    self.assertEqual(first["error"], "time_budget_exhausted")
                    self.assertEqual(len(requests), 1 + int(background))
                    self.assertFalse((session.directory / "late.txt").exists())
                    process = session.process
                    session.start_turn("CONTINUE_BUDGET: save resumed.txt.", 4, 600)
                    continued = self.wait(session)
                    self.assertEqual(
                        continued["status"], "completed", continued.get("error")
                    )
                    self.assertIs(session.process, process)
                    self.assertEqual(
                        (session.directory / "resumed.txt").read_text(), "resumed"
                    )
                    seconds = [
                        int(n)
                        for n in re.findall(
                            r"剩余执行时间约 (\d+) 秒",
                            json.dumps(
                                requests[1 + int(background)], ensure_ascii=False
                            ),
                        )
                    ]
                    self.assertTrue(any(500 < n <= 600 for n in seconds), seconds)
                    self.assertEqual(len(requests), 3 + int(background))
                    identity = session.id
                finally:
                    manager.close()
                restored_manager = ExecutionManager(root=Path(raw))
                try:
                    restored = restored_manager.open(identity, {})
                    self.assertEqual(
                        [t["time_budget_seconds"] for t in restored.turns], [3, 600]
                    )
                    self.assertIsNone(restored.process)
                    self.assertEqual(len(requests), 3 + int(background))
                finally:
                    restored_manager.close()
        finally:
            server.shutdown()
            server.server_close()

    def wait(self, session, target=None):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if (
                snapshot["status"] == target
                or snapshot["status"] == "failed"
                or (
                    target is None
                    and snapshot["status"] not in {"starting", "running", "cancelling"}
                )
            ):
                return snapshot
            time.sleep(0.05)
        self.fail("Selected execution budget did not settle")
