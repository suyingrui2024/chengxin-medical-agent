#!/usr/bin/env python3
"""Generate the docs navigation, and keep it honest.

Why this exists
---------------
``docs/`` had 74 top-level documents written at different times for
different purposes, and nothing said which ones still describe the code
or in what order to read them.  The index now is a *reading order*, not
just a listing: current documents are grouped by subsystem in the order
a reader needs them, and everything that only describes the past lives
in ``archive/`` and is listed collapsed at the bottom.

The grouping is curated here, deliberately.  A new document that nobody
has placed into a group fails ``--check`` -- the same discipline the
code applies to architecture rules: placement is a decision, not
something that happens by existing.

Run locally::

    python scripts/lint/docs_index.py          # rewrite the index
    python scripts/lint/docs_index.py --check  # fail if it has drifted
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
INDEX = DOCS / "README.md"

#: The reading order.  Each group is (heading, one-line purpose, files in
#: the order a reader should open them).  A root document missing from
#: every group is a drift that --check reports.
GROUPS: list[tuple[str, str, list[str]]] = [
    (
        "从这里开始",
        "系统现在是什么。先读这一篇，其余按需查阅。",
        ["系统现状总览.md"],
    ),
    (
        "规范与流程",
        "改代码前必读：规则、门禁、发布流程与数据出域边界。",
        [
            "代码与命名规范.md",
            "架构分层规则.md",
            "模块路径变更对照.md",
            "数据出域与隐私.md",
            "发布门、阶段提交与临床评测框架.md",
        ],
    ),
    (
        "产品与问诊引擎",
        "假设驱动的问诊流程、结论单、风险分流与事实校验。",
        [
            "首版产品与技术设计.md",
            "假设驱动问诊设计文档.md",
            "问诊结论单设计文档.md",
            "动态问诊选项与风险分流设计.md",
            "事实断言与否定证据校验.md",
            "长问诊的信息保留与原话核对.md",
            "就诊对象与会谈草稿隔离.md",
        ],
    ),
    (
        "结构化交互",
        "图示定位、自检任务卡与展示协议。",
        [
            "结构化定位答案与患者核对.md",
            "结构化展示协议与组件扩展.md",
            "门诊式问诊与图示自查背调.md",
        ],
    ),
    (
        "计算与能力包",
        "公式、量表、换算与执行回执。",
        [
            "能力插件与问诊模板.md",
            "门诊显式计算科目与项目总表.md",
            "注册计算工具与执行回执.md",
            "有来源的计算插件与血糖换算.md",
            "患者计算输入与问诊衔接.md",
            "计算请求的同轮补充与接续.md",
            "量表与公式临床评审工作表.md",
        ],
    ),
    (
        "测量与接续",
        "测量反馈、重复测量与问诊接续。",
        [
            "测量反馈与同轮补充.md",
            "重复测量与问诊接续.md",
        ],
    ),
    (
        "执行工作台",
        "患者在自己的项目目录里跑代码：底座、原位执行、澄清与恢复。",
        [
            "通用执行底座与能力评测设计.md",
            "本地项目目录与原位执行.md",
            "执行澄清与用户回答.md",
            "执行工作区恢复与存储.md",
        ],
    ),
    (
        "健康档案",
        "患者纵向数据与图片识别。",
        [
            "患者纵向医疗数据与图片识别归档设计.md",
        ],
    ),
    (
        "联网与子智能体",
        "教育/复核检索、出站闸门与并行分支。",
        [
            "联网与子智能体工具设计.md",
            "DSH后台复核与报告分析设计.md",
        ],
    ),
    (
        "桌面与平台",
        "Electron 壳、打包与各平台验收。",
        [
            "桌面应用使用与打包.md",
            "Windows原生环境与验收.md",
        ],
    ),
    (
        "工作清单",
        "进行中的计划与待办，随时可能过期。",
        [
            "后续工作清单.md",
            "代码质量满分工作清单.md",
            "长函数判定表.md",
        ],
    ),
    (
        "评审与证据",
        "待临床评审项与结论的证据来源。",
        [
            "待临床评审清单.md",
            "证据与来源.md",
            "测试覆盖矩阵.md",
            "使用体验评测报告.md",
        ],
    ),
]

HEADER = """# 文档导航

本目录按**阅读顺序**组织：先读《系统现状总览》了解系统现在是什么，
再按子系统查阅现行设计。已完成或已被取代的历史设计全部移入
[archive/](archive/)（{archive_count} 篇），只在文末列出索引。

机器产物不在本索引：`evaluations/`（评测输出）、`review-packets/`
（评审包）、`assets/`（图片）。

{groups}

## 历史存档（archive/）

以下 {archive_count} 篇是已完成的一次性方案、被后续改造取代的设计、
以及阶段性评估——描述的是**写作当时**的状态，不描述代码现状。
按名字排序：

{archive_table}

---

维护规则：新增文档必须放进上面某个阅读分组（在
`scripts/lint/docs_index.py` 的 `GROUPS` 里登记），然后跑
`python scripts/lint/docs_index.py` 重新生成；`--check` 会在
有文档未被分组或索引过期时失败。
"""


def _display(path: pathlib.Path) -> str:
    """A repo-relative path when the file is inside the repo, else absolute."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


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


def grouped_names() -> dict[str, str]:
    """Every root document -> the group heading it is placed in."""
    placed: dict[str, str] = {}
    for heading, _purpose, names in GROUPS:
        for name in names:
            if name in placed:
                raise SystemExit(f"{name} is listed in two reading groups")
            placed[name] = heading
    return placed


def build() -> str:
    actual = {p.name for p in DOCS.glob("*.md")} - {"README.md"}
    placed = grouped_names()
    ungrouped = sorted(actual - set(placed))
    phantom = sorted(set(placed) - actual)
    if ungrouped or phantom:
        problems = [f"docs/{name}" for name in ungrouped] + [
            f"grouped but missing: docs/{name}" for name in phantom
        ]
        raise SystemExit(
            "docs/ 与阅读分组不一致（先在 GROUPS 里登记，或移动文件）：\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    archive_files = sorted(ARCHIVE.glob("*.md"))
    archive_table = "\n".join(
        f"- [{p.stem}]({ARCHIVE.name}/{p.name})（{last_change(p)} 最后更新）"
        for p in archive_files
    )

    group_sections = []
    for heading, purpose, names in GROUPS:
        rows = "\n".join(
            f"- [{title_of(DOCS / name)}]({name})"
            f"（{last_change(DOCS / name)} 最后更新）"
            for name in names
        )
        group_sections.append(f"### {heading}\n\n{purpose}\n\n{rows}")

    return HEADER.format(
        archive_count=len(archive_files),
        groups="\n\n".join(group_sections),
        archive_table=archive_table,
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the index on disk differs from what would be written",
    )
    args = parser.parse_args(argv)

    try:
        generated = build()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        print("\nRun `python scripts/lint/docs_index.py` after fixing.", file=sys.stderr)
        return 1

    if args.check:
        if not INDEX.exists() or INDEX.read_text(encoding="utf-8") != generated:
            print(
                f"docs index out of date: {_display(INDEX)}",
                file=sys.stderr,
            )
            print(
                "\nRun `python scripts/lint/docs_index.py` to refresh it.",
                file=sys.stderr,
            )
            return 1
        count = len(grouped_names())
        print(f"docs index is up to date: {count} documents, grouped.")
        return 0

    INDEX.write_text(generated, encoding="utf-8")
    print(f"docs index written: {_display(INDEX)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
