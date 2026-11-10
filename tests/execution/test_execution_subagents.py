"""Real asynchronous delegation, explicit waits and continuation in pinned DSH."""

import json
import os
import threading
import unittest

from tests.support.delivery_runtime import DeliveryRuntime, wait_until
from tests.support.scripted import final, tool
from tests.support.subagent_runtime import started_child, wait_results


@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class SubagentCompletionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = DeliveryRuntime()
        self.addCleanup(self.runtime.close)

    def test_wait_then_message_and_wait_again_reads_new_activation_and_real_artifacts(self):
        roots, children, receipts = [], [], []

        def respond(index, body):
            if "ROOT_WAIT" not in json.dumps(body["messages"]):
                children.append(body)
                step = len(children)
                if step in (1, 3):
                    command = (
                        "from pathlib import Path; Path('child.txt').write_text('first')"
                        if step == 1
                        else "from pathlib import Path; assert Path('child.txt').read_text()=='first'; Path('child.txt').write_text('revised')"
                    )
                    return tool(
                        "bash",
                        command=f'python3 -c "{command}"',
                        description="Save actual child work",
                    )
                return final("FIRST_RESULT" if step == 2 else "REVISED_RESULT")
            roots.append(body)
            step = len(roots)
            if step == 1:
                return tool(
                    "subagent",
                    description="Bounded child work",
                    prompt="CHILD_WAIT: write child.txt then report FIRST_RESULT",
                )
            if step in (2, 4):
                return tool("wait_subagent", agent_id=started_child(body))
            if step == 3:
                receipts.append(wait_results(body)[-1])
                return tool(
                    "send_message",
                    agent_id=started_child(body),
                    message="REVISE: assert child.txt is first, change it to revised, report REVISED_RESULT",
                )
            if step == 5:
                receipts.append(wait_results(body)[-1])
                return tool(
                    "delivery_check",
                    label="核对接续产物",
                    command="python3 -c \"from pathlib import Path; assert Path('child.txt').read_text()=='revised'\"",
                )
            return final("已核对真实接续产物")

        session = self.runtime.session(respond)
        session.start_turn("ROOT_WAIT 显式等待子任务，再接续同一个子任务并核对产物。", 12, 40)
        snapshot = self.runtime.finish(session)
        self.assertEqual(
            snapshot["status"],
            "completed",
            json.dumps(
                {
                    "error": snapshot.get("error"),
                    "receipts": receipts,
                    "observations": session.observations,
                    "roots": len(roots),
                    "children": len(children),
                },
                ensure_ascii=False,
            ),
        )
        self.assertEqual((len(roots), len(children)), (6, 4))
        self.assertTrue(all(r["max_tokens"] <= 8192 for r in self.runtime.provider.requests))
        self.assertEqual([r["state"] for r in receipts], ["completed", "completed"])
        self.assertEqual([r["output"] for r in receipts], ["FIRST_RESULT", "REVISED_RESULT"])
        self.assertEqual(receipts[0]["agent_id"], receipts[1]["agent_id"])
        self.assertNotEqual(receipts[0]["activation_id"], receipts[1]["activation_id"])
        self.assertEqual((session.directory / "child.txt").read_text(), "revised")
        self.assertEqual(self.runtime.observations(session)[0]["exit_code"], 0)
        self.assertTrue(all(o["turn_id"] == session.turn_id for o in session.observations))

    def test_wait_timeout_does_not_stop_child_and_retry_does_not_spawn_a_duplicate(self):
        release, roots, receipts = threading.Event(), [], []
        self.addCleanup(release.set)

        def respond(index, body):
            if "ROOT_TIMEOUT" not in json.dumps(body["messages"]):
                release.wait(8)
                return final("DELAYED_RESULT")
            roots.append(body)
            step = len(roots)
            if step == 1:
                return tool(
                    "subagent",
                    description="Delayed result",
                    prompt="CHILD_TIMEOUT: return DELAYED_RESULT",
                )
            if step == 2:
                return tool("wait_subagent", agent_id=started_child(body), timeout_ms=10)
            receipts.append(wait_results(body)[-1])
            if step == 3:
                release.set()
                return tool("wait_subagent", agent_id=started_child(body))
            return final("取得同一子任务结果")

        session = self.runtime.session(respond)
        session.start_turn("ROOT_TIMEOUT 等待超时后，继续等同一个子任务。", 8, 30)
        snapshot = self.runtime.finish(session)
        self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
        self.assertEqual([r["state"] for r in receipts], ["timed_out", "completed"])
        self.assertEqual(receipts[0]["activation_id"], receipts[1]["activation_id"])
        self.assertEqual(receipts[1]["output"], "DELAYED_RESULT")
        self.assertEqual(len(self.runtime.provider.requests), 5)

    def test_user_stop_interrupts_a_long_wait_and_allows_an_independent_next_turn(self):
        release, waiting = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        roots = []

        def respond(index, body):
            messages = json.dumps(body["messages"])
            if "ROOT_STOP" not in messages:
                release.wait(8)
                return final("LATE_CHILD_RESULT")
            if "NEXT_INDEPENDENT" in messages:
                return final("新任务完成")
            roots.append(body)
            if len(roots) == 1:
                return tool(
                    "subagent",
                    description="Cancellable child",
                    prompt="CHILD_STOP: do bounded work",
                )
            waiting.set()
            return tool("wait_subagent", agent_id=started_child(body), timeout_ms=600000)

        session = self.runtime.session(respond)
        session.start_turn("ROOT_STOP 等待子任务，用户稍后停止。", 8, 30)
        self.assertTrue(waiting.wait(10))
        wait_until(
            lambda: any(
                e["type"] == "runtime.event"
                and e.get("notification", {}).get("method") == "session.event"
                and e["notification"]["params"].get("event", {}).get("type") == "tool/call"
                and e["notification"]["params"]["event"]["data"].get("name") == "wait_subagent"
                for e in list(session.events)
            )
        )
        session.cancel()
        self.assertEqual(self.runtime.finish(session)["status"], "cancelled")
        release.set()
        process = session.process
        session.start_turn("NEXT_INDEPENDENT 请完成独立任务。", 4, 20)
        self.assertEqual(self.runtime.finish(session)["status"], "completed")
        self.assertIs(session.process, process)
        self.assertNotIn("LATE_CHILD_RESULT", session.turns[-1].get("final_response", ""))
