"""The docs navigation must place every document, or say why not.

A reading order that quietly omits a document gives it the same fate as no
order at all, so the builder refuses to generate while anything is ungrouped,
and ``--check`` fails when the committed index has drifted from what the
groups would produce.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "docs_index.py"


def _load():
    spec = importlib.util.spec_from_file_location("docs_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def test_grouping_covers_every_root_document_exactly_once():
    """The real tree: nothing at docs/ root is unplaced, nothing phantom."""
    actual = guard.document_names()
    placed = set(guard.grouped_names())
    assert placed == actual, sorted(placed ^ actual)


def test_an_ungrouped_document_stops_the_build(tmp_path, monkeypatch, capsys):
    """A new document nobody placed into a group is a decision, not a default."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in guard.GROUPS[0][2]:
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    (docs / "没人分组的设计.md").write_text("# 没人分组的设计\n", encoding="utf-8")
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "ARCHIVE", docs / "archive")
    with pytest.raises(SystemExit) as exc:
        guard.build()
    assert "没人分组的设计.md" in str(exc.value)


def test_a_phantom_group_entry_stops_the_build(tmp_path, monkeypatch):
    """A group naming a document that does not exist is drift, not prose."""
    docs = tmp_path / "docs"
    docs.mkdir()
    for _heading, _purpose, names in guard.GROUPS:
        for name in names:
            (docs / name).parent.mkdir(parents=True, exist_ok=True)
            (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    (docs / guard.GROUPS[0][2][0]).unlink()  # remove one grouped file
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "ARCHIVE", docs / "archive")
    with pytest.raises(SystemExit) as exc:
        guard.build()
    assert "grouped but missing" in str(exc.value)


def test_build_renders_groups_in_order_and_archives_collapsed(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "archive").mkdir()
    for _heading, _purpose, names in guard.GROUPS:
        for name in names:
            (docs / name).parent.mkdir(parents=True, exist_ok=True)
            (docs / name).write_text(f"# {name}\n", encoding="utf-8")
    (docs / "archive" / "旧设计.md").write_text("# 旧设计\n", encoding="utf-8")
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "ARCHIVE", docs / "archive")
    text = guard.build()
    # Every group heading appears, in order, and the archive is one section.
    positions = [text.index(heading) for heading, _p, _n in guard.GROUPS]
    assert positions == sorted(positions)
    assert "## 历史存档（archive/）" in text
    assert "旧设计.md" in text


def test_the_committed_index_matches_the_groups():
    """The committed navigation is exactly what the groups produce."""
    assert guard.INDEX.exists()
    assert guard.INDEX.read_text(encoding="utf-8") == guard.build()


def test_an_ungrouped_nested_document_stops_the_build(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    (docs / "features").mkdir(parents=True)
    (docs / "features/unlisted.md").write_text("# Unlisted\n")
    monkeypatch.setattr(guard, "DOCS", docs)
    with pytest.raises(SystemExit, match="unlisted.md"):
        guard.build()
