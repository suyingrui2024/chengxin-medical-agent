#!/usr/bin/env python3
"""Generate the docs index, and keep it honest.

Why this exists
---------------
``docs/`` holds 69 top-level design documents.  They were written at different
times for different purposes, and nothing said which ones still describe the
code.  A reader arriving at ``docs/`` today has no way to tell a current rule
from a superseded design, and the failure mode is quiet: an outdated document
is still perfectly readable and still confidently wrong.

So this script writes ``docs/README.md`` -- an index with each document's
status, size and last change -- and the ``--check`` mode fails when the index
has drifted from the files, which is what stops a new document from being
invisible.

The status of a document is derived from its name, and that derivation is
deliberately coarse.  It answers "what kind of thing is this", not "is this
still true"; only a human can decide the second, which is why every row
carries the date it last changed.

Run locally::

    python scripts/lint/docs_index.py          # rewrite the index
    python scripts/lint/docs_index.py --check  # fail if it is out of date
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
INDEX = DOCS / "README.md"

#: Name fragments and the status they imply, most specific first.  The order
#: matters: "方案" is checked after "规则" so that a document named
#: "…规则" is not classified as a plain design.
STATUS_RULES = [
    ("规则", "规范", "当前有效的规则，改代码前先读这一类"),
    ("规范", "规范", "当前有效的规则，改代码前先读这一类"),
    ("清单", "工作清单", "进行中的计划与待办，随时可能过期"),
    ("审查", "评审", "阶段性结论与待评审项"),
    ("评审", "评审", "阶段性结论与待评审项"),
    ("评估", "评估", "一次性评估的结论与数据"),
    ("评测", "评估", "一次性评估的结论与数据"),
    ("审计报告", "评估", "一次性评估的结论与数据"),
    ("测试覆盖矩阵", "评估", "一次性评估的结论与数据"),
    ("方案", "设计", "设计方案，说明当时想做什么"),
    ("设计", "设计", "设计方案，说明当时想做什么"),
    ("说明", "设计", "设计方案，说明当时想做什么"),
    ("研究", "调研", "调研与可行性结论"),
    ("调研", "调研", "调研与可行性结论"),
]

#: Documents that are not indexed by rule but are known to be current.
PINNED = {
    "后续工作清单.md": ("工作清单", "当前有效的规则，改代码前先读这一类"),
    "代码与命名规范.md": ("规范", "当前有效的规则，改代码前先读这一类"),
    "架构分层规则.md": ("规范", "当前有效的规则，改代码前先读这一类"),
    "函数级可读性改造方案.md": ("工作清单", "进行中的计划与待办，随时可能过期"),
    "模块路径变更对照.md": ("规范", "当前有效的规则，改代码前先读这一类"),
    "代码可读性后续优化方案.md": ("历史设计", "已被取代或已完成的设计存档，现状以较新的文档为准"),
    "代码质量满分工作清单.md": ("工作清单", "进行中的计划与待办，随时可能过期"),
    "长函数判定表.md": ("工作清单", "进行中的计划与待办，随时可能过期"),
    "数据出域与隐私.md": ("权威文档", "与代码同步维护的当前设计，以本文为准"),
}

HEADER = """# 文档索引

本目录有 {count} 篇文档，全部是设计、评估与规范记录。**它们描述的是写作当时的状态，
不是代码的现状**——最近几轮改造移动了 30 多个文件的结构，因此除非标为「规范」，
请先对照代码再采信。

状态只有四类：

| 状态 | 含义 |
| --- | --- |
| **规范** | 当前生效的规则：改代码前读这一类 |
| **工作清单** | 进行中的计划与待办，随时可能过期 |
| **评审** | 阶段性结论与待评审项 |
| **设计 / 调研 / 评估** | 当时的设计与结论，作为背景参考 |

「最后更新」是 git 里该文件最后一次被修改的日期。日期越旧，越可能是被后续改造
取代的历史记录。

