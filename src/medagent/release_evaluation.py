"""Deterministic scoring contract for clinically reviewed release cases.

The evaluator intentionally does not manufacture a clinical baseline.  A case
must identify an external reviewer and review timestamp before it can count
toward a release report.
"""

from __future__ import annotations

from typing import Any

from .domain import RiskLevel


SCHEMA = "medical-release-evaluation/v1"
_REPLY_KINDS = {
    "question",
    "summary",
    "urgent",
    "emergency",
    "service_error",
    "publication_blocked",
}


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def validate_case(case: Any) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("case must be an object")
    required = {"case_id", "clinical_review", "expected", "observed"}
    if set(case) != required:
        raise ValueError("case fields are invalid")
    if not isinstance(case["case_id"], str) or not case["case_id"].strip():
        raise ValueError("case_id is required")
    review = case["clinical_review"]
    if (
        not isinstance(review, dict)
        or set(review) != {"status", "reviewer_id", "reviewed_at"}
        or review["status"] != "approved"
        or not all(isinstance(review[key], str) and review[key].strip() for key in review)
    ):
        raise ValueError("case requires approved clinical_review metadata")
    expected = case["expected"]
    observed = case["observed"]
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        raise ValueError("expected and observed must be objects")
    expected_keys = {"risk_level", "reply_kinds", "required_event_types"}
    observed_keys = {"risk_level", "reply_kind", "event_types"}
    if set(expected) != expected_keys or set(observed) != observed_keys:
        raise ValueError("expected or observed fields are invalid")
    if expected["risk_level"] not in {item.value for item in RiskLevel}:
        raise ValueError("expected risk_level is invalid")
    if observed["risk_level"] not in {item.value for item in RiskLevel}:
        raise ValueError("observed risk_level is invalid")
    reply_kinds = _strings(expected["reply_kinds"], "reply_kinds")
    if not reply_kinds or any(item not in _REPLY_KINDS for item in reply_kinds):
        raise ValueError("expected reply_kinds are invalid")
    if observed["reply_kind"] not in _REPLY_KINDS:
        raise ValueError("observed reply_kind is invalid")
    _strings(expected["required_event_types"], "required_event_types")
    _strings(observed["event_types"], "event_types")
    return case


def evaluate_cases(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"schema", "cases"}:
        raise ValueError("evaluation payload is invalid")
    if payload["schema"] != SCHEMA:
        raise ValueError("evaluation schema is invalid")
    if not isinstance(payload["cases"], list) or not payload["cases"]:
        raise ValueError("evaluation requires at least one case")
    cases = [validate_case(case) for case in payload["cases"]]
    if len({case["case_id"] for case in cases}) != len(cases):
        raise ValueError("case_id values must be unique")
    results = []
    for case in cases:
        expected, observed = case["expected"], case["observed"]
        checks = {
            "risk_level": observed["risk_level"] == expected["risk_level"],
            "reply_kind": observed["reply_kind"] in expected["reply_kinds"],
            "required_events": set(expected["required_event_types"]).issubset(
                observed["event_types"]
            ),
        }
        results.append({"case_id": case["case_id"], "passed": all(checks.values()), "checks": checks})
    return {
        "schema": "medical-release-evaluation-result/v1",
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "all_passed": all(item["passed"] for item in results),
        "results": results,
    }
