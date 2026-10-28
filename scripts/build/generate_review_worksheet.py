"""Generate the clinical review worksheet from the catalog and packs.

Every delivered item gets one row-block with its declared parameters and
the specific questions a reviewer has to answer. The worksheet is derived,
never hand-edited: ``tests/test_catalog.py`` asserts every delivered item
appears in it.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from medagent.extensions.catalog import CATALOG, by_department, summary

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "量表与公式临床评审工作表.md"

KIND_TEXT = {"scale": "量表", "formula": "公式", "table": "查表"}
AUDIENCE_TEXT = {"patient": "患者可自评", "clinician": "仅医生"}


def pack_index():
    root = resources.files("medagent.extensions").joinpath("packs")
    index = {}
    for file in root.iterdir():
        if not file.name.endswith(".json"):
            continue
        manifest = json.loads(file.read_text(encoding="utf-8"))
        index[manifest["id"]] = manifest
    return index


def scale_facts(manifest):
    scale = manifest["scale"]
    items = scale["items"]
    aggregate = scale["aggregate"]
    kinds = {item["kind"] for item in items}
    reverse = sum(1 for item in items if item.get("reverse_of") is not None)
    weighted = sum(1 for item in items if item.get("weight", 1) != 1)
    mode = aggregate["mode"]
    unit = "%" if mode == "sum_percent" else aggregate.get("unit", "分")
    top = 100 if mode == "sum_percent" else aggregate["max"]
    shape = "选项型" if kinds == {"choice"} else ("分段查表型" if kinds == {"threshold_table"} else "混合型")
    parts = [f"{len(items)} 项{shape}"]
    if reverse:
        parts.append(f"{reverse} 项反向计分")
    if weighted:
        parts.append(f"{weighted} 项加权")
    parts.append(f"满分 {aggregate['min']}–{top} {unit}")
    return "，".join(parts)


def formula_facts(manifest):
    block = manifest["formula"]
    inputs = block["inputs"]
    output = block["output"]
    places = output["precision"].get("places")
    precision = f"{places} 位小数" if places is not None else "有效数字"
    enum_inputs = sum(1 for item in inputs if item["type"] == "enum")
    date_inputs = sum(1 for item in inputs if item["type"] == "date")
    parts = [f"{len(inputs)} 个输入"]
    if enum_inputs:
        parts.append(f"{enum_inputs} 个选项型")
    if date_inputs:
        parts.append(f"{date_inputs} 个日期型")
    parts.append(f"输出 {output['min']}–{output['max']} {output['unit'] or '（无单位）'}")
    parts.append(precision)
    return "，".join(parts)


def band_text(manifest):
    if isinstance(manifest.get("scale"), dict):
        bands = manifest["scale"].get("bands") or []
    else:
        bands = manifest["formula"].get("bands") or []
    if not bands:
        return "**无分档（只出数值）**"
    return " / ".join(band["label"] for band in bands)


def questions(manifest, audience):
    common = []
    if isinstance(manifest.get("scale"), dict):
        common.append("条目中文措辞是否与授权译本一致（不得自行改写）")
        if any(item.get("reverse_of") is not None for item in manifest["scale"]["items"]):
            common.append("反向计分条目是否正确")
        if any(item.get("weight", 1) != 1 for item in manifest["scale"]["items"]):
            common.append("各条目权重是否符合原表")
        if manifest["scale"].get("bands"):
            common.append("分档区间与分档词是否可接受")
        else:
            common.append("只出总分不给分档，是否维持")
        if manifest["scale"]["aggregate"]["mode"] == "sum_percent":
            common.append("百分比折算方式（合计÷满分×100）是否正确")
    else:
        common.append("常数、单位与公式形式是否与来源一致")
        common.append("输入范围是否会挡住真实病例")
        common.append("精度与舍入方式是否合适")
        if manifest["formula"].get("bands"):
            common.append("分档区间与分档词是否可接受")
    if audience == "patient":
        common.append("免责文案是否充分")
    else:
        common.append("仅医生可用，界面是否已明确标注")
    return common


def main():
    packs = pack_index()
    counts = summary()
    grouped = by_department()

    lines = [
        "# 量表与公式临床评审工作表",
        "",
        "> 本表由 `src/medagent/extensions/catalog.py` 与 `packs/*.json` 生成，**不要手工编辑**。",
        "> 每项列出已声明的参数与需要评审确认的具体问题；请在「结论」列填写。",
        "",
        "## 0. 这份表要解决什么",
        "",
        f"目录共 {counts['total']} 项，其中 **{counts['delivered']} 项已交付**、"
        f"{counts['rejected']} 项明确不做。已交付项目的数值计算与内部一致性已由自动化测试覆盖，",
        "但**代码测试不等于临床审核**。本表用于逐项确认：条目措辞、权重、分档区间、分档词与免责文案。",
        "",
        "评审通过前，所有项目的 `review_status` 均为 `engineering_verified_not_clinical`，",
        "界面上标注「工程验证，非临床评价」。",
        "",
        "## 1. 使用方式",
        "",
        "1. 按科室分配评审人，一次评一个科室。",
        "2. 对每一项，先看「已声明参数」，再逐条回答「需确认的问题」。",
        "3. 在「结论」列填 `通过` / `修改` / `不做`；填 `修改` 时请写明改成什么。",
        "4. 结论汇总到 `docs/待临床评审清单.md` 的评审记录表。",
        "",
        "## 2. 状态说明",
        "",
        "| 标记 | 含义 |",
        "| --- | --- |",
        "| 仅医生 | 患者端不可见，只能通过研发预览视图手填 |",
        "| 患者可自评 | 患者端可见，需在输入卡逐条作答 |",
        "| 无分档 | 只输出数值，不给分级词 |",
        "",
        "---",
        "",
    ]

    for department, entries in grouped.items():
        delivered = [entry for entry in entries if entry.status == "delivered"]
        if not delivered:
            continue
        patient = sum(1 for entry in delivered if entry.audience == "patient")
        lines += [
            f"## {department}（{len(delivered)} 项，患者可自评 {patient}）",
            "",
        ]
        for entry in delivered:
            manifest = packs.get(entry.id)
            if manifest is None:
                continue
            facts = (
                scale_facts(manifest)
                if isinstance(manifest.get("scale"), dict)
                else formula_facts(manifest)
            )
            reference = (manifest.get("references") or [{}])[0]
            lines += [
                f"### {entry.code} {entry.name}",
                "",
                f"- **类型**：{KIND_TEXT.get(entry.kind, entry.kind)} · {AUDIENCE_TEXT[entry.audience]}",
                f"- **已声明参数**：{facts}",
                f"- **分档**：{band_text(manifest)}",
                f"- **来源**：{reference.get('title', '—')}；{reference.get('section', '—')}",
                f"- **注册 id**：`{entry.id}` · 版本 {manifest['version']}",
                "",
                "需确认的问题：",
                "",
            ]
            for index, question in enumerate(questions(manifest, entry.audience), start=1):
                lines.append(f"{index}. {question}")
            lines += [
                "",
                "| 结论 | 评审人 | 日期 | 备注 |",
                "| --- | --- | --- | --- |",
                "|  |  |  |  |",
                "",
            ]

    rejected = [entry for entry in CATALOG if entry.status == "rejected"]
    lines += [
        "---",
        "",
        f"## 附：明确不做的 {len(rejected)} 项",
        "",
        "这些项目已判定不实现，列出仅为记录理由。若评审认为其中某项应当实现，请注明。",
        "",
        "| 编号 | 科室 | 项目 | 理由 | 是否应重新考虑 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in rejected:
        lines.append(
            f"| {entry.code} | {entry.department} | {entry.name} | {entry.note or '—'} |  |"
        )

    lines += [
        "",
        "## 附：评审完成后需要工程侧做的事",
        "",
        "1. 按结论修改 `packs/*.json`，`version` 递增。",
        "2. 若分档或阈值有改动，同步更新 `docs/门诊显式计算科目与项目总表.md`（重新生成）。",
        "3. 把 `review_status` 从 `engineering_verified_not_clinical` 改为评审通过后的状态。",
        "4. 重跑 `pytest` 与发布门，附结果再报。",
        "",
    ]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT, "items:", counts["delivered"])


if __name__ == "__main__":
    main()
