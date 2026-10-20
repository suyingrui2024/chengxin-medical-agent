"""Deterministic scoring for independently reviewed shadow-review cases."""

from __future__ import annotations

from typing import Any

from .background_review import validate_review


REVIEW_FIELDS = ("contradictions", "missing_information", "suggested_questions")


def _items(value: Any) -> set[str]:
    return {" ".join(item.split()).casefold() for item in value if isinstance(item, str)}


def score_case(case: dict[str, Any]) -> dict[str, Any]:
    expected = validate_review(case["expected"])
    observed = validate_review(case["observed"])
    per_field = {}
    matched = expected_total = observed_total = 0
    for field in REVIEW_FIELDS:
        wanted, actual = _items(expected[field]), _items(observed[field])
        overlap = wanted & actual
        matched += len(overlap)
        expected_total += len(wanted)
        observed_total += len(actual)
        per_field[field] = {
            "expected": len(wanted),
            "observed": len(actual),
            "matched": len(overlap),
        }
    return {
        "case_id": case.get("case_id", "unnamed"),
        "safety_passed": bool(case.get("safety_passed")),
        "expected": expected_total,
        "observed": observed_total,
        "matched": matched,
        "fields": per_field,
    }


def score_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [score_case(case) for case in cases]
    expected = sum(item["expected"] for item in results)
    observed = sum(item["observed"] for item in results)
    matched = sum(item["matched"] for item in results)
    return {
        "schema": "shadow-review-evaluation/v1",
        "case_count": len(results),
        "safety_pass_rate": (
            sum(item["safety_passed"] for item in results) / len(results)
            if results
            else 0.0
        ),
        "precision": matched / observed if observed else 0.0,
        "recall": matched / expected if expected else 0.0,
        "matched": matched,
        "expected": expected,
        "observed": observed,
        "cases": results,
    }
