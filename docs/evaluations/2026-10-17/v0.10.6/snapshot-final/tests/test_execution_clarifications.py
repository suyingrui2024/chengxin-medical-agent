"""Human answers resume the original SDK call, with no invented default or budget reset."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from medagent.execution import ExecutionManager, ExecutionError, ExecutionBusy
from medagent.execution_clarifications import validate_clarification

CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
    "MEDAGENT_LLM_API_KEY": "clarification-fixture-only",
    "MEDAGENT_LLM_MODEL": "deepseek-flash",
}


def question(turn, session):
    return {
        "schema": "execution-clarification/v1",
        "id": str(uuid4()),
        "turn_id": turn,
        "session_id": session,
        "question": "应保留哪项行为？",
        "context": "原描述与当前测试要求不同",
        "options": ["保留现有行为", "采用新行为"],
        "created_at": time.time(),
        "deadline_at": time.time() + 60,
        "status": "pending",
        "answer": None,
        "reason": None,
    }


class ClarificationRecordTests(unittest.TestCase):
    def test_invalid_records_cannot_enter_the_host_ledger(self):
        original = question(str(uuid4()), str(uuid4()))
        self.assertEqual(validate_clarification(original), original)
        for change in [
            {"status": []},
            {"answer": "invented"},
            {"options": ["one"]},
            {"options": ["a", "a"]},
            {"deadline_at": float("nan")},
            {"id": "not-an-id"},
        ]:
            with self.subTest(change=change), self.assertRaises(
                (ValueError, TypeError)
            ):
                validate_clarification({**original, **change})

    def test_answer_persists_before_send_retries_idempotently_and_cannot_cross_turns(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create(CONFIG)
                with patch.object(session, "_run", return_value=None):
                    session.start_turn("clarify", 6)
                value = question(session.turn_id, session.id)
                self.assertTrue(
                    session._accept_clarification(
                        "clarification.requested",
                        {"turn_id": session.turn_id, "clarification": value},
                    )
                )
                with patch.object(
                    session, "process", Mock(poll=lambda: None)
                ), patch.object(session, "_send") as send:
                    with patch.object(
                        manager.store, "save", side_effect=OSError("disk full")
                    ):
                        with self.assertRaises(ExecutionError):
                            session.answer_clarification(
                                value["id"], value["turn_id"], "保留现有行为"
                            )
                    send.assert_not_called()
                    self.assertEqual(value["answer"], None)
                    with self.assertRaises(ExecutionError):
                        session.answer_clarification(
                            value["id"], str(uuid4()), "保留现有行为"
                        )
                    session.answer_clarification(
                        value["id"], value["turn_id"], "保留现有行为"
                    )
                    session.answer_clarification(
                        value["id"], value["turn_id"], "保留现有行为"
                    )
                    self.assertEqual(send.call_count, 1)
                    saved = manager.store.read(session.id)["clarifications"][0]
                    self.assertEqual(saved["status"], "submitted")
                    self.assertEqual(saved["answer"], "保留现有行为")
            finally:
                manager.close()

    def test_offline_restore_marks_unanswered_questions_interrupted(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(CONFIG)
            with patch.object(session, "_run", return_value=None):
                session.start_turn("clarify", 6)
            value = question(session.turn_id, session.id)
            session._accept_clarification(
                "clarification.requested",
                {"turn_id": session.turn_id, "clarification": value},
            )
            session.persist(strict=True)
            identity = session.id
            manager.close()
            manager = ExecutionManager(root=Path(raw))
            try:
                restored = manager.open(identity, {})
                self.assertIsNone(restored.process)
                self.assertEqual(restored.clarifications[0]["status"], "interrupted")
                with self.assertRaises(ExecutionError):
                    restored.answer_clarification(value["id"], value["turn_id"], "old")
            finally:
                manager.close()


def respond(handler, step, tool):
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"question-{step}",
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
            "content_block": {"type": "tool_use", "id": f"question-{step}", **tool}
            if tool
            else {"type": "text", "text": "已依据真实回答完成。"},
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
        "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real SDK")
class ClarificationSdkTests(unittest.TestCase):
    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("Condition not reached before deadline")

    def test_root_child_and_registered_calculation_use_real_answers_in_same_turn(self):
        requests, issues = [], []
        root_calls, child_calls = [0], [0]
        owner = self

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                tool = None
                try:
                    serialized = json.dumps(
                        body.get("messages", []), ensure_ascii=False
                    )
                    if "ROOT_CLARIFICATION_FIXTURE" in serialized:
                        step = root_calls[0]
                        root_calls[0] += 1
                        if step == 0:
                            tool = {
                                "name": "request_clarification",
                                "input": {
                                    "question": "应按哪个需求修复？",
                                    "context": "描述与验收行为冲突",
                                    "options": ["保留现有行为", "按原描述改变"],
                                },
                            }
                        elif step == 1:
                            owner.assertIn("保留现有行为", serialized)
                            tool = {
                                "name": "request_clarification",
                                "input": {"question": "请提供需要换算的质量和原单位。"},
                            }
                        elif step == 2:
                            owner.assertIn("1.25 kg", serialized)
                            tool = {
                                "name": "medical_calculate",
                                "input": {
                                    "capability_id": "calculate.units",
                                    "input": {
                                        "value": "1.25",
                                        "from_unit": "kg",
                                        "to_unit": "g",
                                    },
                                    "evidence": "1.25 kg",
                                },
                            }
                        elif step == 3:
                            tool = {
                                "name": "subagent",
                                "input": {
                                    "description": "Review chosen format",
                                    "prompt": "Ask the user to choose a format using request_clarification. After the actual answer, create child.txt with reviewed.",
                                    "run_in_background": False,
                                },
                            }
                        elif step == 4:
                            tool = {
                                "name": "bash",
                                "input": {
                                    "command": "printf preserved > result.txt",
                                    "description": "Save only after the actual answers",
                                },
                            }
                        elif step == 6:
                            tool = {
                                "name": "medical_calculate",
                                "input": {
                                    "capability_id": "calculate.units",
                                    "input": {
                                        "value": "1.25",
                                        "from_unit": "kg",
                                        "to_unit": "g",
                                    },
                                    "evidence": "1.25 kg",
                                },
                            }
                    else:
                        step = child_calls[0]
                        child_calls[0] += 1
                        if step == 0:
                            tool = {
                                "name": "request_clarification",
                                "input": {
                                    "question": "复核文件采用哪种格式？",
                                    "options": ["纯文本", "JSON"],
                                },
                            }
                        elif step == 1:
                            owner.assertIn("纯文本", serialized)
                            tool = {
                                "name": "bash",
                                "input": {
                                    "command": "printf reviewed > child.txt",
                                    "description": "Write the chosen child output",
                                },
                            }
                except Exception as exc:
                    issues.append(str(exc))
                respond(self, len(requests), tool)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                manager = ExecutionManager(root=root)
                try:
                    session = manager.create(
                        {
                            **CONFIG,
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                        }
                    )
                    session.start_turn("ROOT_CLARIFICATION_FIXTURE 明确需求后再执行。", 16, 60)
                    process = None
                    turn = session.turn_id
                    for index, answer in enumerate(["保留现有行为", "1.25 kg", "纯文本"]):
                        value = self.wait(
                            lambda: next(
                                (
                                    q
                                    for q in session.snapshot()["clarifications"]
                                    if q["status"] == "pending"
                                ),
                                None,
                            )
                        )
                        process = process or session.process
                        calls = len(requests)
                        time.sleep(0.2)
                        self.assertEqual(
                            len(requests),
                            calls,
                            "waiting root/child must not invent an answer or poll the model",
                        )
                        with self.assertRaises(ExecutionBusy):
                            session.start_turn("must not replace a live question")
                        with self.assertRaises(ExecutionBusy):
                            manager.release(session.id)
                        session.answer_clarification(value["id"], turn, answer)
                        session.answer_clarification(value["id"], turn, answer)
                    final = self.wait(
                        lambda: (s if s["status"] == "completed" else None)
                        if (s := session.snapshot())
                        else None
                    )
                    self.assertEqual(issues, [])
                    self.assertIs(session.process, process)
                    self.assertEqual(len(session.turns), 1)
                    self.assertEqual(session.turn_id, turn)
                    self.assertEqual(root_calls[0], 6)
                    self.assertEqual(child_calls[0], 3)
                    self.assertEqual(len(requests), 9)
                    self.assertEqual(
                        (session.directory / "result.txt").read_text(), "preserved"
                    )
                    self.assertEqual(
                        [q["status"] for q in final["clarifications"]], ["answered"] * 3
                    )
                    self.assertEqual(
                        session.calculations[0]["evidence"]["source_turn_id"],
                        session.clarifications[1]["id"],
                    )
                    self.assertEqual(session.calculations[0]["data"]["value"], "1250")
                    identity = session.id
                    manager.close()
                    manager = ExecutionManager(root=root)
                    restored = manager.open(identity, {})
                    self.assertEqual(restored.clarifications, session.clarifications)
                    self.assertIsNone(restored.process)
                    self.assertEqual(len(requests), 9)
                    manager.open(
                        identity,
                        {
                            **CONFIG,
                            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                        },
                    )
                    restored.start_turn(
                        "ROOT_CLARIFICATION_FIXTURE 沿用之前真实回答里的质量再换算一次，不重新给出输入。", 6
                    )
                    self.wait(lambda: restored.snapshot()["status"] == "completed")
                    self.assertEqual(len(requests), 11)
                    self.assertEqual(restored.calculations[-1]["data"]["value"], "1250")
                    self.assertEqual(
                        restored.calculations[-1]["evidence"]["source_turn_id"],
                        restored.clarifications[1]["id"],
                    )
                finally:
                    manager.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_deadline_and_cancel_invalidate_questions_without_extra_model_calls(self):
        for stop in ["deadline", "cancel"]:
            with self.subTest(stop=stop):
                requests = []

                class Model(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_POST(self):
                        requests.append(
                            json.loads(
                                self.rfile.read(int(self.headers["Content-Length"]))
                            )
                        )
                        respond(
                            self,
                            len(requests),
                            {
                                "name": "request_clarification",
                                "input": {"question": "请补充必要输入。"},
                            }
                            if len(requests) == 1
                            else None,
                        )

                server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    with tempfile.TemporaryDirectory() as raw:
                        manager = ExecutionManager(root=Path(raw))
                        try:
                            session = manager.create(
                                {
                                    **CONFIG,
                                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                                }
                            )
                            session.start_turn(
                                "Ask and wait", 8, 4 if stop == "deadline" else 30
                            )
                            value = self.wait(
                                lambda: next(
                                    iter(session.snapshot()["clarifications"]), None
                                )
                            )
                            if stop == "cancel":
                                session.cancel()
                            self.wait(
                                lambda: session.snapshot()["status"] == "cancelled", 10
                            )
                            self.assertEqual(
                                session.clarifications[0]["status"], "closed"
                            )
                            with self.assertRaises(ExecutionError):
                                session.answer_clarification(
                                    value["id"], value["turn_id"], "late"
                                )
                            self.assertEqual(len(requests), 1)
                            process = session.process
                            session.start_turn(
                                "Continue same session after invalidating the old question",
                                8,
                            )
                            self.wait(
                                lambda: session.snapshot()["status"] == "completed"
                            )
                            self.assertIs(session.process, process)
                            self.assertEqual(len(requests), 2)
                        finally:
                            manager.close()
                finally:
                    server.shutdown()
                    server.server_close()
