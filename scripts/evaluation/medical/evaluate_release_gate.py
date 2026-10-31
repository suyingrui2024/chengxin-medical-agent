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

# Minimum corpus sizes (floor of 50% of each corpus as authored:
# red_flag_corpus_v0 51 samples / 37 positive / 14 negative;
# red_flag_probe_v1 18 / 12 / 6). Without them an empty or gutted
# corpus passes vacuously — recall defaults to 1.0 with no positives
# and the false-positive rate to 0.0 with no negatives.
MIN_CORPUS_SAMPLES = {
    "red_flag_corpus_v0.json": {"samples": 25, "positives": 18, "negatives": 7},
    "red_flag_probe_v1.json": {"samples": 9, "positives": 6, "negatives": 3},
}

# Conclusion corpus: minimum return_now_if expectations the fixture
# must carry (and meet) so the safety-net lists stay asserted.
MIN_RETURN_NOW_IF_EXPECTATIONS = 2


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
    minimums = MIN_CORPUS_SAMPLES.get(path.name)
    if minimums is not None:
        size_ok = (
            len(corpus["samples"]) >= minimums["samples"]
            and positives >= minimums["positives"]
            and negatives >= minimums["negatives"]
        )
        ok = ok and size_ok
        print(
            f"  corpus size: samples={len(corpus['samples'])} "
            f"positives={positives} negatives={negatives} "
            f"(minimums {minimums['samples']}/{minimums['positives']}/"
            f"{minimums['negatives']}) -> {'PASS' if size_ok else 'FAIL'}"
        )
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
        MIN_CASES,
        MIN_MUST_NOT_MISS,
        build_state,
        score_case,
    )
    from medagent.conclusion import compose_conclusion

    corpus = json.loads(
        (fixtures / "conclusion_cases_v0.json").read_text(encoding="utf-8")
    )
    # Freshness binding: the corpus must name the differentials version
    # it was validated against, so a content upgrade cannot leave the
    # evaluation silently stale behind a green gate.
    differentials = json.loads(
        (ROOT / "src" / "medagent" / "content" / "differentials.json").read_text(
            encoding="utf-8"
        )
    )
    bound_version = corpus.get("differentials_version")
    if bound_version != differentials.get("version"):
        print(
            "conclusion_cases_v0.json: corpus bound to differentials "
            f"{bound_version!r} but content is {differentials.get('version')!r} -> FAIL"
        )
        return False
    # Same binding for the self-care content: the safety-net lists the
    # corpus asserts (expected_return_now_if) come from self_care.json.
    self_care = json.loads(
        (ROOT / "src" / "medagent" / "content" / "self_care.json").read_text(
            encoding="utf-8"
        )
    )
    bound_self_care = corpus.get("self_care_version")
    if bound_self_care != self_care.get("version"):
        print(
            "conclusion_cases_v0.json: corpus bound to self_care "
            f"{bound_self_care!r} but content is {self_care.get('version')!r} -> FAIL"
        )
        return False
    print(
        "conclusion corpus review_status: "
        f"{corpus.get('review_status', 'unknown')} "
        "(engineering gate only — not a clinical sign-off)"
    )
    # Course coverage (P1 §3.3): every self-care group must carry a
    # non-empty course text, or the conclusion sheet's outlook section
    # silently disappears for that pathway. 100% coverage or FAIL,
    # naming the groups that are missing text.
    self_care_groups = self_care.get("groups", [])
    missing_course = [
        group.get("id")
        for group in self_care_groups
        if not str(group.get("course") or "").strip()
    ]
    course_ok = not missing_course
    if not course_ok:
        print(
            "self_care.json: course empty for groups "
            f"{missing_course} -> FAIL"
        )
    total = disposition_hits = mnm_total = mnm_hits = harmful = 0
    rni_total = rni_hits = 0
    narrative_failures = department_failures = 0
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
        if any(f.startswith("narrative") for f in failures):
            narrative_failures += 1
        if any(f.startswith("department") for f in failures):
            department_failures += 1
        expected_rni = case.get("expected_return_now_if")
        if expected_rni is not None:
            rni_total += 1
            if (sheet or {}).get("return_now_if") == list(expected_rni):
                rni_hits += 1
    agreement = disposition_hits / total if total else 0.0
    recall = mnm_hits / mnm_total if mnm_total else 1.0
    # Minimum-sample assertions: an empty or gutted corpus must fail,
    # not pass on the vacuous defaults above (recall 1.0 with no
    # must-not-miss expectations, agreement over a handful of cases).
    size_ok = total >= MIN_CASES and mnm_total >= MIN_MUST_NOT_MISS
    rni_ok = (
        rni_total >= MIN_RETURN_NOW_IF_EXPECTATIONS and rni_hits == rni_total
    )
    ok = (
        agreement >= 0.95
        and recall == 1.0
        and harmful == 0
        and size_ok
        and rni_ok
        and course_ok
        and narrative_failures == 0
        and department_failures == 0
    )
    print(
        f"conclusion_cases_v0.json: disposition={agreement:.3f} "
        f"must_not_miss={recall:.3f} harmful={harmful} "
        f"cases={total} (min {MIN_CASES}) mnm={mnm_total} (min {MIN_MUST_NOT_MISS}) "
        f"return_now_if={rni_hits}/{rni_total} "
        f"course_coverage={len(self_care_groups) - len(missing_course)}/"
        f"{len(self_care_groups)} "
        f"narrative_failures={narrative_failures} "
        f"department_failures={department_failures} "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def check_web_advisory(fixtures: Path) -> bool:
    """Web advisory hard gates (design: 联网与子智能体工具设计 §8.1):
    tier filtering and citation fidelity exact, zero PII leaks,
    injection detection >= 0.95, conflict detection exact — all offline
    against frozen provider/fetch snapshots."""
    sys.path.insert(0, str(ROOT))
    corpus = json.loads(
        (fixtures / "web_advisory_cases_v0.json").read_text(encoding="utf-8")
    )
    # Freshness binding, same discipline as the conclusion corpus: the
    # corpus must name the web-sources registry version it was authored
    # against, so a registry upgrade cannot leave it silently stale.
    bound_version = corpus.get("web_sources_version")
    actual_version = None
    try:
        from medagent import web_tools

        actual_version = web_tools.sources_version()
    except Exception:
        sources_path = ROOT / "src" / "medagent" / "content" / "web_sources.json"
        if sources_path.exists():
            actual_version = json.loads(
                sources_path.read_text(encoding="utf-8")
            ).get("version")
    if bound_version != actual_version:
        print(
            "web_advisory_cases_v0.json: corpus bound to web_sources "
            f"{bound_version!r} but registry is {actual_version!r} -> FAIL"
        )
        return False
    print(
        "web advisory corpus review_status: "
        f"{corpus.get('review_status', 'unknown')} "
        "(engineering gate only — not a clinical sign-off)"
    )
    from scripts.evaluation.medical.evaluate_web_advisory import score_corpus

    report = score_corpus(corpus)
    if report.get("fatal"):
        print(f"web_advisory_cases_v0.json: {report['fatal']} -> FAIL")
        return False
    ok = bool(report["gate_pass"])
    print(
        f"web_advisory_cases_v0.json: tier={report['tier_filter_accuracy']:.3f} "
        f"citation={report['citation_quote_accuracy']:.3f} "
        f"pii_leaks={report['pii_leaks']} "
        f"injection={report['injection_detection_rate']:.3f} "
        f"conflict={report['conflict_detection_rate']:.3f} "
        f"dosage={report['dosage_lint_accuracy']:.3f} "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def check_subagents(fixtures: Path) -> bool:
    """Subagent validation hard gates (design §8.2): schema first-pass
    exact, unverified-quote leaks == 0 (red line), fabricated-quote drop
    rate == 1.0, staleness judgement correct — offline scripted stub."""
    sys.path.insert(0, str(ROOT))
    corpus = json.loads(
        (fixtures / "subagent_cases_v0.json").read_text(encoding="utf-8")
    )
    print(
        "subagent corpus review_status: "
        f"{corpus.get('review_status', 'unknown')} "
        "(engineering gate only — not a clinical sign-off)"
    )
    from scripts.evaluation.medical.evaluate_subagents import score_corpus

    report = score_corpus(corpus)
    if report.get("fatal"):
        print(f"subagent_cases_v0.json: {report['fatal']} -> FAIL")
        return False
    ok = bool(report["gate_pass"])
    print(
        f"subagent_cases_v0.json: schema_first_pass={report['schema_first_pass_rate']:.3f} "
        f"unverified_leaks={report['unverified_quote_leaks']} "
        f"fabricated_dropped={report['fabricated_quote_drop_rate']:.3f} "
        f"stale={report['stale_correct']}/{report['stale_cases']} "
        f"kinds={report.get('cases_per_kind')} "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def check_report_interpretation(fixtures: Path) -> bool:
    """P3 report-interpretation corpus (all_cases_pass discipline).

    Every corpus case is graded by the real medagent.report_interpretation
    module — the gate never re-implements grading, so it cannot pass
    against itself. A single expected-grade mismatch flips the gate.
    Unconfirmed observations must land in `excluded`, never in `items`.
    """
    from medagent.report_interpretation import build_interpretation

    fixture = json.loads(
        (fixtures / "report_interpretation_cases.json").read_text(encoding="utf-8")
    )
    ranges = json.loads(
        (ROOT / "src" / "medagent" / "content" / "reference_ranges.json").read_text(
            encoding="utf-8"
        )
    )
    cases = fixture["cases"]
    confirmed = {"patient_confirmed", "patient_corrected"}
    names = {key: entry["name"] for key, entry in ranges["items"].items()}
    failures: list[str] = []
    for case in cases:
        observations = [
            {
                "observation_id": f"{case['id']}-{index}",
                "current_revision_id": "r1",
                "item_key": obs["item_key"],
                "display_name": names.get(obs["item_key"], obs["item_key"]),
                "value_text": str(obs["value"]),
                "unit": obs["unit"],
                "verification_status": obs["verification_status"],
            }
            for index, obs in enumerate(case["observations"])
        ]
        result = build_interpretation(f"gate-{case['id']}", observations, ranges)
        grades = {
            entry["item_key"]: entry["grade"]
            for entry in result["items"]
            if entry.get("item_key") is not None
        }
        none_grades = [
            entry["grade"]
            for entry in result["items"]
            if entry.get("item_key") is None
        ]
        excluded_refs = {
            entry.get("observation_ref") for entry in result.get("excluded", [])
        }
        for index, obs in enumerate(case["observations"]):
            expected = obs["expected_grade"]
            if obs["verification_status"] not in confirmed:
                if obs["item_key"] in grades or (
                    f"{case['id']}-{index}" not in excluded_refs
                ):
                    failures.append(f"{case['id']}:{obs['item_key']} unconfirmed")
            elif obs["item_key"] not in ranges["items"]:
                if expected != "无法判断" or "无法判断" not in none_grades:
                    failures.append(f"{case['id']}:{obs['item_key']} no-range")
            elif grades.get(obs["item_key"]) != expected:
                failures.append(
                    f"{case['id']}:{obs['item_key']} "
                    f"got={grades.get(obs['item_key'])} expected={expected}"
                )
    ok = len(cases) >= 20 and not failures
    print(
        f"report_interpretation_cases.json: {len(cases)} cases "
        f"mismatches={len(failures)} "
        f"{'; '.join(failures[:5])} -> {'PASS' if ok else 'FAIL'}"
    )
    return ok


def check_calculations() -> bool:
    """Engineering invariants for every registered calculation capability.

    Clinical review is a separate, human gate. What this can prove is that
    the content is internally sound: each item declares a source, each
    scale can actually reach its declared range, each formula reproduces
    its published worked examples, and no patient-visible band label
    carries a diagnostic phrase.
    """
    from medagent.conclusion import BANNED_PHRASES
    from medagent.extensions.builtin import load_registry
    from medagent.extensions.formula_engine import run_formula, validate_formula
    from medagent.extensions.formulas import FORMULAS
    from medagent.extensions.scale_engine import ScaleInputError, run_scale, validate_scale

    registry = load_registry()
    items = registry.descriptions()
    problems: list[str] = []

    # Sources.
    for item in items:
        if not item.get("references"):
            problems.append(f"{item['id']}: 缺少来源")

    # Aliases: unique, present, short.
    seen: dict[str, str] = {}
    for item in items:
        if not (isinstance(item.get("scale"), dict) or isinstance(item.get("formula"), dict)):
            continue
        aliases = item.get("aliases") or []
        if not aliases:
            problems.append(f"{item['id']}: 缺少触发别名")
        for alias in aliases:
            key = alias.lower()
            if key in seen:
                problems.append(f"{alias!r} 同时属于 {seen[key]} 与 {item['id']}")
            seen[key] = item["id"]

    # Scales: reachable range, extremes accepted, gaps refused, safe labels.
    scales = 0
    for item in items:
        if not isinstance(item.get("scale"), dict):
            continue
        spec = validate_scale(item)
        low, high = {}, {}
        reachable_min = reachable_max = 0
        for entry in spec["items"]:
            if entry["kind"] == "choice":
                scores = {}
                for option in entry["options"]:
                    reverse = entry.get("reverse_of")
                    base = reverse - option["value"] if reverse is not None else option["value"]
                    scores[option["value"]] = base * entry["weight"]
                low_value = min(scores, key=lambda v: scores[v])
                high_value = max(scores, key=lambda v: scores[v])
                low[entry["id"]] = low_value
                high[entry["id"]] = high_value
                reachable_min += scores[low_value]
                reachable_max += scores[high_value]
            else:
                values = [e["score"] * entry["weight"] for e in entry["thresholds"]]
                reachable_min += min(values)
                reachable_max += max(values)
                best = min(entry["thresholds"], key=lambda e: e["score"])
                low[entry["id"]] = best.get("gte", best.get("lt", 1)) + 1
                high[entry["id"]] = best.get("lt", best.get("gte", 0)) - 1
        if reachable_min != spec["min"] or reachable_max != spec["max"]:
            problems.append(
                f"{item['id']}: 可达 {reachable_min}-{reachable_max} 与声明 "
                f"{spec['min']}-{spec['max']} 不符"
            )
        try:
            run_scale(spec, low)
            run_scale(spec, high)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{item['id']}: 极端答案被拒（{exc}）")
        partial = dict(low)
        partial.pop(spec["items"][0]["id"])
        try:
            run_scale(spec, partial)
            problems.append(f"{item['id']}: 缺项仍算出结果")
        except ScaleInputError:
            pass
        for band in spec["bands"]:
            for phrase in BANNED_PHRASES:
                if phrase in band["label"]:
                    problems.append(f"{item['id']}: 分档词含禁用表述 {phrase!r}")
        scales += 1

    # Formulas: published worked examples must reproduce exactly.
    formulas = 0
    for key, spec in FORMULAS.items():
        manifest = next((i for i in items if i["id"] == f"calculate.{key}"), None)
        if manifest is None:
            problems.append(f"calculate.{key}: 公式已实现但未注册")
            continue
        validated = validate_formula(manifest)
        for values, expected in spec.audit_cases:
            got = run_formula(validated, values)["value"]
            if got != expected:
                problems.append(f"calculate.{key}: {values} 得 {got}，期望 {expected}")
        formulas += 1

    ok = not problems
    print(
        f"calculations: capabilities={len(items)} scales={scales} formulas={formulas} "
        f"problems={len(problems)} -> {'PASS' if ok else 'FAIL'}"
    )
    for problem in problems[:20]:
        print("   -", problem)
    return ok


def main() -> int:
    service = TriageService()
    fixtures = ROOT / "tests" / "fixtures"
    results = [check_corpus(service, fixtures / name) for name in CORPORA]
    results.append(check_conclusion_sheet(fixtures))
    results.append(check_web_advisory(fixtures))
    results.append(check_subagents(fixtures))
    results.append(check_report_interpretation(fixtures))
    results.append(check_calculations())
    print("release gate:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
