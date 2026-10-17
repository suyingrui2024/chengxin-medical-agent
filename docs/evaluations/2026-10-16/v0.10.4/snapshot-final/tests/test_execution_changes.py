"""Real file observations: byte changes, hostile paths, partial scans and SDK use."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from medagent.execution import ExecutionManager
from medagent.execution_changes import ChangeTracker, ChangeError, capture, compare


class ChangeObservationTests(unittest.TestCase):
    def make_tracker(self, root):
        directory = root / "workspace"
        directory.mkdir()
        info = directory.stat()
        return ChangeTracker(
            directory,
            (info.st_dev, info.st_ino),
            root,
            str(uuid4()),
            "synthetic-provider-secret",
        )

    def test_content_changes_diff_and_offline_baseline_do_not_depend_on_git_or_mtime(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tracker = self.make_tracker(root)
            folder = tracker.directory
            (folder / "main.py").write_text("value = 1\n")
            (folder / "old.txt").write_text("remove me\n")
            (folder / "secret.py").write_text("token='synthetic-provider-secret'\n")
            (folder / ".env").write_text("synthetic-provider-secret")
            turn = str(uuid4())
            tracker.begin(turn)
            stamp = (folder / "main.py").stat()
            (folder / "main.py").write_text("value = 2\n")
            os.utime(folder / "main.py", ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            (folder / "old.txt").unlink()
            (folder / "new.txt").write_text("created\n")
            result = tracker.observe(turn, "main.py")
            self.assertEqual(
                result["counts"],
                {"added": 1, "modified": 1, "deleted": 1, "unverified": 0},
            )
            self.assertIn("-value = 1", result["detail"]["diff"])
            self.assertIn("+value = 2", result["detail"]["diff"])
            self.assertEqual(len(tracker.observe(turn, limit=1)["entries"]), 1)
            self.assertEqual(tracker.observe(turn, limit=1)["next_offset"], 1)
            self.assertEqual(
                tracker.observe(turn, limit=1, offset=2)["next_offset"], None
            )
            (folder / "main.py").write_text("value = 1\n")
            (folder / "main.py").chmod((stamp.st_mode & 0o7777) ^ 0o100)
            mode_only = next(
                e for e in tracker.observe(turn)["entries"] if e["path"] == "main.py"
            )
            self.assertEqual(mode_only["state"], "modified")
            self.assertEqual(mode_only["before_sha256"], mode_only["after_sha256"])
            self.assertNotEqual(mode_only["before_mode"], mode_only["after_mode"])
            cold = ChangeTracker(
                folder, tracker.identity, root, tracker.id, tracker.secret
            )
            self.assertEqual(cold.observe(turn)["counts"], result["counts"])
            baseline = root / ".index/changes" / tracker.id / (turn + ".json")
            self.assertNotIn(b"synthetic-provider-secret", baseline.read_bytes())
            self.assertEqual(
                json.loads(baseline.read_text())["entries"][".env"]["text"], None
            )
            self.assertEqual(baseline.stat().st_mode & 0o777, 0o600)

    def test_links_special_files_and_replaced_roots_do_not_expose_external_content(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tracker = self.make_tracker(root)
            outside = root / "external.txt"
            outside.write_text("outside-content-never-read")
            turn = str(uuid4())
            tracker.begin(turn)
            (tracker.directory / "linked.txt").symlink_to(outside)
            os.mkfifo(tracker.directory / "fifo")
            result = tracker.observe(turn, "linked.txt")
            self.assertFalse(result["detail"]["available"])
            self.assertNotIn("outside-content-never-read", json.dumps(result))
            self.assertEqual(
                {e["kind"] for e in result["entries"]}, {"symlink", "special"}
            )
            for name in ["../external.txt", "/etc/passwd", "a/../b", "a\\b"]:
                with self.assertRaises(ChangeError):
                    tracker.observe(turn, name)
            moved = root / "moved"
            tracker.directory.rename(moved)
            tracker.directory.mkdir()
            with self.assertRaises(ValueError):
                tracker.observe(turn)

    def test_partial_observations_never_fabricate_additions_deletions_or_clean_content(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            tracker = self.make_tracker(Path(raw))
            (tracker.directory / "a.txt").write_text("content")
            (tracker.directory / "b.txt").write_text("content")
            full = capture(tracker.directory, tracker.identity)
            partial = capture(tracker.directory, tracker.identity, max_files=1)
            result = compare(partial, full)
            self.assertEqual(result["counts"]["added"], 0)
            self.assertEqual(result["counts"]["unverified"], 1)
            result = compare(full, partial)
            self.assertEqual(result["counts"]["deleted"], 0)
            self.assertEqual(result["counts"]["unverified"], 1)
            bounded = capture(tracker.directory, tracker.identity, max_bytes=1)
            self.assertEqual(compare(full, bounded)["counts"]["unverified"], 2)
            self.assertFalse(bounded["content_complete"])

    def test_corrupt_baselines_and_linked_store_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tracker = self.make_tracker(root)
            (tracker.directory / "a.txt").write_text("a")
            turn = str(uuid4())
            tracker.begin(turn)
            path = root / ".index/changes" / tracker.id / (turn + ".json")
            original = path.read_text()
            for change in [
                lambda d: d.update(captured_at=float("nan")),
                lambda d: d["entries"]["a.txt"].update(kind=[]),
                lambda d: d.update(turn_id=str(uuid4())),
            ]:
                value = json.loads(original)
                change(value)
                path.write_text(json.dumps(value))
                with self.assertRaises(ChangeError):
                    tracker.observe(turn)
            path.unlink()
            outside = root / "outside.json"
            outside.write_text(original)
            path.symlink_to(outside)
            with self.assertRaises(OSError):
                tracker.observe(turn)


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class ChangeSdkTests(unittest.TestCase):
    def test_main_and_child_query_actual_changes_cleanup_and_continue_with_new_baseline(
        self,
    ):
        requests, main_calls, child_calls, failures = [], [0], [0], []
        owner = self

        def observed(body):
            found = []
            for message in body.get("messages", []):
                for item in message.get("content", []):
                    if not isinstance(item, dict) or item.get("type") != "tool_result":
                        continue
                    for block in item.get("content", []):
                        if not isinstance(block, dict) or block.get("type") != "text":
                            continue
                        try:
                            value = json.loads(block["text"])
                            if value.get("schema") == "workspace-changes/v1":
                                found.append(value)
                        except (ValueError, AttributeError):
                            pass
            return found

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                child = "ROOT_DIFF_FIXTURE" not in json.dumps(body.get("messages", []))
                tool = None
                try:
                    owner.assertIn(
                        "workspace_changes",
                        {t.get("name") for t in body.get("tools", [])},
                    )
                    if child:
                        step = child_calls[0]
                        child_calls[0] += 1
                        if step == 0:
                            tool = {
                                "name": "workspace_changes",
                                "input": {"path": "main.py"},
                            }
                        elif step == 1:
                            owner.assertIn(
                                "-value = 1", observed(body)[-1]["detail"]["diff"]
                            )
                            tool = {
                                "name": "bash",
                                "input": {
                                    "command": "rm tests/generated.tmp && printf reviewed > review.txt",
                                    "description": "Remove only the newly generated disposable test artifact",
                                },
                            }
                    else:
                        step = main_calls[0]
                        main_calls[0] += 1
                        if step == 0:
                            tool = {
                                "name": "bash",
                                "input": {
                                    "command": "printf 'value = 2\\n' > main.py; printf new > new.txt; rm old.txt; mkdir -p tests; printf temporary > tests/generated.tmp",
                                    "description": "Modify, create and delete actual files",
                                },
                            }
                        elif step == 1:
                            tool = {"name": "workspace_changes", "input": {}}
                        elif step == 2:
                            owner.assertEqual(
                                observed(body)[-1]["counts"],
                                {
                                    "added": 2,
                                    "modified": 1,
                                    "deleted": 1,
                                    "unverified": 0,
                                },
                            )
                            tool = {
                                "name": "subagent",
                                "input": {
                                    "description": "Review actual changes",
                                    "prompt": "CHILD_DIFF_REVIEW: use workspace_changes to inspect main.py, remove only tests/generated.tmp created by this task, and write review.txt with reviewed.",
                                    "run_in_background": False,
                                },
                            }
                        elif step == 3:
                            tool = {"name": "workspace_changes", "input": {}}
                        elif step == 4:
                            owner.assertNotIn(
                                "tests/generated.tmp",
                                [e["path"] for e in observed(body)[-1]["entries"]],
                            )
                        elif step == 5:
                            tool = {
                                "name": "bash",
                                "input": {
                                    "command": "printf 'value = 3\\n' > main.py",
                                    "description": "Continue the original session",
                                },
                            }
                        elif step == 6:
                            tool = {
                                "name": "workspace_changes",
                                "input": {"path": "main.py"},
                            }
                        elif step == 7:
                            owner.assertIn(
                                "-value = 2", observed(body)[-1]["detail"]["diff"]
                            )
                            owner.assertNotIn(
                                "-value = 1", observed(body)[-1]["detail"]["diff"]
                            )
                except Exception as exc:
                    failures.append(str(exc))
                    tool = None
                content = (
                    {"type": "tool_use", "id": f"changes-{len(requests)}", **tool}
                    if tool
                    else {"type": "text", "text": "Reviewed actual files."}
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"changes-{len(requests)}",
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
                            "MEDAGENT_LLM_API_KEY": "changes-fixture-only",
                            "MEDAGENT_LLM_MODEL": "deepseek-flash",
                        }
                    )
                    (session.directory / "main.py").write_text("value = 1\n")
                    (session.directory / "old.txt").write_text("remove")
                    (session.directory / "keep.txt").write_text("unchanged")
                    session.start_turn(
                        "ROOT_DIFF_FIXTURE: edit files and delegate an actual change review.",
                        16,
                    )
                    self.assertEqual(self.wait(session)["status"], "completed")
                    self.assertEqual(failures, [])
                    self.assertEqual((main_calls[0], child_calls[0]), (5, 3))
                    self.assertEqual(
                        session.changes()["counts"],
                        {"added": 2, "modified": 1, "deleted": 1, "unverified": 0},
                    )
                    self.assertEqual(
                        (session.directory / "keep.txt").read_text(), "unchanged"
                    )
                    process = session.process
                    session.start_turn(
                        "ROOT_DIFF_FIXTURE follow-up: change main.py again and inspect this turn only.",
                        6,
                    )
                    self.assertEqual(self.wait(session)["status"], "completed")
                    self.assertEqual(failures, [])
                    self.assertIs(session.process, process)
                    self.assertEqual(session.changes()["counts"]["modified"], 1)
                    identity = session.id
                finally:
                    manager.close()
                cold = ExecutionManager(root=Path(raw))
                try:
                    restored = cold.open(identity, {})
                    self.assertIn(
                        "-value = 2", restored.changes("main.py")["detail"]["diff"]
                    )
                    self.assertEqual(len(requests), 11)
                    self.assertIsNone(restored.process)
                finally:
                    cold.close()
        finally:
            server.shutdown()
            server.server_close()

    def wait(self, session):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if snapshot["status"] not in {"starting", "running", "cancelling"}:
                return snapshot
            time.sleep(0.03)
        self.fail("Actual SDK change review did not settle")
