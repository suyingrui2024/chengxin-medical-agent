"""Frozen inputs remain recoverable without overwriting local work."""

import hashlib
import json

import pytest

from scripts.maintenance import evaluation_archive as archive


@pytest.fixture
def archived_file(tmp_path, monkeypatch):
    root = tmp_path / "project"
    manifest = root / "docs/evaluations/archive-manifest.json"
    manifest.parent.mkdir(parents=True)
    name = "docs/evaluations/fixture/source/example.py"
    data = b"print('frozen source')\n"
    manifest.write_text(
        json.dumps(
            {
                "commit": "fixture",
                "files": [
                    {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                ],
            }
        )
    )
    source = tmp_path / "_archives/project/fixture/files" / name
    source.parent.mkdir(parents=True)
    source.write_bytes(data)
    monkeypatch.setattr(archive, "ROOT", root)
    monkeypatch.setattr(archive, "MANIFEST", manifest)
    return root, name, source, data


def test_restores_and_verifies_local_copy(archived_file):
    root, name, _, data = archived_file
    archive.restore(name)
    assert (root / name).read_bytes() == data
    archive.restore(name)  # Identical recovery is idempotent.
    archive.verify()


def test_corrupt_copy_never_reaches_destination(archived_file):
    root, name, source, _ = archived_file
    source.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum"):
        archive.restore(name)
    assert not (root / name).exists()


def test_local_edits_are_not_overwritten(archived_file):
    root, name, _, _ = archived_file
    destination = root / name
    destination.parent.mkdir(parents=True)
    destination.write_text("local changes")
    with pytest.raises(ValueError, match="overwrite"):
        archive.restore(name)
    assert destination.read_text() == "local changes"


def test_unknown_path_is_rejected(archived_file):
    with pytest.raises(ValueError, match="not recorded"):
        archive.restore("../outside")


def test_git_recovery_checks_the_pinned_object(archived_file, monkeypatch):
    root, name, source, data = archived_file
    source.unlink()
    calls = []

    def show(command, *, cwd):
        calls.append((command, cwd))
        return data

    monkeypatch.setattr(archive.subprocess, "check_output", show)
    archive.restore(name)
    assert calls == [(["git", "show", f"fixture:{name}"], root)]
    assert (root / name).read_bytes() == data
