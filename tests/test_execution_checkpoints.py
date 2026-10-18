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

    def test_native_case_alias_tracks_actual_file_bytes(self):
        source = self.root / "source.txt"
        source.write_text("before")
        if not (self.root / "SOURCE.TXT").exists():
            self.skipTest("This filesystem treats these names as different paths")
        before = self.observe(["SOURCE.TXT"])
        self.assertTrue(before["complete"])
        self.assertEqual(before["files"], 1)
        source.write_text("after!")
        after = self.observe(["SOURCE.TXT"])
        self.assertTrue(after["complete"])
        self.assertNotEqual(before["sha256"], after["sha256"])

    def test_native_case_and_unicode_aliases_track_the_actual_input(self):
        import unicodedata

        folder = self.root / "Source"
        folder.mkdir()
        source = folder / "Étude.py"
        source.write_text("before")
        alias = unicodedata.normalize("NFD", "sOURCE/éTUDE.PY")
        if not (self.root / alias).exists():
            self.skipTest("This filesystem does not provide these native aliases")
        before = self.observe([alias])
        self.assertTrue(before["complete"])
        self.assertEqual(before["files"], 1)
        source.write_text("after!")
        after = self.observe([alias])
        self.assertTrue(after["complete"])
        self.assertNotEqual(before["sha256"], after["sha256"])
        self.assertNotIn("after!", json.dumps(after))

    def test_explicit_empty_and_missing_scope_states_are_observed(self):
        path = self.root / "inputs"
        missing = self.observe(["inputs"])
        path.mkdir()
        empty = self.observe(["inputs"])
        self.assertTrue(missing["complete"])
        self.assertTrue(empty["complete"])
        self.assertNotEqual(missing["sha256"], empty["sha256"])

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

    def test_root_scope_preserves_explicit_cache_input_without_scanning_its_neighbors(
        self,
    ):
        package = self.root / "node_modules/pkg"
        package.mkdir(parents=True)
        dependency = package / "index.js"
        dependency.write_text("old")
        neighbor = self.root / "node_modules/other"
        neighbor.mkdir()
        (neighbor / "large.js").write_text("x" * 100)
        paths = [".", "node_modules/pkg"]
        first = self.observe(paths, max_file_bytes=10)
        self.assertTrue(first["complete"])
        self.assertEqual(first["paths"], paths)
        (neighbor / "large.js").write_text("y" * 100)
        self.assertEqual(
            first["sha256"], self.observe(paths, max_file_bytes=10)["sha256"]
        )
        dependency.write_text("new")
        self.assertNotEqual(
            first["sha256"], self.observe(paths, max_file_bytes=10)["sha256"]
        )

    def test_parent_scope_retains_explicit_nested_cache_and_regular_git_marker(self):
        source = self.root / "src"
        source.mkdir()
        (source / "main.py").write_text("pass")
        cache = source / "__pycache__"
        cache.mkdir()
        evidence = cache / "evidence.json"
        evidence.write_text("old")
        marker = self.root / ".git"
        marker.write_text("gitdir: synthetic")
        self.assertEqual(
            normalize_paths(["src", "src/__pycache__/evidence.json"]),
            ["src", "src/__pycache__/evidence.json"],
        )
        paths = ["src", "src/__pycache__/evidence.json"]
        first = self.observe(paths)
        evidence.write_text("new")
        self.assertNotEqual(first["sha256"], self.observe(paths)["sha256"])
        all_files = self.observe()
        marker.write_text("gitdir: changed")
        self.assertNotEqual(all_files["sha256"], self.observe()["sha256"])

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
