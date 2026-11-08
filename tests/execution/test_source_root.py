"""Runtime roots must locate both source checkouts and packaged app resources."""

from pathlib import Path

from medagent.execution import ExecutionManager


def test_default_root_contains_the_actual_worker_and_locked_dependencies(tmp_path):
    manager = ExecutionManager(root=tmp_path / "workspaces")
    try:
        root = manager.source_root
        assert root == Path(__file__).resolve().parents[2]
        assert (root / "integrations/dsh/execution-worker.mjs").is_file()
        assert (root / "package-lock.json").is_file()
    finally:
        manager.close()


def test_explicit_application_root_is_preserved(tmp_path):
    app = tmp_path / "resources" / "app"
    manager = ExecutionManager(root=tmp_path / "workspaces", source_root=app)
    try:
        assert manager.source_root == app
    finally:
        manager.close()
