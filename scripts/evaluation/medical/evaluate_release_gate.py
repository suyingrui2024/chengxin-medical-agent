#!/usr/bin/env python3
"""Release gate (design doc §5): red-flag corpus thresholds.

Runs both engineering corpora through TriageService and fails (exit 1)
unless every threshold holds:
  - overall recall >= 0.99 on each corpus
  - per-red-flag recall >= 0.95 on each corpus
  - false-positive rate <= 0.05 on each corpus

These corpora are unreviewed engineering assets; passing this gate is
necessary, never sufficient, for a clinical release. The clinician-case
gate remains scripts/evaluation/medical/evaluate_medical_release.py, and the behaviour
baseline is the pytest suite (tests/test_triage_layered.py embeds the
same corpora as tests).

    PYTHONPATH=src python3 scripts/evaluation/medical/evaluate_release_gate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from medagent.triage import TriageService  # noqa: E402

CORPORA = ["red_flag_corpus_v0.json", "red_flag_probe_v1.json"]
MIN_RECALL = 0.99
MIN_PER_FLAG_RECALL = 0.95
MAX_FPR = 0.05


def check_corpus(service: TriageService, path: Path) -> bool:
    corpus = json.loads(path.read_text(encoding="utf-8"))
    positives = negatives = tp = fp = 0
    per_flag: dict[str, list[int]] = {}
    for sample in corpus["samples"]:
        got = bool(service.scan_text(sample["text"]))
        flag = sample.get("red_flag_id") or "-"
        if sample["expect_trigger"]:
            positives += 1
            per_flag.setdefault(flag, [0, 0])
            per_flag[flag][1] += 1
            if got:
                tp += 1
                per_flag[flag][0] += 1
        else:
            negatives += 1
            if got:
                fp += 1
    recall = tp / positives if positives else 1.0
    fpr = fp / negatives if negatives else 0.0
    ok = recall >= MIN_RECALL and fpr <= MAX_FPR
    print(f"{path.name}: recall={recall:.3f} fpr={fpr:.3f} -> {'PASS' if ok else 'FAIL'}")
    for flag, (hits, total) in sorted(per_flag.items()):
        flag_recall = hits / total if total else 1.0
        flag_ok = flag_recall >= MIN_PER_FLAG_RECALL
        ok = ok and flag_ok
        print(f"  {flag}: recall={flag_recall:.3f} ({hits}/{total}) {'PASS' if flag_ok else 'FAIL'}")
    return ok


def check_conclusion_sheet(fixtures: Path) -> bool:
    """Conclusion-sheet hard gates (design: 结论单 §8.2): disposition
    agreement >= 0.95, must-not-miss recall == 1.0, harmful == 0, over the
    engineering case set (unreviewed; clinician corpus required for the
    phase-B directions display gate)."""
    sys.path.insert(0, str(ROOT))
    from scripts.evaluation.medical.evaluate_conclusion_sheet import (
        build_state,
        score_case,
    )
    from medagent.conclusion import compose_conclusion

    corpus = json.loads(
        (fixtures / "conclusion_cases_v0.json").read_text(encoding="utf-8")
    )
    total = disposition_hits = mnm_total = mnm_hits = harmful = 0
    for case in corpus["cases"]:
        state, summary = build_state(case)
        sheet = compose_conclusion(state, summary=summary)
        failures = score_case(case, sheet)
        total += 1
        if (sheet or {}).get("disposition", {}).get("level") in case[
            "expected_disposition"
        ]:
            disposition_hits += 1
        shown = [d["hypothesis_id"] for d in (sheet or {}).get("directions", [])]
        for needed in case.get("must_not_miss", []):
            mnm_total += 1
            if needed in shown:
                mnm_hits += 1
        if any(f.startswith("forbidden") for f in failures):
            harmful += 1
    agreement = disposition_hits / total if total else 0.0
    recall = mnm_hits / mnm_total if mnm_total else 1.0
    ok = agreement >= 0.95 and recall == 1.0 and harmful == 0
    print(
        f"conclusion_cases_v0.json: disposition={agreement:.3f} "
        f"must_not_miss={recall:.3f} harmful={harmful} -> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def main() -> int:
    service = TriageService()
    fixtures = ROOT / "tests" / "fixtures"
    results = [check_corpus(service, fixtures / name) for name in CORPORA]
    results.append(check_conclusion_sheet(fixtures))
    print("release gate:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
