"""The docs index must list every document, and say what each one is.

An index that quietly omits a document gives it the same fate as no index at
all, so the check is tested against a tree where a document is missing from the
index.
"""
from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "docs_index.py"


def _load():
    spec = importlib.util.spec_from_file_location("docs_index", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def build_docs(root: pathlib.Path, names: dict[str, str]) -> pathlib.Path:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    for name, body in names.items():
        (docs / name).write_text(body, encoding="utf-8")
    return docs


def test_status_is_derived_from_the_document_name():
    assert guard.status_for("架构分层规则.md")[0] == "规范"
    assert guard.status_for("代码与命名规范.md")[0] == "规范"
    assert guard.status_for("后续工作清单.md")[0] == "工作清单"
    assert guard.status_for("待临床评审清单.md")[0] == "工作清单"
    assert guard.status_for("问诊结论单设计文档.md")[0] == "设计"
    assert guard.status_for("执行能力对照评测.md")[0] == "评估"


def test_an_unknown_name_is_reference_not_a_rule():
    """A document nobody has classified must not be promoted to a rule."""
    assert guard.status_for("某次讨论记录.md")[0] == "参考"


def test_the_index_lists_every_document(tmp_path, monkeypatch):
    docs = build_docs(
        tmp_path,
        {
            "架构分层规则.md": "# 架构分层规则\n\n规则。\n",
            "某次设计.md": "# 某次设计\n\n设计。\n",
        },
    )
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "INDEX", docs / "README.md")
    guard.main([])
    text = (docs / "README.md").read_text(encoding="utf-8")
    assert "架构分层规则.md" in text
    assert "某次设计.md" in text


def test_a_document_missing_from_the_index_is_reported(tmp_path, monkeypatch, capsys):
    docs = build_docs(
        tmp_path,
        {
            "架构分层规则.md": "# 架构分层规则\n\n规则。\n",
            "新来的设计.md": "# 新来的设计\n\n设计。\n",
        },
    )
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "INDEX", docs / "README.md")
    guard.main([])
    assert guard.main(["--check"]) == 0

    # A third document arrives and nobody adds it to the index.
    (docs / "没人记得的设计.md").write_text("# 没人记得的设计\n\n设计。\n", encoding="utf-8")
    assert guard.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert "没人记得的设计.md" in captured.err


def test_the_real_index_covers_every_document():
    """The committed index lists all of them."""
    listed = guard.indexed_documents()
    actual = {
        d.name
        for d in guard.DOCS.glob("*.md")
        if d.name != "README.md"
    }
    assert actual == set(listed), sorted(actual ^ set(listed))
