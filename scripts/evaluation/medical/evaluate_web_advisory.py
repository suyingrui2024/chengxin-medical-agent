"""Run the web-advisory case set against medagent.web_tools (offline).

The corpus is an *engineering* corpus (review_status: unreviewed in the
fixture): it verifies the deterministic safety machinery of the web
advisory layer from docs/联网与子智能体工具设计.md §8.1 — source-tier
filtering, verbatim citation fidelity, the outbound PII gate, conflict
and injection detection, and the dosage lint. Provider responses and
fetch bodies are frozen snapshots injected through the WebTools
transport / _http_get seams; no real network request is ever made
(socket.getaddrinfo is replaced for the duration of each fetch case).

Usage:
    python -m scripts.evaluation.medical.evaluate_web_advisory \
        [--cases tests/fixtures/web_advisory_cases_v0.json] \
        [--json-out report.json]

Exit codes: 0 iff every hard gate passes; 1 on a gate failure; 2 when
the module under test is unavailable or the corpus cannot be scored
(setup error, reported without a traceback).
"""
from __future__ import annotations

import sys as _scripts_sys
from pathlib import Path as _ScriptsPath

_scripts_sys.path.insert(0, str(_ScriptsPath(__file__).resolve().parents[3]))

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Minimum corpus sizes (floor of 50% of the corpus as authored:
# 25 cases — tier_filter 4, citation_quote 4, dosage 4, conflict 3,
# pii_gate 5, injection 5 of which 4 expected-suspect). Without them
# an empty or gutted corpus passes on vacuous 1.0 rates: every
# accuracy below defaults to 1.0 when its category has no cases.
MIN_TOTAL_CASES = 12
MIN_CATEGORY_CASES = {
    "tier_filter": 2,
    "citation_quote": 2,
    "dosage": 2,
    "conflict": 1,
    "pii_gate": 2,
    "injection": 2,
}
MIN_INJECTION_POSITIVES = 2

# Fallback copies, used only if medagent.education cannot be imported.
# The canonical caliber lives in medagent.education (DOSE_PATTERN /
# CONFLICT_PHRASES); these literals mirror it so the corpus stays
# scorable against web_tools alone.
_FALLBACK_DOSE_PATTERN = None  # compiled lazily from the same caliber
_FALLBACK_CONFLICT_PHRASES = (
    "无需就医",
    "不需要就医",
    "不用就医",
    "不必就医",
    "无需看医生",
    "不用看医生",
    "不用去医院",
    "不必去医院",
    "在家观察即可",
    "在家休息即可",
)

_PUBLIC_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class SetupFailure(Exception):
    """The module under test is missing or shaped unexpectedly."""


def _load_web_tools():
    try:
        from medagent import web_tools
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        raise SetupFailure(
            f"medagent.web_tools is not available ({type(exc).__name__}: {exc}); "
            "the web advisory layer (Phase B) must land before this evaluation can run"
        ) from exc
    for name in ("WebTools", "scrub_free_query", "tier_for_domain", "make_citation"):
        if not callable(getattr(web_tools, name, None)):
            raise SetupFailure(f"medagent.web_tools.{name} is missing or not callable")
    return web_tools


def _dose_search():
    """Return a text->bool dosage predicate (education caliber if present)."""
    try:
        from medagent import education

        pattern = getattr(education, "DOSE_PATTERN", None)
        if pattern is not None:
            return lambda text: bool(text) and bool(pattern.search(text))
    except Exception:
        pass
    global _FALLBACK_DOSE_PATTERN
    if _FALLBACK_DOSE_PATTERN is None:
        import re

        _FALLBACK_DOSE_PATTERN = re.compile(
            r"\d+(?:\.\d+)?\s*(?:mg|毫克|ml|片|粒|次\s*/\s*日)"
            r"|每日\s*\d+\s*次"
            r"|\d+\s*次\s*/\s*日"
        )
    return lambda text: bool(text) and bool(_FALLBACK_DOSE_PATTERN.search(text))


def _conflict_phrases():
    try:
        from medagent import education

        phrases = getattr(education, "CONFLICT_PHRASES", None)
        if phrases:
            return tuple(phrases)
    except Exception:
        pass
    return _FALLBACK_CONFLICT_PHRASES


def _make_tools(web_tools, snapshot):
    """WebTools whose search transport replays the frozen snapshot."""

    def transport(method, url, headers, body, timeout):
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
        )

    return web_tools.WebTools(
        events=None, api_key="eval-offline", transport=transport
    )


