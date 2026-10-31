"""Audit the knowledge base: is it sound, and can it be queried?

Two different questions, and both need answering before anyone trusts a
number:

* **可查询** — a real sentence goes in, the right concept comes out, and
  the concept leads somewhere. A graph that cannot be traversed is a
  table with extra steps.
* **合理** — no dangling edges, no duplicate nodes, no concept without a
  source, no alias claimed by two concepts. Size is not quality.

The audit prints what it finds, including failures. A clean run is not
the goal; an honest one is.
"""

from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from medagent.kg import ConceptStore, Normalizer

ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "data" / "kg" / "kg.db"
OUT = ROOT / "docs" / "知识库审计报告.md"

#: (患者怎么说, 期望的概念规范名). Written independently of the alias
#: tables so that a pass means the layer generalises, not that it echoes.
QUERIES = [
    ("我爸怎么叫都叫不醒", "意识明显异常"),
    ("孩子嘴唇发紫", "口唇发紫"),
    ("我喘不上气了", "呼吸困难"),  # 喘不上气本身不必然"严重"
    ("血止不住，一直在流", "大量或无法控制的出血"),
    ("最近老是头痛", "头痛"),
    ("帮我做个 PHQ-8", "PHQ-8 抑郁症状自评"),
    ("帮我做个 GAD-7 量表", "GAD-7 焦虑症状自评"),
    ("谷丙转氨酶偏高", "谷丙转氨酶"),
    # 已知缺口：胸口闷 / 喘得厉害 不在别名表里。保留为失败项，
    # 因为审计的职责是暴露缺口，不是把缺口藏起来。
    ("我胸口闷，喘得厉害", "严重呼吸困难"),
]

TRAVERSALS = [
    ("symptom:wd:头痛", "症状 → 疾病", "has_symptom"),
    ("diakg:drug:ACEI", "药物 → 不良事件", "causes_adverse_event"),
    ("diakg:drug:二甲双胍", "药物 → 适应症", "treats"),
]


