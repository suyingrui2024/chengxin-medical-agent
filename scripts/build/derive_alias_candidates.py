"""Propose colloquial aliases for red-flag concepts, from the triage rules.

Why this exists: the coverage gap is not missing knowledge, it is missing
wording. 「叫不醒」 expresses 昏迷, but 昏迷 is already in the store under
that name and the colloquial form is not an alias anywhere.

The triage layer already knows these phrases — they are the regexes that
fire on them. This script reads those rules and proposes the phrases as
aliases, which is a transfer of knowledge the product already trusts, not
an invention.

What it deliberately does NOT do: read the evaluation corpus. Deriving
aliases from the samples they will be scored against would make the
coverage number meaningless.

Output is a candidate file. Nothing enters the knowledge base until a
human flips ``review_status`` to ``approved``; that gate is the whole
point, because a wrong alias here becomes a wrong red flag in production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ROOT / "src" / "medagent" / "content" / "red_flags.json"
OUT = ROOT / "data" / "kg" / "candidates" / "triage-aliases.tsv"

#: Fragments that are not terms. Body parts, quantifiers, degree words and
#: pieces of a longer phrase that the regex split apart. Excluding these is
#: a judgement about Chinese, not about medicine, so it is safe to keep here.
NOT_A_TERM = {
    "全身", "四肢", "浑身", "嘴唇", "口唇", "唇",
    "大量", "好多", "很多", "全是", "一大片", "一些",
    "厉害", "费劲", "费力", "急促", "窘迫",
    "空气", "说不出", "说不了", "说不完", "说不完整", "说不了完整",
    "过去", "上吊", "上翻", "不动", "不停", "不止", "按不住",
    "没有", "什么", "怎么", "这样", "那样",
}

#: Red flag id -> the concept the alias should attach to.
#: Kept explicit: the flag 「意识明显异常」 is not the same node as 昏迷,
#: and guessing the mapping would be a clinical decision.
CONCEPT_FOR_FLAG = {
    "unresponsive": "red_flag:unresponsive",
    "seizure": "red_flag:seizure",
    "cyanosis": "red_flag:cyanosis",
    "severe_breathing": "red_flag:severe_breathing",
    "chest_pain": "red_flag:chest_pain",
    "major_bleeding": "red_flag:major_bleeding",
}

PURE_LITERAL = re.compile(r"^[^()|.\[\]{}*+?\\]+$")


def candidates_for(pattern: str) -> set[str]:
    """Literal phrases a single rule can match."""
    found: set[str] = set()
    if PURE_LITERAL.match(pattern):
        found.add(pattern)
        return found
    for group in re.findall(r"\(([^()]*)\)", pattern):
        for alternative in group.split("|"):
            alternative = alternative.strip()
            if 2 <= len(alternative) <= 8:
                found.add(alternative)
    return found


def main() -> int:
    payload = json.loads(FLAGS.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str, str]] = []
    for flag in payload["flags"]:
        concept = CONCEPT_FOR_FLAG.get(flag["id"])
        if not concept:
            continue
        phrases: set[str] = set()
        for pattern in flag.get("patterns") or []:
            phrases |= candidates_for(pattern)
        for phrase in sorted(phrases - NOT_A_TERM):
            if phrase == flag["label"]:
                continue
            rows.append((concept, phrase, flag["id"], "pending"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 红旗同义词候选（由 red_flags.json 的正则推导，未经临床审核）",
        "# 列：concept_id\talias\t来源红旗\treview_status",
        "# 审核通过请把 pending 改成 approved；未批准的不会进入知识库。",
    ]
    lines += ["\t".join(row) for row in rows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} candidates={len(rows)}")
    for concept, phrase, flag, _ in rows:
        print(f"  {flag:18s} {phrase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