def _fetch(web_tools, case):
    """Drive WebTools.fetch fully offline against the case snapshot."""
    snap = case["fetch_snapshot"]
    tools = web_tools.WebTools(events=None, api_key="eval-offline")
    html_bytes = snap["html"].encode("utf-8")
    content_type = snap.get("content_type", "text/html; charset=utf-8")

    def fake_http_get(url, timeout):
        return 200, {"Content-Type": content_type}, html_bytes

    tools._http_get = fake_http_get  # the module's documented test seam
    original_getaddrinfo = socket.getaddrinfo
    socket.getaddrinfo = lambda *args, **kwargs: list(_PUBLIC_ADDRINFO)
    try:
        return tools.fetch(case["url"])
    finally:
        socket.getaddrinfo = original_getaddrinfo


def _score_tier_filter(web_tools, case):
    tools = _make_tools(web_tools, case["provider_response"])
    result = tools.search(
        case["query"],
        purpose=case["purpose"],
        min_tier=case["min_tier"],
        max_results=8,
    )
    results = result.get("results") or []
    tiers = sorted({item.get("tier") for item in results})
    ok = (
        result.get("status") == "ok"
        and tiers == sorted(case["expected_tiers"])
        and len(results) == case["expected_result_count"]
        and result.get("filtered_below_tier") == case["expected_filtered_below_tier"]
    )
    detail = (
        f"status={result.get('status')} tiers={tiers} "
        f"count={len(results)} filtered={result.get('filtered_below_tier')}"
    )
    return ok, detail, {}


def _score_dosage(web_tools, case, dose_search):
    tools = _make_tools(web_tools, case["provider_response"])
    result = tools.search(
        case["query"],
        purpose=case.get("purpose", "drug_info"),
        min_tier=case.get("min_tier", "T4"),
        max_results=8,
    )
    snippets = [item.get("snippet", "") for item in result.get("results") or []]
    hit = any(dose_search(snippet) for snippet in snippets)
    ok = hit == case["expect_dosage_hit"]
    return ok, f"dosage_hit={hit} snippets={len(snippets)}", {}


def _score_citation(web_tools, case):
    fetched = _fetch(web_tools, case)
    content = fetched.get("content") or ""
    hit = case["quote"] in content
    citation = web_tools.make_citation(fetched, case["quote"])
    citation_ok = (
        citation.get("quote") == case["quote"]
        and citation.get("url") == case["url"]
        and bool(citation.get("citation_id"))
        and citation.get("tier") == fetched.get("tier")
    )
    ok = fetched.get("status") == "ok" and hit == case["expected_verbatim"] and citation_ok
    detail = (
        f"status={fetched.get('status')} verbatim={hit} "
        f"expected={case['expected_verbatim']} citation_ok={citation_ok}"
    )
    return ok, detail, {}


def _score_pii(web_tools, case):
    fragments = tuple(case.get("case_fragments", [])) + tuple(case.get("identifiers", []))
    result = web_tools.scrub_free_query(case["query"], forbidden_fragments=fragments)
    leaks = 0
    if result is not None:
        leaks = sum(result.count(identifier) for identifier in case.get("identifiers", []))
    if case["expect_blocked"]:
        ok = result is None or leaks == 0
    else:
        ok = result is not None and leaks == 0
    detail = f"blocked={result is None} leaks={leaks} result={result!r}"
    return ok, detail, {"pii_leaks": leaks}


def _score_conflict(web_tools, case, phrases):
    fetched = _fetch(web_tools, case)
    content = fetched.get("content") or ""
    flagged = bool(
        fetched.get("conflict") or fetched.get("conflict_detected")
    ) or any(phrase in content for phrase in phrases)
    ok = fetched.get("status") == "ok" and flagged == case["expect_conflict"]
    return ok, f"status={fetched.get('status')} conflict={flagged}", {}


def _score_injection(web_tools, case):
    fetched = _fetch(web_tools, case)
    suspect = bool(fetched.get("injection_suspect"))
    ok = fetched.get("status") == "ok" and suspect == case["expected_suspect"]
    detail = f"status={fetched.get('status')} injection_suspect={suspect}"
    return ok, detail, {}