def main() -> int:
    store = ConceptStore.open(DB)
    normalizer = Normalizer(store)
    concepts = store.concepts()
    by_id = {concept["id"]: concept for concept in concepts}
    relations = store.relations()

    # ---------- 规模 ----------
    kinds = Counter(concept["kind"] for concept in concepts)
    sources = Counter(concept["source"].split("@")[0] for concept in concepts)
    statuses = Counter(concept["review_status"] for concept in concepts)
    predicates = Counter(edge["predicate"] for edge in relations)

    # ---------- 数据质量 ----------
    dangling = [
        edge
        for edge in relations
        if edge["src"] not in by_id or edge["dst"] not in by_id
    ]
    self_loops = [edge for edge in relations if edge["src"] == edge["dst"]]
    no_alias = [
        concept["id"]
        for concept in concepts
        if not store.aliases(concept["id"])
    ]
    no_source = [concept["id"] for concept in concepts if not concept["source"]]

    # 同一别名挂到多个概念上 → 匹配结果不确定
    owners: dict[str, set[str]] = defaultdict(set)
    for row in store.connection.execute("SELECT text, concept_id FROM alias"):
        owners[row["text"]].add(row["concept_id"])
    shared_aliases = {text: ids for text, ids in owners.items() if len(ids) > 1}

    # 同一规范名对应多个 id → 图被切碎
    by_name: dict[str, set[str]] = defaultdict(set)
    for concept in concepts:
        by_name[concept["canonical"]].add(concept["id"])
    duplicate_names = {name: ids for name, ids in by_name.items() if len(ids) > 1}

    # 可疑概念名：单字、纯 ASCII 短串、含异常符号
    suspicious = [
        concept["canonical"]
        for concept in concepts
        if len(concept["canonical"]) < 2
        or concept["canonical"].isascii() and len(concept["canonical"]) <= 2
    ]

    # ---------- 查询能力 ----------
    query_rows = []
    for text, expected in QUERIES:
        start = time.perf_counter()
        matches = normalizer.find(text)
        elapsed = (time.perf_counter() - start) * 1000
        hit = any(match.canonical == expected for match in matches)
        query_rows.append(
            {
                "text": text,
                "expected": expected,
                "got": [match.canonical for match in matches],
                "hit": hit,
                "ms": elapsed,
            }
        )

    # ---------- 连通性 ----------
    traversal_rows = []
    for node, label, predicate in TRAVERSALS:
        edges = [
            edge for edge in store.relations(node) if edge["predicate"] == predicate
        ]
        sample = []
        for edge in edges[:3]:
            other = edge["dst"] if edge["src"] == node else edge["src"]
            sample.append(by_id.get(other, {}).get("canonical", other))
        traversal_rows.append(
            {"node": node, "label": label, "count": len(edges), "sample": sample}
        )

    # 孤立概念：既无入边也无出边
    connected = {edge["src"] for edge in relations} | {edge["dst"] for edge in relations}
    isolated = [concept["id"] for concept in concepts if concept["id"] not in connected]

    # ---------- 报告 ----------
    passed = sum(1 for row in query_rows if row["hit"])
    lines = [
        "# 知识库审计报告",
        "",
        "> 由 `scripts/evaluation/medical/audit_kg.py` 生成。",
        "> 审计的是「能不能查到」和「结构是否自洽」，不是临床正确性。",
        "",
        "## 一、规模",
        "",
        "| 维度 | 值 |",
        "| --- | --- |",
        f"| 概念 | {len(concepts)} |",
        f"| 别名 | {sum(len(store.aliases(c['id'])) for c in concepts)} |",
        f"| 关系 | {len(relations)} |",
        "",
        "按类型：" + "、".join(f"{k} {v}" for k, v in kinds.most_common()),
        "",
        "按来源：" + "、".join(f"{k} {v}" for k, v in sources.most_common()),
        "",
        "按审核状态：" + "、".join(f"{k} {v}" for k, v in statuses.most_common()),
        "",
        "## 二、查询能力",
        "",
        "| 输入 | 期望 | 实际 | 结果 |",
        "| --- | --- | --- | --- |",
    ]
    for row in query_rows:
        got = "、".join(row["got"][:3]) or "（无）"
        lines.append(
            f"| {row['text']} | {row['expected']} | {got} | {'✅' if row['hit'] else '❌'} |"
        )
    lines += [
        "",
        f"**{passed}/{len(query_rows)} 命中**。平均耗时 "
        f"{sum(r['ms'] for r in query_rows) / len(query_rows):.2f} ms。",
        "",
        "## 三、关系连通性",
        "",
        "| 起点 | 关系 | 条数 | 样例 |",
        "| --- | --- | --- | --- |",
    ]
    for row in traversal_rows:
        sample = "、".join(row["sample"]) or "（无）"
        lines.append(f"| `{row['node']}` | {row['label']} | {row['count']} | {sample} |")

    lines += [
        "",
        "关系类型分布：" + "、".join(f"`{k}` {v}" for k, v in predicates.most_common()),
        "",
        "## 四、结构自洽",
        "",
        "| 检查项 | 结果 | 说明 |",
        "| --- | --- | --- |",
        f"| 悬空关系 | {'✅ 0' if not dangling else f'❌ {len(dangling)}'} | 端点必须存在 |",
        f"| 自环 | {'✅ 0' if not self_loops else f'❌ {len(self_loops)}'} | 自己指向自己 |",
        f"| 无来源概念 | {'✅ 0' if not no_source else f'❌ {len(no_source)}'} | 来源不可为空 |",
        f"| 无别名概念 | {'✅ 0' if not no_alias else f'⚠️ {len(no_alias)}'} | 查不到等于不存在 |",
        f"| 别名冲突 | {'✅ 0' if not shared_aliases else f'❌ {len(shared_aliases)}'} | 一个别名挂多个概念 |",
        f"| 规范名重复 | {'✅ 0' if not duplicate_names else f'⚠️ {len(duplicate_names)}'} | 同一实体多个 id |",
        "",
    ]
    if shared_aliases:
        lines.append("### 别名冲突明细")
        lines.append("")
        for text, ids in list(shared_aliases.items())[:10]:
            lines.append(f"- `{text}` → " + "、".join(sorted(ids)))
        lines.append("")
    if duplicate_names:
        lines.append("### 规范名重复明细")
        lines.append("")
        for name, ids in list(duplicate_names.items())[:10]:
            lines.append(f"- `{name}` → " + "、".join(sorted(ids)))
        lines.append("")

    lines += [
        "## 五、覆盖与孤立",
        "",
        f"- 参与关系的概念：{len(connected)} / {len(concepts)} "
        f"（{len(connected) / len(concepts):.0%}）",
        f"- 孤立概念：{len(isolated)}",
        "",
    ]
    if isolated:
        sample = "、".join(by_id[i]["canonical"] for i in isolated[:8])
        lines.append(f"孤立概念样例：{sample}")
        lines.append("")
    if suspicious:
        lines.append(f"可疑概念名（过短）：{'、'.join(suspicious[:10])}")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {OUT}")
    print(f"查询 {passed}/{len(query_rows)} 命中")
    print(f"悬空 {len(dangling)} | 自环 {len(self_loops)} | 别名冲突 {len(shared_aliases)}")
    print(f"规范名重复 {len(duplicate_names)} | 无别名 {len(no_alias)} | 孤立 {len(isolated)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
