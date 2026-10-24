"""Corpus tests for P3a report interpretation (design doc §5.1, C13).

Two layers, kept strictly separate:

1. Schema / fixture validation (no grading module needed): the
   reference-range table is well formed and the synthetic corpus is
   internally consistent with the grading rules documented in the fixture.

2. Grading tests against P3-A's ``medagent.report_interpretation``:
   ``build_interpretation(entry_id, observations, ranges)`` where
   observations use the stored shape (observation_id /
   current_revision_id / verification_status / display_name / value_text
   / unit, plus an explicit ``item_key`` which P3-A resolves first).
   This file deliberately implements NO grading logic of its own:
   duplicating the grader here would let the corpus pass against itself
   instead of against P3-A's module.

Known P3-A seam facts (read from the module, not assumed):
- Both ref bounds are required: an item with a missing ref_low or
  ref_high grades 无法判断. Single-sided items therefore carry a
  structural ref_low of 0 in the table (non-negative floor, not a
  clinical bound), documented in the table note.
- Red is reached only at/above red_high (or at/below red_low); the gap
  between the yellow band edge and the red threshold grades 无法判断.
  The corpus avoids that gap for its 红 cases (creatinine 320, not 200)
  and flags the gap as an open seam for the coordinator to adjudicate:
  if P3-A is changed to a continuous red band, no corpus 红 expectation
  changes, but a gap case would need to be added.
- Unconfirmed observations land in the output ``excluded`` list, not in
  ``items``; the grading test accepts that as 无法判断. Confirmed
  observations on items absent from the table land in ``items`` with
  item_key None and grade 无法判断; the test accepts that too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RANGES_PATH = REPO_ROOT / "src" / "medagent" / "content" / "reference_ranges.json"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "report_interpretation_cases.json"

GRADES = {"绿", "黄", "红", "无法判断"}
CONFIRMED = {"patient_confirmed", "patient_corrected"}
REQUIRED_CATEGORIES = {
    "normal",
    "single_abnormal",
    "multi_abnormal",
    "unconfirmed",
    "no_range",
    "unit_conversion",
}
# Item keys the corpus uses that are intentionally absent from the table.
NO_RANGE_KEYS = {"vitamin_d", "cea"}


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def test_reference_ranges_schema() -> None:
    data = _load_json(RANGES_PATH)
    assert data["version"] == "1.0"
    # The engineering-v1 / not-clinically-reviewed disclaimer is mandatory.
    assert "engineering-v1" in data["note"] and "未经临床评审" in data["note"]
    items = data["items"]
    assert len(items) >= 12
    expected_keys = {
        "hemoglobin",
        "white_blood_cells",
        "platelet_count",
        "fasting_glucose",
        "total_cholesterol",
        "triglycerides",
        "alt",
        "ast",
        "creatinine",
        "urea",
        "tsh",
        "potassium",
    }
    assert expected_keys <= set(items)
    for key, entry in items.items():
        # snake_case: lowercase letters/digits, single underscores only.
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", key), key
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["unit"], str) and entry["unit"]
        # P3-A seam: both ref bounds are required, else the item grades
        # 无法判断 outright. Single-sided items use a structural 0 floor.
        assert entry["ref_low"] is not None and entry["ref_high"] is not None
        low_chain = [
            entry["red_low"],
            entry["yellow_low"],
            entry["ref_low"],
        ]
        present_low = [v for v in low_chain if v is not None]
        assert present_low == sorted(present_low), key
        high_chain = [
            entry["ref_high"],
            entry["yellow_high"],
            entry["red_high"],
        ]
        present_high = [v for v in high_chain if v is not None]
        assert present_high == sorted(present_high), key
        if entry["ref_low"] is not None:
            assert entry["ref_low"] < entry["ref_high"], key


def test_corpus_shape_and_coverage() -> None:
    fixture = _load_json(FIXTURE_PATH)
    cases = fixture["cases"]
    assert len(cases) >= 20
    by_category: dict[str, int] = {}
    seen_ids = set()
    for case in cases:
        assert case["id"] not in seen_ids
        seen_ids.add(case["id"])
        by_category[case["category"]] = by_category.get(case["category"], 0) + 1
        assert case["observations"], case["id"]
        for obs in case["observations"]:
            assert obs["expected_grade"] in GRADES, (case["id"], obs)
            assert isinstance(obs["value"], (int, float)), (case["id"], obs)
            assert obs["verification_status"] in CONFIRMED | {
                "needs_confirmation",
                "model_extracted",
                "patient_corrected",
            }, (case["id"], obs)
    assert REQUIRED_CATEGORIES <= set(by_category), by_category
    for category in REQUIRED_CATEGORIES:
        assert by_category[category] >= 2, (category, by_category)


def test_corpus_grades_consistent_with_ranges() -> None:
    """Every corpus label must be explainable by the table + grading rules.

    Confirmed observations on a tabled item may only be 无法判断 when the
    value falls on a side that has a reference bound but no yellow/red
    band (partial thresholds, fail-closed). Confirmed observations on a
    non-tabled item must be 无法判断. Unconfirmed observations must be
    无法判断. Everything else must be a concrete 绿/黄/红 label.
    """
    ranges = _load_json(RANGES_PATH)["items"]
    fixture = _load_json(FIXTURE_PATH)
    for case in fixture["cases"]:
        for obs in case["observations"]:
            key = obs["item_key"]
            expected = obs["expected_grade"]
            if obs["verification_status"] not in CONFIRMED:
                assert expected == "无法判断", (case["id"], obs)
                continue
            if key not in ranges:
                assert key in NO_RANGE_KEYS, (case["id"], obs)
                assert expected == "无法判断", (case["id"], obs)
                continue
            entry = ranges[key]
            if expected == "无法判断":
                # Only the partial-threshold direction may fail closed.
                below = (
                    entry["ref_low"] is not None
                    and obs["value"] < entry["ref_low"]
                    and entry["yellow_low"] is None
                )
                above = (
                    entry["ref_high"] is not None
                    and obs["value"] > entry["ref_high"]
                    and entry["yellow_high"] is None
                )
                assert below or above, (case["id"], obs)
            else:
                assert expected in {"绿", "黄", "红"}, (case["id"], obs)


def _extract_grades(result):
    """Return (grades by item_key, grades of unresolvable items).

    P3-A returns {"items": [{item_key, grade, ...}]}; confirmed
    observations on items absent from the table carry item_key None.
    """
    assert isinstance(result, dict) and isinstance(result.get("items"), list), result
    grades: dict[str, str] = {}
    none_grades: list[str] = []
    for entry in result["items"]:
        assert "grade" in entry, entry
        assert entry["grade"] in GRADES, entry
        if entry.get("item_key") is None:
            none_grades.append(entry["grade"])
        else:
            grades[entry["item_key"]] = entry["grade"]
    return grades, none_grades


_CASES = _load_json(FIXTURE_PATH)["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_grade_case_against_p3a_module(case) -> None:
    """Grade each corpus case with P3-A's real build_interpretation.

    The import happens inside the test (never at collection time), so a
    missing P3-A module skips these tests without breaking collection of
    the schema tests above, while a broken P3-A import chain errors
    loudly here instead of being masked.
    """
    module = pytest.importorskip(
        "medagent.report_interpretation",
        reason="P3-A report_interpretation module not landed yet",
    )
    ranges = _load_json(RANGES_PATH)
    names = {k: v["name"] for k, v in ranges["items"].items()}
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
    result = module.build_interpretation(
        f"corpus-{case['id']}", observations, ranges
    )
    grades, none_grades = _extract_grades(result)
    excluded_refs = {
        entry.get("observation_ref") for entry in result.get("excluded", [])
    }
    for index, obs in enumerate(case["observations"]):
        expected = obs["expected_grade"]
        if obs["verification_status"] not in CONFIRMED:
            # Unconfirmed observations land in result["excluded"], not items.
            assert obs["item_key"] not in grades, (case["id"], obs, grades)
            assert f"{case['id']}-{index}" in excluded_refs, (
                case["id"],
                obs,
                excluded_refs,
            )
        elif obs["item_key"] not in ranges["items"]:
            # No-range items grade 无法判断 with item_key None.
            assert expected == "无法判断" and "无法判断" in none_grades, (
                case["id"],
                obs,
                grades,
                none_grades,
            )
        else:
            assert grades.get(obs["item_key"]) == expected, (
                case["id"],
                obs,
                grades,
            )
