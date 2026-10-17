"""Opt-in wall-clock check across the previous fixed 180-second SDK deadline."""

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
    os.environ.get("RUN_DSH_LONG_EXECUTION") == "1",
    "requires explicit 185-second wall-clock SDK check",
)
class LongBudgetTests(unittest.TestCase):
    def test_ten_minute_budget_survives_old_rpc_deadline_and_collects_same_job(self):
        requests, issues = [], []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                outputs = []
                for message in body.get("messages", []):
                    for item in message.get("content", []):
                        if (
                            not isinstance(item, dict)
                            or item.get("type") != "tool_result"
                        ):
                            continue
                        content = item.get("content", [])
                        if isinstance(content, str):
                            outputs.append(content)
                        else:
                            outputs.extend(
                                block.get("text", "")
                                for block in content
                                if isinstance(block, dict)
                                and block.get("type") == "text"
                            )
                serialized = "\n".join(outputs)
                if len(requests) == 1:
                    tool = {
                        "name": "bash",
                        "input": {
                            "command": "python3 -u -c \"import time;from pathlib import Path;print('long-budget-started',flush=True);time.sleep(185);Path('long-budget.txt').write_text('finished');print('long-budget-finished')\"",
                            "description": "Run once beyond the previous three-minute SDK request timeout",
                            "timeoutMs": 600000,
                        },
                    }
                elif "long-budget-finished" not in serialized:
                    match = re.search(r"moved to background job ([^\]\s]+)", serialized)
                    if not match:
                        issues.append("Original job ID missing")
                        tool = None
                    else:
                        tool = {
                            "name": "job_output",
                            "input": {
                                "job_id": match.group(1),
                                "wait": True,
                                "timeout_ms": 600000,
                            },
                        }
                else:
                    tool = None
                content = (
                    {"type": "tool_use", "id": f"long-{len(requests)}", **tool}
                    if tool
                    else {
                        "type": "text",
                        "text": "Collected the original long-running job.",
                    }
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"long-{len(requests)}",
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
                payload = "".join(
                    "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                    for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                manager = ExecutionManager(root=Path(raw))
                try:
                    session = manager.create(
                        {
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                            "MEDAGENT_LLM_API_KEY": "long-budget-fixture-only",
                            "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        }
                    )
                    started = time.monotonic()
                    session.start_turn(
                        "Run the command once and collect the same job.", 12, 600
                    )
                    while time.monotonic() - started < 230:
                        snapshot = session.snapshot()
                        if snapshot["status"] not in {
                            "starting",
                            "running",
                            "cancelling",
                        }:
                            break
                        time.sleep(0.2)
                    elapsed = time.monotonic() - started
                    self.assertEqual(
                        snapshot["status"], "completed", snapshot.get("error")
                    )
                    self.assertGreaterEqual(elapsed, 185)
                    self.assertLess(elapsed, 215)
                    self.assertEqual(
                        (session.directory / "long-budget.txt").read_text(), "finished"
                    )
                    self.assertEqual(issues, [])
                    self.assertEqual(len(requests), 8)
                    self.assertTrue(
                        any(
                            o["tool"] == "job_output" and o["exit_code"] == 0
                            for o in session.observations
                        )
                    )
                finally:
                    manager.close()
        finally:
            server.shutdown()
            server.server_close()
