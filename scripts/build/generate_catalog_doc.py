"""Generate docs/门诊显式计算科目与项目总表.md from extensions/catalog.py."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from medagent.extensions.catalog import CATALOG, STATUSES, by_department, summary

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "门诊显式计算科目与项目总表.md"

STATUS_TEXT = {
    "delivered": "已交付",
    "planned": "待做",
    "clinician_pending": "待入口",
    "rejected": "不做",
}
KIND_TEXT = {"scale": "量表", "formula": "公式", "table": "查表"}
AUDIENCE_TEXT = {"patient": "患者", "clinician": "医生"}


def pack_index():
    root = resources.files("medagent.extensions").joinpath("packs")
    index = {}
    for file in root.iterdir():
        if not file.name.endswith(".json"):
            continue
        manifest = json.loads(file.read_text(encoding="utf-8"))
        index[manifest["id"]] = manifest
    return index


def signature(manifest):
    """Short input/output description for a delivered pack."""
    if manifest is None:
        return "—", "—"
    if isinstance(manifest.get("scale"), dict):
        scale = manifest["scale"]
        items = scale.get("items") or []
        aggregate = scale.get("aggregate") or {}
        inputs = f"{len(items)} 项单选"
        output = f"0–{aggregate.get('max')} {aggregate.get('unit', '分')}"
        if aggregate.get("mode") == "sum_percent":
            output = "0–100 %"
        return inputs, output
    formula = manifest.get("formula") or {}
    inputs = " / ".join(item["label"] for item in formula.get("inputs") or [])
    output = formula.get("output") or {}
    return inputs, f"{output.get('label', '')}（{output.get('unit', '')}）"


def main():
    packs = pack_index()
    counts = summary()
    lines = [
        "# 门诊显式计算科目与项目总表",
        "",
        "> 本表由 `src/medagent/extensions/catalog.py` 生成，**不要手工编辑**。",
        "> `tests/test_catalog.py` 断言本表、目录与内容包三者一致；改动请改 catalog 后重新生成本表。",
        "",
        "## 0. 目的与边界",
        "",
        "本表只回答一件事：**哪些科室的哪些项目可以做成显式计算，以及它们现在处于什么状态**。",
        "它不是诊疗路径，也不是检查套餐；表内任何项目都**不构成诊断，不给出治疗、剂量或预后建议**。",
        "",
        "## 1. 状态定义",
        "",
        "| 状态 | 含义 |",
        "| --- | --- |",
        "| 已交付 `delivered` | 已注册、可执行、有测试覆盖 |",
        "| 待做 `planned` | 现有引擎可以表达，内容尚未编写 |",
        "| 待入口 `clinician_pending` | 仅医生可用，但医生端入口尚未建立，**故意不注册** |",
        "| 不做 `rejected` | 记录在此仅为说明为何不做 |",
        "",
        "## 2. 总览",
        "",
        f"共 **{counts['total']} 项**，覆盖 **{len(by_department())} 个科室**；"
        f"患者可用 {counts['patient']} 项，仅医生 {counts['clinician']} 项。",
        "",
        "| 状态 | 数量 |",
        "| --- | --- |",
    ]
    for status in ("delivered", "planned", "clinician_pending", "rejected"):
        lines.append(f"| {STATUS_TEXT[status]} | {counts[status]} |")

    lines += [
        "",
        "## 3. 分科室明细",
        "",
        "输入形态与输出单位在已交付项目上由内容包派生；未交付项目标注为待定稿，避免与将来的实现漂移。",
        "",
    ]
    for department, entries in by_department().items():
        patient = sum(1 for item in entries if item.audience == "patient")
        lines += [
            f"### {department}（{len(entries)} 项，患者 {patient}）",
            "",
            "| 编号 | 项目 | 类型 | 输入形态 | 输出/单位 | 受众 | 状态 | 注册 id | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for entry in entries:
            manifest = packs.get(entry.id) if entry.id else None
            inputs, output = signature(manifest)
            if entry.status != "delivered":
                inputs = inputs if inputs != "—" else "待定稿"
                output = output if output != "—" else "待定稿"
            lines.append(
                "| {code} | {name} | {kind} | {inputs} | {output} | {audience} | {status} | {id} | {note} |".format(
                    code=entry.code,
                    name=entry.name,
                    kind=KIND_TEXT[entry.kind],
                    inputs=inputs,
                    output=output,
                    audience=AUDIENCE_TEXT[entry.audience],
                    status=entry.status,
                    id=entry.id or "—",
                    note=entry.note or "",
                )
            )
        lines.append("")

    lines += [
        "## 4. 引擎能力矩阵",
        "",
        "| 引擎原语 | 能表达 | 不能表达 |",
        "| --- | --- | --- |",
        "| 量表 `choice` + `sum` | 加权求和的条目型量表（PHQ-8、GAD-7、ISI） | 需要幂运算或回归方程的评分 |",
        "| 量表 `sum_percent` | 按满分折算百分比的量表（ODI、NDI） | 需要人群常模的百分位 |",
        "| 分段查表 `threshold_table` | 测量值映射为分值（NEWS2、qSOFA） | 需要插值的曲线（生长 Z 值、光疗阈值） |",
        "| 命名公式 `formula` | 白名单内的纯公式（BMI、MAP、阴离子间隙） | 任意表达式、条件分支、概率模型 |",
        "",
        "## 5. 门禁与评审",
        "",
        "- 每个已交付项目必须有可核对的 HTTPS 来源，且 `citation` 出现在 `references[].section` 中。",
        "- 所有项目 `review_status` 为 `engineering_verified_not_clinical`；数值正确不等于临床可用。",
        "- 临床评审条目见 `docs/待临床评审清单.md`（C15 量表组、C17 公式组、C18 扩容组）。",
        "- 结果不写入病例事实，不进入结论单。",
        "",
        "## 6. 与代码的映射",
        "",
        "| 位置 | 作用 |",
        "| --- | --- |",
        "| `src/medagent/extensions/catalog.py` | 唯一机读真源，`delivered` 行即注册白名单 |",
        "| `src/medagent/extensions/scale_engine.py` | 量表计分与分档 |",
        "| `src/medagent/extensions/formula_engine.py` | 公式校验与执行 |",
        "| `src/medagent/extensions/formulas.py` | 公式白名单实现 |",
        "| `src/medagent/extensions/packs/*.json` | 内容包（已交付项目） |",
        "| `tests/test_catalog.py` | 三方一致性门禁 |",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT, "entries:", len(CATALOG))


if __name__ == "__main__":
    main()
