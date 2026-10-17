"""Actual pinned harness tools with a deterministic local model, no paid requests."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager
from tests.test_execution_capabilities import model_reply


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "Requires real isolated DSH"
)
class GlucoseSdkTests(unittest.TestCase):
    def test_host_rejects_incomplete_evidence_then_parent_and_child_compute_and_restore(
        self,
    ):
        observed, root_step, child_step = [], [0], [0]
        owner = self

        def calculate(quote="血糖180mg/dL"):
            return {
                "name": "medical_calculate",
                "input": {
                    "capability_id": "calculate.glucose",
                    "evidence": quote,
                    "input": {
                        "value": "180",
                        "from_unit": "mg/dL",
                        "to_unit": "mmol/L",
                    },
                },
            }

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(body)
                owner.assertTrue(
                    {"medical_capabilities", "medical_calculate", "subagent"}
                    <= {t.get("name") for t in body.get("tools", [])}
                )
                if "ROOT_GLUCOSE" not in json.dumps(body["messages"]):
                    step = child_step[0]
                    child_step[0] += 1
                    tool = calculate() if step == 0 else None
                else:
                    step = root_step[0]
                    root_step[0] += 1
                    if step == 0:
                        tool = {"name": "medical_capabilities", "input": {}}
                    elif step == 1:
                        owner.assertIn(
                            "calculate.glucose", json.dumps(body["messages"])
                        )
                        tool = calculate("180mg/dL")
                    elif step == 2:
                        owner.assertIn(
                            "glucose_subject_missing", json.dumps(body["messages"])
                        )
                        tool = calculate()
                    elif step == 3:
                        owner.assertIn("precision_policy", json.dumps(body["messages"]))
                        tool = {
                            "name": "subagent",
                            "input": {
                                "description": "复核血糖换算",
                                "prompt": "CHILD_GLUCOSE 对用户的血糖180mg/dL使用注册工具换成mmol/L，保留出处，不执行脚本。",
                                "run_in_background": False,
                            },
                        }
                    else:
                        tool = None
                model_reply(self, len(observed), tool)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                manager = ExecutionManager(root=Path(raw))
                try:
                    session = manager.create(
                        {
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                            "MEDAGENT_LLM_API_KEY": "glucose-fixture-only",
                            "MEDAGENT_LLM_MODEL": "fixture",
                        }
                    )
                    identity = session.id
                    session.start_turn("ROOT_GLUCOSE 请把血糖180mg/dL换成mmol/L，并委托复核。", 10)
                    deadline = time.monotonic() + 40
                    while (
                        session.status in {"starting", "running", "cancelling"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    snapshot = session.snapshot()
                    owner.assertEqual(
                        snapshot["status"], "completed", snapshot["error"]
                    )
                    owner.assertEqual((root_step[0], child_step[0]), (5, 2))
                    owner.assertEqual(len(snapshot["calculations"]), 2)
                    for receipt in snapshot["calculations"]:
                        owner.assertEqual(receipt["data"]["value"], "10")
                        owner.assertTrue(receipt["data"]["approximate"])
                        owner.assertEqual(
                            receipt["evidence"]["source_turn_id"], session.turn_id
                        )
                        owner.assertEqual(
                            receipt["references"][0]["url"],
                            "https://diabetes.org/about-diabetes/common-terms",
                        )
                    owner.assertEqual(
                        sum(
                            e["type"] == "capability.failed" for e in snapshot["events"]
                        ),
                        1,
                    )
                    receipts = snapshot["calculations"]
                finally:
                    manager.close()
                count = len(observed)
                manager = ExecutionManager(root=Path(raw))
                try:
                    restored = manager.open(identity, {})
                    owner.assertEqual(restored.calculations, receipts)
                    owner.assertIsNone(restored.process)
                    owner.assertEqual(len(observed), count)
                finally:
                    manager.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