新增文档请在表里加一行，然后跑 `python scripts/lint/docs_index.py` 重新生成，
或用 `--check` 确认索引与文件一致。

## 规范与进行中

{table_current}

## 其余文档

{table_rest}
"""


def _display(path: pathlib.Path) -> str:
    """A repo-relative path when the file is inside the repo, else absolute."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _neg_date(date: str) -> str:
    """Sort key that puts the newest date first (dates sort as text)."""
    return "".join(chr(ord("9") - (ord(c) - ord("0"))) if c.isdigit() else c for c in date)


def status_for(name: str) -> tuple[str, str]:
    """The (status, description) pair for a document name."""
    if name in PINNED:
        return PINNED[name]
    for fragment, status, description in STATUS_RULES:
        if fragment in name:
            return status, description
    return "参考", "背景材料，没有明确状态"


def last_change(path: pathlib.Path) -> str:
    """The date git last recorded a change to this file."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return result.stdout.strip() or "—"


def title_of(path: pathlib.Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def indexed_documents() -> dict[str, str]:
    """The documents the index on disk lists, with the status it gives them."""
    if not INDEX.exists():
        return {}
    listed = {}
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| \[[^\]]*\]\(([^)]+\.md)\) \| ([^|]+) \|", line)
        if match:
            listed[match.group(1)] = match.group(2).strip()
    return listed


def build() -> str:
    documents = sorted(DOCS.glob("*.md"))
    documents = [d for d in documents if d.name != "README.md"]
    rows = []
    for document in documents:
        status, description = status_for(document.name)
        rows.append(
            {
                "status": status,
                "description": description,
                "date": last_change(document),
                "lines": len(
                    document.read_text(encoding="utf-8", errors="ignore").splitlines()
                ),
                "name": document.name,
                "title": title_of(document),
            }
        )

    current = [r for r in rows if r["status"] in {"规范", "工作清单"}]
    rest = [r for r in rows if r["status"] not in {"规范", "工作清单"}]
    current.sort(key=lambda r: (r["status"] != "规范", _neg_date(r["date"])))
    rest.sort(key=lambda r: _neg_date(r["date"]))

    def render(items):
        lines = [
            "| 文档 | 状态 | 最后更新 | 行数 |",
            "| --- | --- | --- | --- |",
        ]
        for item in items:
            lines.append(
                f"| [{item['title']}]({item['name']}) | {item['status']} | "
                f"{item['date']} | {item['lines']} |"
            )
        return "\n".join(lines)

    return HEADER.format(
        count=len(rows),
        table_current=render(current),
        table_rest=render(rest),
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the index on disk differs from what would be written",
    )
    args = parser.parse_args(argv)

    if args.check:
        if not INDEX.exists():
            print(f"docs index missing: {_display(INDEX)}", file=sys.stderr)
            return 1
        listed = indexed_documents()
        actual = {
            d.name: status_for(d.name)[0]
            for d in sorted(DOCS.glob("*.md"))
            if d.name != "README.md"
        }
        missing = sorted(set(actual) - set(listed))
        unknown = sorted(set(listed) - set(actual))
        wrong_status = sorted(
            name for name in set(actual) & set(listed) if listed[name] != actual[name]
        )
        if missing or unknown or wrong_status:
            print("docs index is out of date:", file=sys.stderr)
            for name in missing:
                print(f"  - not indexed: docs/{name}", file=sys.stderr)
            for name in unknown:
                print(f"  - indexed but absent: docs/{name}", file=sys.stderr)
            for name in wrong_status:
                print(
                    f"  - status changed: docs/{name} "
                    f"({listed[name]} -> {actual[name]})",
                    file=sys.stderr,
                )
            print(
                "\nRun `python scripts/lint/docs_index.py` to refresh it.",
                file=sys.stderr,
            )
            return 1
        print(f"docs index is up to date: {len(actual)} documents.")
        return 0

    INDEX.write_text(build(), encoding="utf-8")
    print(f"docs index written: {_display(INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
