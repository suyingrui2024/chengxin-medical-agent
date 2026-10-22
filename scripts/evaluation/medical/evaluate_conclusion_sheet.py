"""Run the conclusion-sheet case set against the composer.

The corpus is an *engineering* corpus (review_status: unreviewed in the
fixture): it verifies the design's deterministic rules — disposition
mapping, must-not-miss surfacing, abstention, forbidden content — not
clinical ground truth. Phase-B patient display of the directions layer
requires a clinician-reviewed corpus (design doc decision D4).

Usage:
    python -m scripts.evaluation.medical.evaluate_conclusion_sheet \
        [--cases tests/fixtures/conclusion_cases_v0.json] \
        [--json-out report.json]

Exit code 0 iff every hard gate passes (design §8.2): disposition
agreement >= 95%, must-not-miss recall == 100%, harmful content == 0.
"""
from __future__ import annotations

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import json
from pathlib import Path

from medagent.conclusion import RETURN_NOW_IF, compose_conclusion
from medagent.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.followups import build_follow_up_plan

ROOT = Path(__file__).resolve().parents[3]


def build_state(case) -> CaseState:
    state = CaseState(session_id=f"case:{case['id']}")
    state.phase = Phase.CLOSED
    state.closed_reason = case["closed_reason"]
    state.risk = RiskAssessment(RiskLevel(case["risk"]))
    for raw in case["facts"]:
        state.record(
            Fact(
                key=raw["key"],
                value=raw.get("value"),
                assertion=Assertion(raw["assertion"]),
                source=Source.PATIENT,
                turn_id="case",
                evidence=raw.get("evidence") or "",
            )
        )
    state.transcript = [
        {"role": "user", "content": raw.get("evidence") or ""}
        for raw in case["facts"]
    ]
    state.follow_up = build_follow_up_plan(state) or {}
    unknown_details = [
        {"key": raw["key"], "label": raw["key"]}
        for raw in case["facts"]
        if raw["assertion"] == "unknown"
    ]
    summary = {"unknown_details": unknown_details, "return_now_if": RETURN_NOW_IF}
    return state, summary


def score_case(case, sheet):
    failures = []
    if sheet is None:
        return ["no sheet produced"]
    if case.get("expected_tier") and sheet["tier"] != case["expected_tier"]:
        failures.append(f"tier {sheet['tier']} != {case['expected_tier']}")
    if (
        case.get("expected_subject_kind")
        and sheet["subject_kind"] != case["expected_subject_kind"]
    ):
        failures.append("subject_kind mismatch")
    level = sheet["disposition"]["level"]
    if level not in case["expected_disposition"]:
        failures.append(f"disposition {level} not in {case['expected_disposition']}")
    shown = [d["hypothesis_id"] for d in sheet["directions"]]
    for needed in case.get("must_not_miss", []):
        if needed not in shown:
            failures.append(f"must-not-miss {needed} not shown")
    for banned_id in case.get("must_not_show", []):
        if banned_id in shown:
            failures.append(f"{banned_id} shown but must not show")
    if case.get("expected_abstain") is not None:
        if sheet["directions_abstained"] != case["expected_abstain"]:
            failures.append(
                f"abstained={sheet['directions_abstained']} expected {case['expected_abstain']}"
            )
    if case.get("expected_abstain") is False:
        acceptable = set(case.get("acceptable_directions", []))
        for hid in shown:
            if hid not in acceptable:
                failures.append(f"direction {hid} outside acceptable set")
    for gap_key in case.get("expected_gap_keys", []):
        if gap_key not in [g["fact_key"] for g in sheet["gaps"]]:
            failures.append(f"gap {gap_key} missing")
    rendered = json.dumps(sheet, ensure_ascii=False)
    for phrase in case.get("forbidden", []):
        if phrase in rendered:
            failures.append(f"forbidden content present: {phrase}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", default=str(ROOT / "tests" / "fixtures" / "conclusion_cases_v0.json")
    )
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    corpus = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    results = []
    disposition_hits = 0
    must_not_miss_total = 0
    must_not_miss_hits = 0
    harmful = 0
    for case in corpus["cases"]:
        state, summary = build_state(case)
        sheet = compose_conclusion(state, summary=summary)
        failures = score_case(case, sheet)
        shown = [d["hypothesis_id"] for d in (sheet or {}).get("directions", [])]
        level = (sheet or {}).get("disposition", {}).get("level")
        if level in case["expected_disposition"]:
            disposition_hits += 1
        for needed in case.get("must_not_miss", []):
            must_not_miss_total += 1
            if needed in shown:
                must_not_miss_hits += 1
        if any(f.startswith("forbidden") for f in failures):
            harmful += 1
        results.append(
            {
                "id": case["id"],
                "ok": not failures,
                "failures": failures,
                "disposition": level,
                "directions": shown,
                "abstained": (sheet or {}).get("directions_abstained"),
            }
        )
    total = len(results)
    disposition_rate = disposition_hits / total if total else 0
    mnm_rate = (
        must_not_miss_hits / must_not_miss_total if must_not_miss_total else 1.0
    )
    gates = {
        "disposition_agreement>=0.95": disposition_rate >= 0.95,
        "must_not_miss_recall==1.0": mnm_rate == 1.0,
        "harmful==0": harmful == 0,
    }
    report = {
        "corpus": corpus["corpus"],
        "corpus_version": corpus["version"],
        "review_status": corpus.get("review_status"),
        "cases": total,
        "passed": sum(1 for r in results if r["ok"]),
        "disposition_agreement": round(disposition_rate, 3),
        "must_not_miss_recall": round(mnm_rate, 3),
        "harmful_cases": harmful,
        "gates": gates,
        "gate_pass": all(gates.values()),
        "results": results,
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
