"""Measure the knowledge base against the red-flag corpus.

The question this answers is not "how many concepts did we import" but
"does the graph see anything useful in the sentences the product actually
has to handle". The corpus is the only reviewed-ish clinical text in the
repository (51 samples, five categories), so it is the honest test set.

Two numbers matter:

* **coverage** — on samples that should trigger, how often does the graph
  recognise at least one clinical concept. A triage layer cannot reason
  about a symptom it never saw.
* **negation blindness** — on samples that must *not* trigger, how often
  the graph still matches. This is not a bug in the matcher: "没有胸痛"
  genuinely contains 胸痛. It is the reason the graph stays in shadow
  mode, and the number is what any future integration has to beat.

Run: PYTHONPATH=src python3 scripts/evaluation/medical/evaluate_kg_coverage.py
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from medagent.kg import ConceptStore, Normalizer

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "tests" / "fixtures" / "red_flag_corpus_v0.json"
DB = ROOT / "data" / "kg" / "kg.db"
OUT = ROOT / "docs" / "知识库覆盖评估.md"


def main() -> int:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    samples = corpus["samples"]
    store = ConceptStore.open(DB)
    normalizer = Normalizer(store)

    rows = []
    for sample in samples:
        matches = normalizer.find(sample["text"])
        # A red-flag sample is "seen" when at least one recognised concept
        # is clinical — a scale name in a benign sentence is not evidence.
        clinical = [m for m in matches if m.kind in {"symptom", "disease", "red_flag"}]
        rows.append(
            {
                **sample,
                "matched": [(m.text, m.canonical, m.kind) for m in matches],
                "clinical": [(m.text, m.kind) for m in clinical],
            }
        )

    positives = [r for r in rows if r["expect_trigger"]]
    negations = [r for r in rows if not r["expect_trigger"] and r["category"] == "negation"]
    benign = [r for r in rows if r["category"] == "benign"]

    def rate(group, predicate):
        if not group:
            return 0.0
        return sum(1 for row in group if predicate(row)) / len(group)

    has_clinical = lambda row: bool(row["clinical"])  # noqa: E731
    has_any = lambda row: bool(row["matched"])  # noqa: E731

    by_flag = defaultdict(lambda: {"total": 0, "seen": 0})
    for row in positives:
        key = row.get("red_flag_id") or "?"
        by_flag[key]["total"] += 1
        by_flag[key]["seen"] += 1 if has_clinical(row) else 0

    kinds = Counter(kind for row in rows for _, kind in row["clinical"])

    summary = {
        "samples": len(rows),
        "positives": len(positives),
        "coverage_positives": rate(positives, has_clinical),
        "negations": len(negations),
        "spurious_negations": rate(negations, has_any),
        "benign": len(benign),
        "noise_benign": rate(benign, has_any),
    }

    lines = [
        "# 知识库覆盖评估",
        "",
        "> 由 `scripts/evaluation/medical/evaluate_kg_coverage.py` 生成。",
        "> 语料 `red_flag_corpus_v0.json` 本身标注为 **unreviewed**，下列数字是工程信号，不是临床结论。",
        "",
        "## 总览",
        "",
        "| 指标 | 结果 |",
        "| --- | --- |",
        f"| 语料样本 | {summary['samples']} |",
        f"| 应触发样本中，图谱识别到临床概念 | **{summary['coverage_positives']:.0%}**（{sum(1 for r in positives if has_clinical(r))}/{summary['positives']}） |",
        f"| 否定样本中，图谱仍然命中 | **{summary['spurious_negations']:.0%}**（{sum(1 for r in negations if has_any(r))}/{summary['negations']}） |",
        f"| 良性样本中，图谱产生命中 | {summary['noise_benign']:.0%}（{sum(1 for r in benign if has_any(r))}/{summary['benign']}） |",
        "",
        "## 按红旗分组",
        "",
        "| 红旗 | 识别 | 样本 |",
        "| --- | --- | --- |",
    ]
    for flag, stat in sorted(by_flag.items()):
        lines.append(f"| {flag} | {stat['seen']}/{stat['total']} | {stat['total']} |")

    lines += [
        "",
        "## 识别到的概念类型",
        "",
    ]
    for kind, count in kinds.most_common():
        lines.append(f"- {kind}: {count}")

    lines += [
        "",
        "## 未识别的应触发样本",
        "",
        "这些句子里图谱没有认出任何临床概念——要么措辞太口语，要么术语不在库里。",
        "",
    ]
    missed = [r for r in positives if not has_clinical(r)]
    if not missed:
        lines.append("无。")
    for row in missed:
        lines.append(f"- `{row['id']}`（{row.get('red_flag_id')}）：{row['text']}")

    lines += [
        "",
        "## 否定样本：命中了什么",
        "",
        "图谱没有否定判断能力，这些命中是预期内的，也正是它只能做影子观察的原因。",
        "",
    ]
    for row in negations:
        hits = "、".join(text for text, _ in row["clinical"]) or "（无）"
        lines.append(f"- `{row['id']}`：{row['text']} → {hits}")

    lines += [
        "",
        "## 结论",
        "",
        f"1. 应触发样本中图谱只认出 **{summary['coverage_positives']:.0%}**，远低于可用水平。",
        "   两条机械规则（剥离 ICD 标题修饰前缀、按「和/与」拆分合并标题）",
        "   把它从 19% 提到 32%，剩下的缺口不是数据量问题，是**词形问题**。",
        f"2. 否定样本中 **{summary['spurious_negations']:.0%}** 仍然命中——图谱没有否定判断能力。",
        "   这不是匹配器的 bug：「没有胸痛」确实含有「胸痛」。它只能做影子观察。",
        "3. 漏识别集中在口语：叫不醒、嘴唇发紫、喘不上气、血止不住。",
        "   这些不会出现在任何 ICD 标题里，只能靠人工整理的问诊同义词表。",
        "4. 免费 ICD-10 中文数据是**类目级**（3 位编码 1,502 条 + 4 位 84 条），",
        "   不是完整亚目——「呕血」(K92.0)、「便血」(K92.1) 都不在库中。",
        "",
        "## 对下一步的含义",
        "",
        "继续导入更多 ICD/本体**不会**明显提升这个数字。真正能提升它的是",
        "一份 200–500 条的问诊口语同义词表，而这必须由临床方提供或签字。",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {OUT}")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
