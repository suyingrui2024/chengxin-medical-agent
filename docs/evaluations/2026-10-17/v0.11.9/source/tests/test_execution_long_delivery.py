"""Opt-in actual check beyond the former 120/135-second tool and service caps."""

import json
import os
import time
import unittest

import tests.test_execution_delivery as fixtures


@unittest.skipUnless(
    os.environ.get("RUN_DSH_LONG_EXECUTION") == "1",
    "requires explicit 142-second delivery runtime check",
)
class LongDeliveryTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("RUN_DSH_CHUNKED_DELIVERY") == "1",
        "requires explicit 605-second multi-wait delivery runtime check",
    )
    def test_check_continues_same_job_across_the_600_second_wait_boundary(self):
        fixture = fixtures.DeliveryRuntimeTests()
        fixture.setUp()
        try:
            session = fixture.session(
                [
                    fixtures.tool(
                        "delivery_check",
                        label="跨600秒检查",
                        command="python3 -u -c \"import time;from pathlib import Path;print('started',flush=True);time.sleep(605);Path('chunked-check.txt').write_text('finished');print('finished')\"",
                        timeout_ms=720000,
                    ),
                    fixtures.final("跨等待边界的同一作业完成。"),
                ]
            )
            started = time.monotonic()
            session.start_turn("合成跨600秒等待边界，不能重启命令或增加模型调用。", 2, 900)
            deadline = time.monotonic() + 665
            while time.monotonic() < deadline:
                snapshot = session.snapshot()
                if snapshot["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.5)
            else:
                self.fail("Chunked check exceeded bounded verification window")
            elapsed = time.monotonic() - started
            self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
            self.assertGreaterEqual(elapsed, 603)
            self.assertEqual(
                (session.directory / "chunked-check.txt").read_text(), "finished"
            )
            values = []
            for envelope in snapshot["events"]:
                event = (
                    envelope.get("notification", {}).get("params", {}).get("event", {})
                )
                if event.get("type") != "tool/result":
                    continue
                for block in (
                    event.get("data", {}).get("message", {}).get("content", [])
                ):
                    if block.get("type") != "text":
                        continue
                    try:
                        value = json.loads(block.get("text", ""))
                    except ValueError:
                        continue
                    if (
                        isinstance(value, dict)
                        and value.get("schema") == "execution-delivery/v1"
                    ):
                        values.append(value)
            self.assertEqual(len(values), 1)
            self.assertGreaterEqual(values[0]["wait_cycles"], 2)
            self.assertEqual(values[0]["exit_code"], 0)
            self.assertEqual(len(fixture.provider.requests), 2)
            print(
                "CHUNKED_DELIVERY_PROOF="
                + json.dumps(
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "model_fixture_requests": 2,
                        "wait_cycles": values[0]["wait_cycles"],
                        "actual_exit": values[0]["exit_code"],
                        "same_job": values[0]["job_id"],
                        "status": snapshot["status"],
                    }
                ),
                flush=True,
            )
        finally:
            fixture.tearDown()

    def test_check_runs_beyond_the_old_tool_cap_and_collects_the_same_job(self):
        fixture = fixtures.DeliveryRuntimeTests()
        fixture.setUp()
        try:
            session = fixture.session(
                [
                    fixtures.tool(
                        "delivery_check",
                        label="真实长检查",
                        command="python3 -u -c \"import time;from pathlib import Path;print('started',flush=True);time.sleep(142);Path('long-check.txt').write_text('finished');print('finished')\"",
                        timeout_ms=180000,
                    ),
                    fixtures.final("同一检查作业已运行完成。"),
                ]
            )
            started = time.monotonic()
            session.start_turn("合成真实长检查，保持同一作业和两次模型调用。", 2, 300)
            deadline = time.monotonic() + 205
            while time.monotonic() < deadline:
                snapshot = session.snapshot()
                if snapshot["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.2)
            else:
                self.fail("Long check exceeded bounded verification window")
            elapsed = time.monotonic() - started
            self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
            self.assertGreaterEqual(elapsed, 140)
            self.assertEqual(
                (session.directory / "long-check.txt").read_text(), "finished"
            )
            checks = fixture.observations(session)
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0]["exit_code"], 0)
            self.assertEqual(len(fixture.provider.requests), 2)
            print(
                "LONG_DELIVERY_PROOF="
                + json.dumps(
                    {
                        "elapsed_seconds": round(elapsed, 3),
                        "model_fixture_requests": 2,
                        "actual_exit": checks[0]["exit_code"],
                        "same_job": checks[0]["job_id"],
                        "status": snapshot["status"],
                    }
                ),
                flush=True,
            )
        finally:
            fixture.tearDown()
