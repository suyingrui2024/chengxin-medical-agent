"""Cold SDK resume must preserve actual model history, not just saved files."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager, ExecutionError, ExecutionBusy
from unittest.mock import patch


CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
    "MEDAGENT_LLM_API_KEY": "configuration-secret-never-persist",
    "MEDAGENT_LLM_MODEL": "deepseek-flash",
}


class WorkspaceIndexTests(unittest.TestCase):
    def test_configured_secret_is_redacted_before_title_or_transcript_is_shortened(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(CONFIG)
            try:
                with patch.object(session, "_run", return_value=None):
                    session.start_turn(
                        CONFIG["MEDAGENT_LLM_API_KEY"]
                        + " is synthetic; retain a note.",
                        6,
                    )
                raw_record = (Path(raw) / ".index" / (session.id + ".json")).read_text()
                self.assertNotIn(CONFIG["MEDAGENT_LLM_API_KEY"], raw_record)
                self.assertNotIn(CONFIG["MEDAGENT_LLM_API_KEY"][:20], raw_record)
                self.assertIn("[REDACTED]", raw_record)
            finally:
                manager.close()

    def test_offline_history_and_files_can_be_viewed_but_execution_requires_original_route(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            first = ExecutionManager(root=Path(raw))
            session = first.create(CONFIG)
            (session.directory / "note.txt").write_text("retained")
            identity = session.id
            first.close()
            second = ExecutionManager(root=Path(raw))
            try:
                restored = second.open(identity, {})
                self.assertIsNone(restored.process)
                self.assertEqual(restored.read_file("note.txt")["text"], "retained")
                self.assertFalse(restored.snapshot()["execution_configured"])
                with self.assertRaises(ExecutionError):
                    restored.start_turn("must not call model")
                self.assertIsNone(restored.process)
                self.assertIs(
                    second.open(
                        identity,
                        {
                            **CONFIG,
                            "MEDAGENT_LLM_BASE_URL": "https://other.invalid/anthropic",
                        },
                    ),
                    restored,
                )
                self.assertFalse(restored.execution_configured)
                second.open(
                    identity, {**CONFIG, "MEDAGENT_LLM_BASE_URL": "https://[invalid"}
                )
                self.assertFalse(restored.execution_configured)
                self.assertIsNone(restored.process)
                second.open(
                    identity, {**CONFIG, "MEDAGENT_LLM_API_KEY": "rotated-value"}
                )
                self.assertTrue(restored.execution_configured)
                self.assertIsNone(restored.process)
            finally:
                second.close()
            data = b"".join(
                p.read_bytes() for p in (Path(raw) / ".index").glob("*.json")
            )
            self.assertNotIn(CONFIG["MEDAGENT_LLM_API_KEY"].encode(), data)
            self.assertNotIn(b"rotated-value", data)

    def test_two_owners_cannot_open_same_workspace_and_release_allows_cold_reopen(self):
        with tempfile.TemporaryDirectory() as raw:
            first = ExecutionManager(root=Path(raw))
            session = first.create(CONFIG)
            second = ExecutionManager(root=Path(raw))
            try:
                with self.assertRaises(ExecutionBusy):
                    second.open(session.id, CONFIG)
                first.release(session.id)
                restored = second.open(session.id, CONFIG)
                self.assertIsNone(restored.process)
                self.assertEqual(restored.id, session.id)
                self.assertEqual(restored.directory, session.directory)
            finally:
                first.close()
                second.close()

    def test_corrupt_records_and_replaced_directories_fail_without_creating_replacements(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = ExecutionManager(root=root)
            session = first.create(CONFIG)
            first.close()
            record = root / ".index" / (session.id + ".json")
            original = record.read_bytes()
            second = ExecutionManager(root=root)
            try:
                record.write_text("{broken")
                self.assertEqual(second.catalog()[0]["status"], "unavailable")
                with self.assertRaises(ExecutionError):
                    second.open(session.id, CONFIG)
                record.write_bytes(original)
                moved = root / "moved"
                session.directory.rename(moved)
                session.directory.mkdir()
                with self.assertRaises(ExecutionError):
                    second.open(session.id, CONFIG)
                session.directory.rmdir()
                moved.rename(session.directory)
                self.assertEqual(second.open(session.id, CONFIG).id, session.id)
            finally:
                second.close()

    def test_interrupted_metadata_is_preserved_as_interrupted_and_never_autostarts(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = ExecutionManager(root=root)
            session = first.create(CONFIG)
            identity = session.id
            first.close()
            path = root / ".index" / (identity + ".json")
            record = json.loads(path.read_text())
            record["status"] = "running"
            record["turn_count"] = 1
            from uuid import uuid4

            record["turns"] = [
                {
                    "id": str(uuid4()),
                    "text": "unfinished task",
                    "status": "running",
                    "max_calls": 6,
                    "model_calls": 0,
                }
            ]
            path.write_text(json.dumps(record))
            second = ExecutionManager(root=root)
            try:
                restored = second.open(identity, CONFIG)
                self.assertEqual(restored.snapshot()["status"], "interrupted")
                self.assertEqual(
                    restored.snapshot()["turns"][0]["status"], "interrupted"
                )
                self.assertIsNone(restored.process)
                self.assertFalse(
                    any(e["type"] == "model.request" for e in restored.events)
                )
            finally:
                second.close()

    def test_unwritable_history_refuses_new_task_and_does_not_leave_a_busy_state(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(CONFIG)
            try:
                with patch.object(
                    manager.store, "save", side_effect=OSError("injected disk full")
                ):
                    with self.assertRaises(ExecutionError):
                        session.start_turn("do not run when durable submission failed")
                    self.assertEqual(session.status, "created")
                    self.assertEqual(session.turns, [])
                    self.assertIsNone(session.process)
            finally:
                manager.close()

    def test_index_links_and_invalid_identifiers_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manager = ExecutionManager(root=root)
            session = manager.create(CONFIG)
            manager.close()
            record = root / ".index" / (session.id + ".json")
            record.unlink()
            record.symlink_to(root / "outside")
            second = ExecutionManager(root=root)
            try:
                for identity in [session.id, "../../.env", "not-an-id"]:
                    with self.assertRaises(ExecutionError):
                        second.open(identity, {})
            finally:
                second.close()


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class ColdResumeTests(unittest.TestCase):
    def test_cold_sdk_resume_preserves_context_and_files_with_rotated_credential(self):
        seen, credential = [], ["first-provider-fixture"]
        owner = self
        marker = "amber-sparrow-731"

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.assertEqual(self.headers.get("x-api-key"), credential[0])
                seen.append(body)
                step = len(seen)
                owner.assertIn(marker, json.dumps(body["messages"]))
                content = {"type": "text", "text": "Files verified."}
                if step in {1, 3}:
                    command = (
                        "printf '%s' 'amber-sparrow-731' > original.txt"
                        if step == 1
                        else "test $(cat original.txt) = amber-sparrow-731 && printf resumed > followup.txt"
                    )
                    content = {
                        "type": "tool_use",
                        "id": f"call-{step}",
                        "name": "bash",
                        "input": {
                            "command": command,
                            "description": "Operate original durable workspace",
                        },
                    }
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"m-{step}",
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
                            "stop_reason": "tool_use" if step in {1, 3} else "end_turn"
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

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                config = {
                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                    "MEDAGENT_LLM_API_KEY": credential[0],
                    "MEDAGENT_LLM_MODEL": "deepseek-flash",
                }
                manager = ExecutionManager(root=root)
                original = manager.create(config)
                try:
                    original.start_turn(
                        "REMEMBER_ORIGINAL: "
                        + marker
                        + "; write original.txt and verify.",
                        6,
                    )
                    self.assertEqual(self.terminal(original)["status"], "completed")
                finally:
                    manager.close()
                self.assertIsNotNone(original.process.poll())
                credential[0] = "rotated-provider-fixture"
                config["MEDAGENT_LLM_API_KEY"] = credential[0]
                second = ExecutionManager(root=root)
                self.assertEqual(second.catalog()[0]["id"], original.id)
                resumed = second.open(original.id, config)
                try:
                    self.assertIsNone(resumed.process)
                    self.assertEqual(
                        resumed.snapshot()["turns"][-1]["status"], "completed"
                    )
                    resumed.start_turn(
                        "Continue the original task; verify the saved note and create followup.txt.",
                        6,
                    )
                    snapshot = self.terminal(resumed)
                    self.assertEqual(
                        snapshot["status"], "completed", snapshot.get("error")
                    )
                    self.assertNotEqual(original.process.pid, resumed.process.pid)
                    self.assertEqual(
                        (resumed.directory / "followup.txt").read_text(), "resumed"
                    )
                    self.assertEqual(len(seen), 4)
                finally:
                    second.close()
        finally:
            model.shutdown()
            model.server_close()

    def test_required_missing_history_never_silently_creates_new_session(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(
                {
                    "MEDAGENT_LLM_BASE_URL": "http://127.0.0.1:1/anthropic",
                    "MEDAGENT_LLM_API_KEY": "fixture",
                    "MEDAGENT_LLM_MODEL": "deepseek-flash",
                }
            )
            session.require_resume = True
            try:
                session.start_turn(
                    "A required history is missing; do not replace it.", 4
                )
                snapshot = self.terminal(session)
                self.assertEqual(snapshot["status"], "failed")
                self.assertFalse(
                    any(e["type"] == "model.request" for e in snapshot["events"])
                )
            finally:
                manager.close()

    def terminal(self, session):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if snapshot["status"] not in {"starting", "running", "cancelling"}:
                return snapshot
            time.sleep(0.05)
        self.fail("execution did not finish")
