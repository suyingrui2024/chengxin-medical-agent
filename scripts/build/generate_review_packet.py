"""Generate a sign-off packet for a fixed slice of calculation content.

The packet is the artefact a clinician reviews. Each page is
self-contained: what the instrument measures, every item with its
options and scores, the scoring rule, the bands, the source, the exact
disclaimer, and a signature block.

Every page carries a ``content_sha256`` of its pack. A signature is
therefore a signature on *that* version of the content — if the pack
changes, the hash changes and the signature no longer applies. That is
the whole point: "approved PHQ-8" is not a fact, "approved this exact
set of items and bands" is.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "review-packets" / "explicit-calculation-v1"

# Twelve items chosen to cover every engine shape and both audiences, so
# a reviewer's feedback generalises instead of applying to one scale.
SLICE = [
    ("01", "calculate.phq8", "患者自评 · 有分档"),
    ("02", "calculate.gad7", "患者自评 · 有分档"),
    ("03", "calculate.gds15", "患者自评 · 反向计分 · 无分档"),
    ("04", "calculate.odi", "患者自评 · 百分比折算"),
    ("05", "calculate.barthel", "患者自评 · 选项即分值"),
    ("06", "calculate.cact", "患者自评 · 无分档"),
    ("07", "calculate.bmi", "公式 · 患者可用"),
    ("08", "calculate.egfr_ckdepi2021", "公式 · 仅医生 · 年龄性别分支"),
    ("09", "calculate.cha2ds2vasc", "量表 · 仅医生 · 加权"),
    ("10", "calculate.gcs", "量表 · 仅医生 · 单向分级"),
    ("11", "calculate.sofa", "量表 · 仅医生 · 分段查表"),
    ("12", "calculate.news2", "量表 · 仅医生 · 分段查表"),
]

AUDIENCE = {"patient": "患者可自评", "clinician": "仅医生", "both": "两者皆可"}


def packs():
    root = resources.files("medagent.extensions").joinpath("packs")
    index = {}
    for file in root.iterdir():
        if file.name.endswith(".json"):
            manifest = json.loads(file.read_text(encoding="utf-8"))
            index[manifest["id"]] = manifest
    return index


def content_sha(manifest) -> str:
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def option_line(options) -> str:
    return " ／ ".join(f"{o['label']}（{o['value']} 分）" for o in options)


def threshold_line(thresholds) -> str:
    parts = []
    for entry in thresholds:
        low, high = entry.get("gte"), entry.get("lt")
        # Chinese comparison wording, not "<" / "≤": the HTML-to-DOCX
        # chain drops segments containing angle brackets, and clinicians
        # read "低于 100" faster than "x < 100" anyway.
        if low is not None and high is not None:
            span = f"{low} 至 {high}（含 {low}）"
        elif high is not None:
            span = f"低于 {high}"
        elif low is not None:
            span = f"{low} 及以上"
        else:
            span = "其他"
        parts.append(f"{span} → {entry['score']} 分")
    return "；".join(parts)


def scale_section(manifest) -> list[str]:
    scale = manifest["scale"]
    aggregate = scale["aggregate"]
    lines = [
        "### 条目与计分",
        "",
        "| # | 条目 | 选项（分值） |",
        "| --- | --- | --- |",
    ]
    for index, item in enumerate(scale["items"], start=1):
        if item["kind"] == "choice":
            detail = option_line(item["options"])
            if item.get("reverse_of") is not None:
                detail += f"　**（反向计分：{item['reverse_of']} − 选项值）**"
            if item.get("weight", 1) != 1:
                detail += f"　**（权重 {item['weight']}）**"
        else:
            detail = threshold_line(item["thresholds"])
            if item.get("weight", 1) != 1:
                detail += f"　**（权重 {item['weight']}）**"
        lines.append(f"| {index} | {item['label']} | {detail} |")
    mode = aggregate["mode"]
    if mode == "sum_percent":
        rule = f"合计除以 {aggregate['max']} 再乘 100，四舍五入取整，输出百分比"
    else:
        rule = "各条目得分相加"
    lines += [
        "",
        f"**计分规则**：{rule}。总分范围 {aggregate['min']}–{aggregate['max']}"
        f"{'%' if mode == 'sum_percent' else ' ' + aggregate.get('unit', '分')}。",
        "",
    ]
    return lines


def formula_section(manifest) -> list[str]:
    block = manifest["formula"]
    output = block["output"]
    lines = [
        "### 输入与输出",
        "",
        "| 输入 | 单位 | 可填范围 |",
        "| --- | --- | --- |",
    ]
    for item in block["inputs"]:
        if item["type"] == "enum":
            allowed = "、".join(option["label"] for option in item["options"])
            lines.append(f"| {item['label']} | — | {allowed} |")
        elif item["type"] == "date":
            lines.append(f"| {item['label']} | 日期 | {item['min']} 至 {item['max']} |")
        else:
            lines.append(f"| {item['label']} | {item['unit']} | {item['min']}–{item['max']} |")
    places = output["precision"].get("places")
    from medagent.extensions.formulas import FORMULAS

    spec = FORMULAS[block["function"]]
    lines += [
        "",
        f"**计算式**：{spec.method}",
        "",
        f"**来源编号**：{block['citation']}",
        "",
        "**已知限制**：",
        "",
    ]
    for limitation in spec.limitations:
        lines.append(f"- {limitation}")
    lines += [
        "",
        f"**输出**：{output['label']}，单位 {output['unit'] or '无'}，"
        f"保留 {places} 位小数，可接受范围 {output['min']}–{output['max']}。",
        "",
    ]
    return lines


def band_section(manifest) -> list[str]:
    if isinstance(manifest.get("scale"), dict):
        bands = manifest["scale"].get("bands") or []
        unit = manifest["scale"]["aggregate"].get("unit", "分")
        if manifest["scale"]["aggregate"]["mode"] == "sum_percent":
            unit = "%"
    else:
        bands = manifest["formula"].get("bands") or []
        unit = manifest["formula"]["output"]["unit"]
    if not bands:
        return ["### 分档", "", "本项**不设分档**，只输出数值，界面不显示任何分级词。", ""]
    lines = ["### 分档", "", "| 区间 | 分级词 |", "| --- | --- |"]
    for band in bands:
        if "min" in band:
            span = f"{band['min']}–{band['max']} {unit}"
        elif "lt" in band and "gte" in band:
            span = f"{band['gte']} 至 {band['lt']} {unit}（含 {band['gte']}）"
        elif "lt" in band:
            span = f"低于 {band['lt']} {unit}"
        else:
            span = f"{band['gte']} {unit} 及以上"
        lines.append(f"| {span} | {band['label']} |")
    lines.append("")
    return lines


def questions(manifest) -> list[str]:
    common = []
    if isinstance(manifest.get("scale"), dict):
        common.append("条目中文措辞是否与授权译本一致？如需修改，请给出准确文本。")
        if any(i.get("reverse_of") is not None for i in manifest["scale"]["items"]):
            common.append("反向计分条目是否正确？")
        if any(i.get("weight", 1) != 1 for i in manifest["scale"]["items"]):
            common.append("各条目权重是否符合原表？")
        if manifest["scale"].get("bands"):
            common.append("分档区间与分级词是否可接受？是否应改为只出分数？")
        else:
            common.append("维持「只出分数、不给分级词」是否符合预期？")
    else:
        common.append("公式形式、常数与单位是否与来源一致？")
        common.append("输入范围是否会挡住真实病例？")
        common.append("精度与舍入方式是否合适？")
        if manifest["formula"].get("bands"):
            common.append("分档区间与分级词是否可接受？")
    if manifest.get("audience") == "patient":
        common.append("患者可见的免责文案是否充分？")
    else:
        common.append("仅医生可用，入口与标注是否满足要求？")
    return common


def item_page(code, identity, manifest, label) -> str:
    reference = (manifest.get("references") or [{}])[0]
    lines = [
        f"# {code} · {manifest['name']}",
        "",
        f"**注册 id**：`{identity}`　**版本**：{manifest['version']}　"
        f"**内容哈希**：`{content_sha(manifest)}`",
        "",
        f"**受众**：{AUDIENCE.get(manifest['audience'], manifest['audience'])}　"
        f"**覆盖形态**：{label}",
        "",
        "## 它是什么",
        "",
        manifest["description"],
        "",
        "## 范围与限制",
        "",
        manifest["validation"].get("scope", "—"),
        "",
    ]
    if manifest.get("scale", {}).get("note"):
        lines += [f"> {manifest['scale']['note']}", ""]
    if manifest.get("formula", {}).get("note"):
        lines += [f"> {manifest['formula']['note']}", ""]

    if isinstance(manifest.get("scale"), dict):
        lines += scale_section(manifest)
    else:
        lines += formula_section(manifest)
    lines += band_section(manifest)

    lines += [
        "## 来源",
        "",
        f"- {reference.get('title', '—')}",
        f"- {reference.get('section', '—')}",
        f"- {reference.get('url', '—')}（访问于 {reference.get('accessed', '—')}）",
        "",
        "## 界面上的免责文案",
        "",
        "> 本结果由量表计分程序按公开量表规则计算，仅作记录与参考，不构成诊断，也不替代医生判断。",
        "",
        "## 需要确认的问题",
        "",
    ]
    for index, question in enumerate(questions(manifest), start=1):
        lines.append(f"{index}. {question}")
    lines += [
        "",
        "## 评审结论",
        "",
        "| 结论 | 评审人 | 日期 | 修改意见 |",
        "| --- | --- | --- | --- |",
        "| ☐ 通过　☐ 修改　☐ 不做 |  |  |  |",
        "",
        f"签字即表示确认内容哈希 `{content_sha(manifest)}` 对应的条目、权重、分档与免责文案。",
        "",
    ]
    return "\n".join(lines)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = packs()
    rows = []
    snapshot = {"packet": "explicit-calculation-v1", "generated": "2026-10-28", "items": []}

    for code, identity, label in SLICE:
        manifest = index.get(identity)
        if manifest is None:
            raise SystemExit(f"未找到内容包：{identity}")
        page = item_page(code, identity, manifest, label)
        (OUT / f"{code}.md").write_text(page, encoding="utf-8")
        rows.append(
            f"| {code} | [{manifest['name']}]({code}.md) | {AUDIENCE[manifest['audience']]} | "
            f"{label} | `{content_sha(manifest)}` |"
        )
        snapshot["items"].append(
            {
                "code": code,
                "id": identity,
                "name": manifest["name"],
                "audience": manifest["audience"],
                "content_sha256": content_sha(manifest),
            }
        )

    overview = [
        "# 显式计算 · 临床评审送审包（第一批 12 项）",
        "",
        "> 生成于 2026-10-28，内容来自 `src/medagent/extensions/packs/`。",
        "> 本包只覆盖 12 项，用于验证评审流程本身；其余 116 项待本批结论出来后再分批送审。",
        "",
        "## 为什么是这 12 项",
        "",
        "不是为了覆盖最多的科室，而是为了覆盖**所有计分形态**：患者自评与仅医生、",
        "有分档与无分档、反向计分、百分比折算、加权求和、分段查表、公式（含年龄性别分支）。",
        "这样评审意见才能推广到其余同类项目，而不是只对一个量表有效。",
        "",
        "| 编号 | 项目 | 受众 | 覆盖形态 | 内容哈希 |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
        "## 怎么评审",
        "",
        "1. 每页自包含：条目、选项、分值、计分规则、分档、来源、免责文案。不需要打开代码。",
        "2. 逐条回答「需要确认的问题」，在页面末尾的结论表里勾选并签字。",
        "3. 签字确认的是**内容哈希对应的那一版**。内容一旦修改，哈希变化，签字自动失效，需重新确认。",
        "4. 若某项应修改，请在「修改意见」写明改成什么；工程侧改完后会重新出具该页。",
        "",
        "## 评审前需要知道的边界",
        "",
        "- 这些项目**不是诊断工具**。界面与文案都不给诊断结论，只给分数与区间。",
        "- 所有项目的 `review_status` 目前是 `engineering_verified_not_clinical`，",
        "  自动化测试只能证明内部一致（条目能算出声明的分、缺项会拒绝、公式复现文献算例），",
        "  **不能证明临床可用**。",
        "- 结果不写入病例事实，不进入结论单。",
        "- 分档词（如「轻度」「高风险」）对患者可见，且固定附免责文案。若认为患者端不应出现分级词，",
        "  请在对应页注明，工程侧可改为只出分数。",
        "",
        "## 这批评审要回答的三个问题",
        "",
        "1. **条目措辞**：现有中文表述是否可用？若需替换为授权译本，请提供文本。",
        "2. **分档策略**：患者端显示分级词是否可接受？还是应统一改为只出分数？",
        "3. **流程本身**：这样一页一项的送审方式，是否足够你判断？缺什么？",
        "",
        "第 3 个问题最重要——如果这个格式不适用，后面 116 项都会走错方向。",
        "",
        "## 评审记录",
        "",
        "| 编号 | 结论 | 评审人 | 日期 |",
        "| --- | --- | --- | --- |",
        *[f"| {code} |  |  |  |" for code, _, _ in SLICE],
        "",
    ]
    (OUT / "index.md").write_text("\n".join(overview), encoding="utf-8")
    (OUT / "packet.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUT}")
    print(f"items: {len(SLICE)}")


if __name__ == "__main__":
    main()