def score_corpus(corpus) -> dict:
    """Score the whole corpus; never raises for a missing module."""
    report = {
        "corpus": corpus.get("corpus"),
        "corpus_version": corpus.get("version"),
        "review_status": corpus.get("review_status"),
        "web_sources_version": corpus.get("web_sources_version"),
        "cases": 0,
        "passed": 0,
        "fatal": None,
        "gates": {},
        "gate_pass": False,
        "results": [],
    }
    try:
        web_tools = _load_web_tools()
    except SetupFailure as exc:
        report["fatal"] = str(exc)
        return report
    dose_search = _dose_search()
    phrases = _conflict_phrases()
    scorers = {
        "tier_filter": lambda case: _score_tier_filter(web_tools, case),
        "dosage": lambda case: _score_dosage(web_tools, case, dose_search),
        "citation_quote": lambda case: _score_citation(web_tools, case),
        "pii_gate": lambda case: _score_pii(web_tools, case),
        "conflict": lambda case: _score_conflict(web_tools, case, phrases),
        "injection": lambda case: _score_injection(web_tools, case),
    }
    stats = {
        "tier_total": 0,
        "tier_correct": 0,
        "citation_total": 0,
        "citation_correct": 0,
        "pii_total": 0,
        "pii_leaks": 0,
        "injection_total": 0,
        "injection_positives": 0,
        "injection_detected": 0,
        "injection_false_positives": 0,
        "conflict_total": 0,
        "conflict_correct": 0,
        "dosage_total": 0,
        "dosage_correct": 0,
    }
    for case in corpus.get("cases", []):
        scorer = scorers.get(case.get("category"))
        if scorer is None:
            ok, detail, extra = False, f"unknown category {case.get('category')!r}", {}
        else:
            try:
                ok, detail, extra = scorer(case)
            except Exception as exc:  # one bad case must not sink the run
                ok, detail, extra = False, f"{type(exc).__name__}: {exc}", {}
        stats["pii_leaks"] += extra.get("pii_leaks", 0)
        category = case.get("category")
        if category == "tier_filter":
            stats["tier_total"] += 1
            stats["tier_correct"] += int(ok)
        elif category == "citation_quote":
            stats["citation_total"] += 1
            stats["citation_correct"] += int(ok)
        elif category == "dosage":
            stats["dosage_total"] += 1
            stats["dosage_correct"] += int(ok)
        elif category == "conflict":
            stats["conflict_total"] += 1
            stats["conflict_correct"] += int(ok)
        elif category == "pii_gate":
            stats["pii_total"] += 1
        elif category == "injection":
            stats["injection_total"] += 1
            if case["expected_suspect"]:
                stats["injection_positives"] += 1
                stats["injection_detected"] += int(ok)
            elif not ok:
                stats["injection_false_positives"] += 1
        report["results"].append(
            {"id": case.get("id"), "category": category, "ok": ok, "detail": detail}
        )
    report["cases"] = len(report["results"])
    report["passed"] = sum(1 for item in report["results"] if item["ok"])

    def rate(hits, total):
        return hits / total if total else 1.0

    tier_rate = rate(stats["tier_correct"], stats["tier_total"])
    citation_rate = rate(stats["citation_correct"], stats["citation_total"])
    injection_rate = rate(stats["injection_detected"], stats["injection_positives"])
    conflict_rate = rate(stats["conflict_correct"], stats["conflict_total"])
    dosage_rate = rate(stats["dosage_correct"], stats["dosage_total"])
    report.update(
        {
            "tier_filter_accuracy": round(tier_rate, 3),
            "citation_quote_accuracy": round(citation_rate, 3),
            "pii_leaks": stats["pii_leaks"],
            "injection_detection_rate": round(injection_rate, 3),
            "injection_false_positives": stats["injection_false_positives"],
            "conflict_detection_rate": round(conflict_rate, 3),
            "dosage_lint_accuracy": round(dosage_rate, 3),
        }
    )
    category_cases = {
        "tier_filter": stats["tier_total"],
        "citation_quote": stats["citation_total"],
        "dosage": stats["dosage_total"],
        "conflict": stats["conflict_total"],
        "pii_gate": stats["pii_total"],
        "injection": stats["injection_total"],
    }
    report["category_cases"] = category_cases
    report["gates"] = {
        "tier_filter_accuracy==1.0": tier_rate == 1.0,
        "citation_quote_accuracy==1.0": citation_rate == 1.0,
        "pii_leaks==0": stats["pii_leaks"] == 0,
        "injection_detection_rate>=0.95": injection_rate >= 0.95,
        "conflict_detection_rate==1.0": conflict_rate == 1.0,
        "dosage_lint_accuracy==1.0": dosage_rate == 1.0,
        "corpus_min_total_cases": report["cases"] >= MIN_TOTAL_CASES,
        "corpus_min_category_cases": all(
            category_cases[category] >= minimum
            for category, minimum in MIN_CATEGORY_CASES.items()
        ),
        "corpus_min_injection_positives": (
            stats["injection_positives"] >= MIN_INJECTION_POSITIVES
        ),
    }
    report["gate_pass"] = all(report["gates"].values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases", default=str(ROOT / "tests" / "fixtures" / "web_advisory_cases_v0.json")
    )
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    corpus = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    report = score_corpus(corpus)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    if report["fatal"]:
        print(f"evaluate_web_advisory: SETUP ERROR: {report['fatal']}", file=sys.stderr)
        return 2
    return 0 if report["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
