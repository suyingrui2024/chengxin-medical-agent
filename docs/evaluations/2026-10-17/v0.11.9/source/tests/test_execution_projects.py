"""Explicit projects edit in place while runtime records remain host-owned."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from medagent.execution import ExecutionManager, ExecutionBusy, ExecutionError
from medagent.execution_projects import project_path

CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
    "MEDAGENT_LLM_API_KEY": "project-fixture-only",
    "MEDAGENT_LLM_MODEL": "deepseek-flash",
}


class ProjectBindingTests(unittest.TestCase):
    def test_project_is_bound_in_place_without_a_model_request_and_cold_opens_offline(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "project"
            directory.mkdir()
            (directory / "keep.txt").write_text("original")
            manager = ExecutionManager(root=root / "records")
            try:
                session = manager.attach_project(directory, CONFIG)
                identity = session.id
                self.assertEqual(session.directory, directory)
                self.assertEqual(session.snapshot()["workspace_kind"], "project")
                self.assertIsNone(session.process)
                self.assertEqual(list(directory.iterdir()), [directory / "keep.txt"])
                self.assertIs(manager.attach_project(directory, CONFIG), session)
                self.assertFalse((directory / ".state").exists())
                self.assertEqual(
                    session.state_directory, root / "records/.state" / identity
                )
            finally:
                manager.close()
            manager = ExecutionManager(root=root / "records")
            try:
                restored = manager.attach_project(directory, {})
                self.assertEqual(restored.id, identity)
                self.assertFalse(restored.execution_configured)
                self.assertEqual(restored.read_file("keep.txt")["text"], "original")
                self.assertIsNone(restored.process)
            finally:
                manager.close()

    def test_project_diff_uses_private_store_not_project_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "project"
            directory.mkdir()
            (directory / "main.py").write_text("value = 1\n")
            manager = ExecutionManager(root=root / "records")
            try:
                session = manager.attach_project(directory, CONFIG)
                with patch.object(session, "_run", return_value=None):
                    session.start_turn("Review real changes", 6)
                (directory / "main.py").write_text("value = 2\n")
                changes = session.changes(path="main.py")
                self.assertEqual(changes["counts"]["modified"], 1)
                self.assertIn("-value = 1", changes["detail"]["diff"])
                self.assertTrue(
                    (
                        root
                        / "records/.index/changes"
                        / session.id
                        / (session.turn_id + ".json")
                    ).is_file()
                )
                self.assertFalse((root / ".index").exists())
                self.assertFalse((directory / ".index").exists())
                record = json.loads(
                    (root / "records/.index" / (session.id + ".json")).read_text()
                )
                self.assertEqual(record["project_path"], str(directory))
            finally:
                manager.close()

    def test_same_inode_cannot_be_owned_through_other_indices_or_aliases(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "project"
            directory.mkdir()
            alias = root / "alias"
            alias.symlink_to(directory, target_is_directory=True)
            first = ExecutionManager(root=root / "records-one")
            second = ExecutionManager(root=root / "records-two")
            try:
                session = first.attach_project(directory, CONFIG)
                self.assertIs(first.attach_project(alias, CONFIG), session)
                with self.assertRaises(ExecutionBusy):
                    second.attach_project(alias, CONFIG)
                first.release(session.id)
                opened = second.attach_project(directory, CONFIG)
                self.assertEqual(opened.directory, directory)
            finally:
                first.close()
                second.close()

    def test_protected_missing_and_non_directory_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manager = ExecutionManager(root=root / "records")
            (root / "file.txt").write_text("retained")
            (root / "alias").symlink_to(manager.source_root, target_is_directory=True)
            try:
                for path in [
                    Path("/"),
                    Path.home(),
                    manager.source_root,
                    manager.source_root / "src",
                    manager.root,
                    root,
                    root / "alias",
                    root / "file.txt",
                    "relative/path",
                    None,
                    "a\x00b",
                ]:
                    with self.subTest(path=str(path)), self.assertRaises(
                        (ValueError, OSError)
                    ):
                        manager.attach_project(path, CONFIG)
                with self.assertRaises(OSError):
                    manager.attach_project(root / "missing", CONFIG)
                self.assertEqual(manager.sessions, {})
                self.assertEqual((root / "file.txt").read_text(), "retained")
            finally:
                manager.close()

    def test_replaced_and_tampered_projects_do_not_resume_or_create_a_substitute(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "project"
            directory.mkdir()
            (directory / "main.py").write_text("original")
            manager = ExecutionManager(root=root / "records")
            session = manager.attach_project(directory, CONFIG)
            identity = session.id
            directory.rename(root / "moved")
            directory.mkdir()
            try:
                with self.assertRaises(ExecutionError):
                    manager.attach_project(directory, CONFIG)
            finally:
                manager.close()
            manager = ExecutionManager(root=root / "records")
            try:
                with self.assertRaises((ExecutionError, OSError)):
                    manager.open(identity, {})
                self.assertEqual(list(directory.iterdir()), [])
                record = manager.store.read(identity)
                record["project_path"] = str(manager.source_root)
                manager.store.save(identity, record)
                with self.assertRaises(ValueError):
                    manager.open(identity, CONFIG)
            finally:
                manager.close()

    def test_failed_persistence_releases_project_without_deleting_user_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "project"
            directory.mkdir()
            (directory / "keep.txt").write_text("original")
            manager = ExecutionManager(root=root / "records")
            try:
                with patch.object(
                    manager.store, "save", side_effect=OSError("disk full")
                ):
                    with self.assertRaises(ExecutionError):
                        manager.attach_project(directory, CONFIG)
                self.assertEqual(manager.sessions, {})
                self.assertEqual((directory / "keep.txt").read_text(), "original")
                session = manager.attach_project(directory, CONFIG)
                self.assertIsNone(session.process)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
