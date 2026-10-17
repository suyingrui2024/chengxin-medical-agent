"""File-version evidence observes actual bytes, bounds and explicit input scope."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from medagent.execution_checkpoints import checkpoint, normalize_paths


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "workspace"
        self.root.mkdir()
        info = self.root.stat()
        self.identity = [info.st_dev, info.st_ino]

    def observe(self, paths=None, **limits):
        return checkpoint(self.root, self.identity, paths or ["."], **limits)

    def test_content_change_is_detected_despite_restored_timestamp_and_same_size(self):
        source = self.root / "code.py"
        source.write_text("answer=1")
        timestamp = source.stat().st_mtime_ns
        first = self.observe()
        self.assertTrue(first["complete"])
        source.write_text("answer=2")
        os.utime(source, ns=(timestamp, timestamp))
        self.assertNotEqual(first["sha256"], self.observe()["sha256"])
        source.write_text("answer=1")
        self.assertEqual(first["sha256"], self.observe()["sha256"])

    def test_scoped_inputs_ignore_reports_but_track_new_and_deleted_code(self):
        (self.root / "src").mkdir()
        source = self.root / "src/main.py"
        source.write_text("pass")
        first = self.observe(["src"])
        (self.root / "report.txt").write_text("report")
        self.assertEqual(first["sha256"], self.observe(["src"])["sha256"])
        extra = self.root / "src/extra.py"
        extra.write_text("pass")
        self.assertNotEqual(first["sha256"], self.observe(["src"])["sha256"])
        extra.unlink()
        self.assertEqual(first["sha256"], self.observe(["src"])["sha256"])
        source.unlink()
        self.assertNotEqual(first["sha256"], self.observe(["src"])["sha256"])

    def test_explicit_dependency_path_overrides_default_cache_directory_exclusion(self):
        package = self.root / "node_modules/pkg"
        package.mkdir(parents=True)
        source = package / "index.js"
        source.write_text("one")
        whole, explicit = self.observe(), self.observe(["node_modules/pkg"])
        source.write_text("two")
        self.assertEqual(whole["sha256"], self.observe()["sha256"])
        self.assertNotEqual(
            explicit["sha256"], self.observe(["node_modules/pkg"])["sha256"]
        )

    def test_incomplete_scan_has_no_proof_digest_and_never_returns_file_text(self):
        secret = "synthetic-private-value-for-checkpoint-test"
        (self.root / ".env").write_text(secret)
        (self.root / "large.txt").write_text("large")
        small = self.observe(max_file_bytes=4)
        self.assertFalse(small["complete"])
        self.assertIsNone(small["sha256"])
        self.assertNotIn(secret, json.dumps(self.observe()))

    def test_symbolic_link_input_is_unknown_and_outside_target_is_never_followed(self):
        target = Path(self.temporary.name) / "outside"
        target.write_text("outside source")
        (self.root / "linked").symlink_to(target)
        scoped = self.observe(["linked"])
        self.assertFalse(scoped["complete"])
        self.assertIsNone(scoped["sha256"])
        self.assertFalse(scoped["symlink_targets_followed"])
        self.assertNotIn("outside source", json.dumps(scoped))

    def test_replaced_workspace_and_escape_paths_are_rejected(self):
        self.root.rename(self.root.with_name("old"))
        self.root.mkdir()
        with self.assertRaises(ValueError):
            self.observe()
        for paths in [
            [],
            ["../outside"],
            ["/etc"],
            ["a\\b"],
            [True],
            [".", "../outside"],
        ]:
            with self.subTest(paths=paths):
                with self.assertRaises(ValueError):
                    normalize_paths(paths)
        self.assertEqual(
            normalize_paths(["src/a.py", "src", "tests"]), ["src", "tests"]
        )
