"""Directory coverage, stable pagination and path isolation against actual filesystem data."""
import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from medagent.execution import ExecutionManager, ExecutionError
from medagent.execution_files import directory_page, DirectoryChanged
from medagent.runtime import build_runtime
from medagent.server import MedicalAgentServer, Handler

CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
    "MEDAGENT_LLM_API_KEY": "local-directory-fixture",
    "MEDAGENT_LLM_MODEL": "fixture",
}


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.manager = ExecutionManager(root=Path(self.temporary.name) / "records")
        self.addCleanup(self.manager.close)
        self.session = self.manager.create(CONFIG)
        self.root = self.session.directory

    def test_all_275_files_and_folders_paginate_in_order_without_duplicates(self):
        for i in range(275):
            (self.root / f"file-{i:03}.txt").write_text(str(i))
        for name in ["alpha", "目录"]:
            (self.root / name).mkdir()
        entries = []
        cursor = None
        sizes = []
        while True:
            page = self.session.list_directory(cursor=cursor)
            self.assertTrue(page["scan_complete"])
            self.assertEqual(page["total_entries"], 277)
            entries.extend(page["entries"])
            sizes.append(len(page["entries"]))
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(sizes, [100, 100, 77])
        self.assertEqual(len({e["path"] for e in entries}), 277)
        self.assertEqual([e["name"] for e in entries[:2]], ["alpha", "目录"])
        self.assertEqual(
            [e["name"] for e in entries[2:]], [f"file-{i:03}.txt" for i in range(275)]
        )
        self.assertFalse(self.session.snapshot()["artifact_scan"]["complete"])

    def test_deep_directory_file_is_reachable_and_overview_reports_its_limit(self):
        path = self.root
        for i in range(13):
            path = path / f"目录{i}"
            path.mkdir()
        (path / "result.csv").write_text("sample,value\nA,1\n")
        relative = str(path.relative_to(self.root))
        overview = self.session.snapshot()
        self.assertEqual(overview["artifacts"], [])
        self.assertIn("depth_limit", overview["artifact_scan"]["reasons"])
        page = self.session.list_directory(relative)
        self.assertTrue(page["scan_complete"])
        self.assertEqual(page["entries"][0]["path"], relative + "/result.csv")
        self.assertEqual(
            self.session.preview_file(relative + "/result.csv")["kind"], "table"
        )
        self.assertIsNone(self.session.process)

    def test_cursor_rejects_changed_names_but_allows_file_content_updates(self):
        for name in ["a.txt", "b.txt", "c.txt"]:
            (self.root / name).write_text("old")
        first = self.session.list_directory(limit=1)
        (self.root / "b.txt").write_text("new contents")
        second = self.session.list_directory(cursor=first["next_cursor"], limit=1)
        self.assertEqual(second["entries"][0]["name"], "b.txt")
        self.assertEqual(second["entries"][0]["size"], len("new contents"))
        (self.root / "new.txt").write_text("new")
        with self.assertRaises(DirectoryChanged):
            self.session.list_directory(cursor=first["next_cursor"])

    def test_hidden_caches_links_and_special_files_are_explicit(self):
        (self.root / ".private").write_text("synthetic")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules/pkg").mkdir()
        (self.root / "node_modules/pkg/index.js").write_text("1")
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "secret").write_text("outside")
        (self.root / "shortcut").symlink_to(outside, target_is_directory=True)
        os.mkfifo(self.root / "pipe")
        page = self.session.list_directory()
        self.assertEqual({e["name"] for e in page["entries"]}, {"shortcut", "pipe"})
        self.assertEqual({e["kind"] for e in page["entries"]}, {"link", "other"})
        visible = self.session.list_directory(include_hidden=True)
        self.assertEqual(visible["total_entries"], 4)
        self.assertEqual(
            self.session.list_directory("node_modules/pkg")["entries"][0]["name"],
            "index.js",
        )
        with self.assertRaises(ExecutionError):
            self.session.list_directory("shortcut")

    def test_scanning_limits_return_unknown_totals_instead_of_false_completeness(self):
        for i in range(20):
            (self.root / f"{i:02}.txt").write_text("x")
        root = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            page = directory_page(root, max_entries=5)
            self.assertFalse(page["scan_complete"])
            self.assertIsNone(page["total_entries"])
            self.assertEqual(page["scanned_entries"], 5)
            self.assertIn("entry_limit", page["reasons"])
            timed = directory_page(root, max_seconds=-1)
            self.assertFalse(timed["scan_complete"])
            self.assertIn("time_limit", timed["reasons"])
        finally:
            os.close(root)

    def test_unsupported_utf8_name_does_not_break_json_or_hide_coverage_status(self):
        raw = os.fsencode(self.root) + b"/bad-\xff.txt"
        try:
            fd = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o600)
        except OSError as exc:
            self.skipTest(f"filesystem does not allow undecodable names: {exc}")
        os.close(fd)
        (self.root / "good.txt").write_text("ok")
        page = self.session.list_directory()
        self.assertFalse(page["scan_complete"])
        self.assertIn("unsupported_name", page["reasons"])
        self.assertEqual(page["entries"][0]["name"], "good.txt")
        json.dumps(page, ensure_ascii=False).encode("utf-8")
        self.assertIn(
            "unsupported_name", self.session.snapshot()["artifact_scan"]["reasons"]
        )
        with self.assertRaises(ExecutionError):
            self.session.preview_file(os.fsdecode(b"bad-\xff.txt"))

    def test_real_default_scan_bound_still_allows_direct_file_access(self):
        for i in range(10005):
            (self.root / f"item-{i:05}.txt").touch()
        target = self.root / "item-10004.txt"
        target.write_text("reachable directly")
        page = self.session.list_directory()
        self.assertFalse(page["scan_complete"])
        self.assertIsNone(page["total_entries"])
        self.assertLessEqual(page["scanned_entries"], 10000)
        self.assertTrue(set(page["reasons"]) & {"entry_limit", "time_limit"})
        self.assertEqual(
            self.session.preview_file(target.name)["text"], "reachable directly"
        )

    def test_invalid_paths_and_cursors_do_not_leak_directory_descriptors(self):
        fd_directory = Path("/proc/self/fd")
        if not fd_directory.is_dir():
            fd_directory = Path("/dev/fd")
        if not fd_directory.is_dir():
            self.skipTest("platform does not expose process file descriptors")
        before = len(list(fd_directory.iterdir()))
        for path in [
            "../outside",
            "/etc",
            "x/../y",
            "x/./y",
            "x//y",
            "x\\y",
            None,
            "\udcff",
        ]:
            with self.subTest(path=repr(path)), self.assertRaises(
                (ExecutionError, ValueError)
            ):
                self.session.list_directory(path)
        for cursor in [
            "broken",
            "x" * 5000,
            base64.urlsafe_b64encode(b'{"revision":12,"after":[]}').decode(),
        ]:
            with self.assertRaises(ExecutionError):
                self.session.list_directory(cursor=cursor)
        self.assertLessEqual(len(list(fd_directory.iterdir())), before + 1)

    def test_cheap_snapshots_still_detect_missing_or_replaced_roots(self):
        with patch.object(
            self.session,
            "artifact_overview",
            side_effect=AssertionError("recursive overview must not run"),
        ):
            value = self.session.snapshot(include_artifacts=False)
            self.assertFalse(value["artifact_scan"]["performed"])
            self.assertIsNone(value["artifact_scan"]["complete"])
            moved = self.root.with_name("moved")
            self.root.rename(moved)
            self.assertTrue(
                self.session.snapshot(include_artifacts=False)["storage_error"]
            )
            self.root.mkdir()
            self.assertTrue(
                self.session.snapshot(include_artifacts=False)["storage_error"]
            )
            self.root.rmdir()
            moved.rename(self.root)
            self.assertIsNone(
                self.session.snapshot(include_artifacts=False)["storage_error"]
            )

    def test_directory_http_keeps_auth_origin_and_changed_cursor_status(self):
        runtime = build_runtime(backend="demo")
        context = runtime.start()
        server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
            access_token="directory-fixture-token",
        )
        server.execution.close()
        server.execution = self.manager
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        for name in ["a.txt", "b.txt"]:
            (self.root / name).write_text("data")
        base = f"http://127.0.0.1:{server.server_port}/api/execution/{self.session.id}"
        headers = {"X-MedAgent-Token": "directory-fixture-token"}
        with urlopen(Request(base + "/directory?limit=1", headers=headers)) as response:
            page = json.load(response)
        self.assertEqual(len(page["entries"]), 1)
        (self.root / "added").mkdir()
        cases = [
            (Request(base + "/directory"), 401),
            (
                Request(
                    base + "/directory",
                    headers={**headers, "Origin": "https://foreign.invalid"},
                ),
                403,
            ),
            (Request(base + "/directory?limit=0", headers=headers), 400),
            (
                Request(
                    base + "/directory?" + urlencode({"cursor": page["next_cursor"]}),
                    headers=headers,
                ),
                409,
            ),
        ]
        for request, status in cases:
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, status)
        with patch.object(
            self.session, "artifact_overview", side_effect=AssertionError("no overview")
        ):
            with urlopen(
                Request(base + "?include_artifacts=0", headers=headers)
            ) as response:
                self.assertFalse(json.load(response)["artifact_scan"]["performed"])
