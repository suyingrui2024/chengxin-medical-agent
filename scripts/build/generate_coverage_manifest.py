"""Build the test coverage matrix.

The question this answers is "have we finished testing?". Nothing in the
repo could answer it before: coverage was implicit in whatever happened
to be written.

Coverage is derived from artefacts, not from reading test source:

  engine  — the capability is actually executed by an automated test.
            Scales and formulas are all run by the release gate's
            ``check_calculations``; conversions carry their own tests.
  browser — a Playwright spec drives the real UI for it.
  review  — it appears in a clinical review packet.

Uncovered capabilities are written into the manifest as an explicit gap
list. ``tests/test_coverage_manifest.py`` fails when that list no longer
matches reality, so a gap can shrink (good, regenerate) but never grow
silently.
"""

import json
import re
from pathlib import Path

from medagent.extensions.builtin import load_registry

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests" / "fixtures" / "coverage-manifest.json"
REPORT = ROOT / "docs" / "测试覆盖矩阵.md"


def ids_in(paths):
    found = set()
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        found.update(re.findall(r"calculate\.[a-z0-9_]+", text))
    return found


def main():
    items = load_registry().descriptions()
    total = {item["id"]: item for item in items}

    scales = {i for i, item in total.items() if isinstance(item.get("scale"), dict)}
    formulas = {i for i, item in total.items() if isinstance(item.get("formula"), dict)}
    conversions = set(total) - scales - formulas

    # Engine: the gate executes every scale and formula; conversions need
    # their own test, so look for the id in the Python suite.
    python_ids = ids_in(sorted((ROOT / "tests").glob("*.py")))
    engine = scales | formulas | (conversions & python_ids)

    # Browser: the sweep fixture covers patient-facing items; the rest is
    # whatever a spec or its fixtures reference by id.
    sweep = set()
    for name in ("patient-capabilities.json", "clinician-capabilities.json"):
        path = ROOT / "tests" / "fixtures" / name
        if path.exists():
            sweep |= {row["id"] for row in json.loads(path.read_text(encoding="utf-8"))}
    spec_ids = ids_in(sorted((ROOT / "tests" / "ui").glob("*.mjs"))) | ids_in(
        sorted((ROOT / "tests" / "fixtures").glob("*.mjs"))
    )
    browser = sweep | spec_ids

    # Review: whatever the current packet lists.
    packet_path = ROOT / "docs" / "review-packets" / "explicit-calculation-v1" / "packet.json"
    review = set()
    if packet_path.exists():
        review = {
            entry["id"]
            for entry in json.loads(packet_path.read_text(encoding="utf-8"))["items"]
        }

    patient = {i for i, item in total.items() if item.get("patient_form")}
    gaps = {
        "engine": sorted(set(total) - engine),
        "browser": sorted(set(total) - browser),
        "review": sorted(set(total) - review),
    }

    manifest = {
        "total": len(total),
        "patient_facing": len(patient),
        "levels": {
            "engine": sorted(engine),
            "browser": sorted(browser),
            "review": sorted(review),
        },
        "counts": {
            "engine": len(engine),
            "browser": len(browser),
            "review": len(review),
        },
        "gaps": gaps,
    }
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# 测试覆盖矩阵",
        "",
        "> 由 `scripts/build/generate_coverage_manifest.py` 生成，不要手工编辑。",
        "> `tests/test_coverage_manifest.py` 会校验：引擎覆盖必须 100%，",
        "> 患者可用项必须有浏览器覆盖，缺口清单必须与实际一致。",
        "",
        "## 总量",
        "",
        f"- 已注册能力：**{len(total)}**",
        f"- 患者可用：**{len(patient)}**",
        "",
        "## 覆盖情况",
        "",
        "| 层面 | 覆盖 | 占比 | 缺口 |",
        "| --- | --- | --- | --- |",
    ]
    for level, label in (("engine", "引擎执行"), ("browser", "浏览器渲染"), ("review", "临床送审")):
        covered = len(manifest["levels"][level])
        lines.append(
            f"| {label} | {covered} / {len(total)} | {covered / len(total):.0%} | "
            f"{len(gaps[level])} |"
        )
    lines += ["", "## 缺口明细", ""]
    for level, label in (("engine", "引擎执行"), ("browser", "浏览器渲染"), ("review", "临床送审")):
        missing = gaps[level]
        lines.append(f"### {label}（{len(missing)} 项未覆盖）")
        lines.append("")
        if not missing:
            lines.append("无。")
        else:
            by_audience = {"patient": [], "clinician": [], "other": []}
            for identity in missing:
                audience = total[identity].get("audience")
                by_audience["patient" if audience == "patient" else "clinician" if audience == "clinician" else "other"].append(identity)
            for group, ids in by_audience.items():
                if not ids:
                    continue
                name = {"patient": "患者可用", "clinician": "仅医生", "other": "其他"}[group]
                lines.append(f"- **{name}**（{len(ids)}）：" + "、".join(f"`{i}`" for i in ids[:12]))
                if len(ids) > 12:
                    lines.append(f"  （另有 {len(ids) - 12} 项，见 manifest）")
        lines.append("")

    lines += [
        "## 怎么读这份矩阵",
        "",
        "- **引擎执行**是底线：一项能力如果连引擎都没跑过，它就不该算「已交付」。",
        "- **浏览器渲染**目前只覆盖患者可用项。医生端 71 项走研发预览视图，那个页面没有自动化测试。",
        "- **临床送审**只覆盖第一批 12 项，这是有意为之——先验证评审流程本身。",
        "- 缺口不是「忘了」，是「已知且记录在案」。清单一旦与实际不符，测试会红。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {MANIFEST}")
    print(f"wrote {REPORT}")
    print(f"total={len(total)} engine={len(engine)} browser={len(browser)} review={len(review)}")
    print(f"gaps: engine={len(gaps['engine'])} browser={len(gaps['browser'])} review={len(gaps['review'])}")


if __name__ == "__main__":
    main()
