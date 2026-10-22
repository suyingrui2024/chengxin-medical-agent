#!/usr/bin/env python3
"""Run the red-flag corpus against the deterministic triage layer.

Measures the emergency phrase gate (TriageService.scan_text) and the
combined assess() level on a blank case, per red-flag and per category.

The corpus is an *engineering* corpus (review_status: unreviewed in the
JSON). Numbers here are engineering signals, not clinical validation.

Usage:
    PYTHONPATH=src python3 scripts/evaluate_red_flags.py \
        [--corpus tests/fixtures/red_flag_corpus_v0.json] [--json-out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from medagent.domain import CaseState  # noqa: E402
from medagent.triage import TriageService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default=str(Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "red_flag_corpus_v0.json"))
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    triage = TriageService()

    rows = []
    for sample in corpus["samples"]:
        text = sample["text"]
        triggers = triage.scan_text(text)
        state = CaseState(session_id="corpus")
        level = triage.assess(state, text).level.value
        got = bool(triggers)
        rows.append({
            "id": sample["id"],
            "category": sample["category"],
            "red_flag_id": sample.get("red_flag_id"),
            "expect_trigger": sample["expect_trigger"],
            "got_trigger": got,
            "triggers": triggers,
            "assess_level": level,
            "ok": got == sample["expect_trigger"],
            "text": text,
        })

    positives = [r for r in rows if r["expect_trigger"]]
    negatives = [r for r in rows if not r["expect_trigger"]]
    misses = [r for r in positives if not r["got_trigger"]]
    false_alarms = [r for r in negatives if r["got_trigger"]]
    recall = (len(positives) - len(misses)) / len(positives) if positives else 1.0
    fpr = len(false_alarms) / len(negatives) if negatives else 0.0

    by_flag = defaultdict(lambda: {"pos": 0, "miss": 0})
    for r in positives:
        by_flag[r["red_flag_id"]]["pos"] += 1
        if not r["got_trigger"]:
            by_flag[r["red_flag_id"]]["miss"] += 1
    by_cat = defaultdict(lambda: {"n": 0, "bad": 0})
    for r in rows:
        by_cat[r["category"]]["n"] += 1
        if not r["ok"]:
            by_cat[r["category"]]["bad"] += 1

    report = {
        "corpus": corpus["corpus"],
        "corpus_version": corpus["version"],
        "review_status": corpus.get("review_status", "unreviewed"),
        "samples": len(rows),
        "positives": len(positives),
        "negatives": len(negatives),
        "recall": round(recall, 4),
        "false_positive_rate": round(fpr, 4),
        "misses": [r["id"] for r in misses],
        "false_alarms": [r["id"] for r in false_alarms],
        "by_red_flag": {k: dict(v) for k, v in sorted(by_flag.items(), key=lambda kv: str(kv[0]))},
        "by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
        "rows": rows,
    }

    print(f"corpus={report['corpus']} v{report['corpus_version']} "
          f"({report['review_status']}) samples={report['samples']}")
    print(f"recall={recall:.3f}  false_positive_rate={fpr:.3f}")
    print("by red flag (missed/positives):")
    for flag, v in report["by_red_flag"].items():
        print(f"  {flag}: {v['miss']}/{v['pos']}")
    print("by category (errors/total):")
    for cat, v in report["by_category"].items():
        print(f"  {cat}: {v['bad']}/{v['n']}")
    if misses:
        print("MISSES:")
        for r in misses:
            print(f"  [{r['id']}] ({r['red_flag_id']}/{r['category']}) {r['text']}")
    if false_alarms:
        print("FALSE ALARMS:")
        for r in false_alarms:
            print(f"  [{r['id']}] ({r['category']}) {r['text']} -> {r['triggers']}")

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
