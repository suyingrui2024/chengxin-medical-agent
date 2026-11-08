"""Artifact services work without a session and cannot adopt another workspace."""

from dataclasses import FrozenInstanceError

import pytest

from medagent.execution.artifact_access import WorkspaceArtifactAccess
from medagent.execution.errors import ExecutionError


def access_for(directory):
    info = directory.stat()
    return WorkspaceArtifactAccess(directory, (info.st_dev, info.st_ino))


def test_files_tables_and_hash_checks_need_no_model_or_session(tmp_path):
    (tmp_path / "result.csv").write_text("name,value\nweight,1250\n", encoding="utf-8")
    access = access_for(tmp_path)
    preview = access.preview_file("result.csv")
    assert preview["kind"] == "table"
    assert preview["rows"][1] == ["weight", "1250"]
    assert access.artifact_status("result.csv", preview["sha256"])["matches"]
    (tmp_path / "result.csv").write_text("name,value\nweight,2500\n", encoding="utf-8")
    assert not access.artifact_status("result.csv", preview["sha256"])["matches"]
    assert access.read_file("result.csv")["text"].endswith("2500\n")


def test_scope_is_immutable_and_a_replacement_root_is_rejected(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    access = access_for(root)
    with pytest.raises(FrozenInstanceError):
        access.directory = tmp_path
    root.rename(tmp_path / "original")
    root.mkdir()
    (root / "foreign.txt").write_text("must not be adopted", encoding="utf-8")
    for action in [access.overview, access.list_directory, lambda: access.read_file("foreign.txt")]:
        with pytest.raises(ExecutionError):
            action()


def test_scan_excludes_links_and_caches_and_reports_partial_scope(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("must not leak", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)
    (root / ".hidden").write_text("hidden", encoding="utf-8")
    cache = root / "node_modules"
    cache.mkdir()
    (cache / "ignored.txt").write_text("cached", encoding="utf-8")
    for index in range(205):
        (root / f"file-{index:03}.txt").write_text("ok", encoding="utf-8")
    overview = access_for(root).overview()
    assert len(overview["entries"]) == 200
    assert not overview["scan"]["complete"]
    assert "entry_limit" in overview["scan"]["reasons"]
    assert all(item["path"].startswith("file-") for item in overview["entries"])
