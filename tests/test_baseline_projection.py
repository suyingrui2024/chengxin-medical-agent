"""Tests for the P2c baseline projection (pure functions, no wiring)."""

from datetime import datetime, timedelta, timezone

from medagent.baseline_projection import (
    BASELINE_REFERENCE_DISABLED_EVENT,
    BASELINE_REFERENCE_ENABLED_FIELD,
    baseline_reference_enabled,
    build_allergy_hint,
    build_baseline_projection,
)

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


def _measurement(kind, value, days_ago, status="patient_confirmed", unit="mmol/L"):
    return {
        "kind": kind,
        "value": value,
        "unit": unit,
        "measured_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "verification_status": status,
    }


def _full_profile():
    return {
        "display_name": "张三",
        "birth_year": 1980,
        "birth_year_month": "1980-05",
        "sex": "女",
        "chronic_conditions": ["高血压", "糖尿病"],
        "allergies": [{"name": "青霉素", "kind": "药物"}],
        "medications": [{"name": "氨氯地平", "purpose": "降压"}],
        "surgical_history": ["2019 年阑尾切除"],
    }


def test_whitelist_drops_everything_else():
    profile = _full_profile()
    profile.update(
        {
            "height_cm": 170,
            "weight_kg": 65,
            "id_number": "110101198005010001",
            "phone": "13800000000",
            "address": "某市某区某路 1 号",
            "notes": "自由文本备注",
            "emergency_contact": "李四",
        }
    )
    # Allergies/medications must keep only name + kind/purpose.
    profile["allergies"] = [{"name": "青霉素", "kind": "药物", "severity": "重度", "note": "休克史"}]
    profile["medications"] = [{"name": "氨氯地平", "purpose": "降压", "dose": "5mg qd"}]
    out = build_baseline_projection(profile, [], now=NOW)
    assert set(out) == {
        "display_name",
        "birth_year",
        "birth_year_month",
        "sex",
        "chronic_conditions",
        "allergies",
        "medications",
        "surgical_history",
        "truncated",
    }
    assert out["allergies"] == [{"name": "青霉素", "kind": "药物"}]
    assert out["medications"] == [{"name": "氨氯地平", "purpose": "降压"}]
    assert out["truncated"] is False


def test_empty_profile_returns_empty_dict():
    assert build_baseline_projection({}, [], now=NOW) == {}
    assert build_baseline_projection(None, None, now=NOW) == {}
    assert build_baseline_projection({"height_cm": 170}, [], now=NOW) == {}


def test_list_cap_and_string_truncation_flagged():
    long_text = "慢" * 100
    profile = {
        "display_name": "x" * 90,
        "chronic_conditions": [f"疾病{i}" for i in range(15)],
        "allergies": [{"name": long_text, "kind": "药物"}],
    }
    out = build_baseline_projection(profile, [], now=NOW)
    assert len(out["chronic_conditions"]) == 10
    assert len(out["display_name"]) == 81  # 80 chars + ellipsis marker
    assert out["display_name"].endswith("…")
    assert out["truncated"] is True
    assert out["allergies"][0]["truncated"] is True
    assert len(out["allergies"][0]["name"]) == 81


def test_recent_window_filters_old_measurements():
    measurements = [
        _measurement("blood_glucose", 6.1, days_ago=1),
        _measurement("blood_glucose", 7.0, days_ago=8),  # outside 7 days
        _measurement("blood_glucose", 5.9, days_ago=30),
    ]
    out = build_baseline_projection({}, measurements, now=NOW)
    stats = out["recent_measurements"]["blood_glucose"]
    assert stats["count"] == 1
    assert stats["latest"] == 6.1
    assert stats["min"] == stats["max"] == stats["mean"] == 6.1


def test_unconfirmed_measurements_excluded():
    measurements = [
        _measurement("blood_glucose", 6.1, days_ago=1, status="model_extracted"),
        _measurement("blood_glucose", 6.5, days_ago=1, status="needs_confirmation"),
        _measurement("blood_glucose", 6.2, days_ago=2, status="patient_corrected"),
    ]
    out = build_baseline_projection({}, measurements, now=NOW)
    stats = out["recent_measurements"]["blood_glucose"]
    assert stats["count"] == 1
    assert stats["latest"] == 6.2


def test_measurement_stats_are_descriptive_only():
    measurements = [
        _measurement("blood_pressure_systolic", 120, days_ago=3, unit="mmHg"),
        _measurement("blood_pressure_systolic", 130, days_ago=1, unit="mmHg"),
        _measurement("blood_pressure_systolic", 125, days_ago=2, unit="mmHg"),
    ]
    out = build_baseline_projection({}, measurements, now=NOW)
    stats = out["recent_measurements"]["blood_pressure_systolic"]
    assert stats["min"] == 120
    assert stats["max"] == 130
    assert stats["mean"] == 125.0
    assert stats["latest"] == 130
    assert stats["unit"] == "mmHg"
    assert stats["count"] == 3
    # No judgement wording anywhere in the projection.
    rendered = str(out)
    for word in ("正常", "偏高", "偏低", "异常", "危险", "建议"):
        assert word not in rendered


def test_allergy_hint_positive():
    allergies = [{"name": "青霉素", "kind": "药物"}]
    assert (
        build_allergy_hint(allergies, "避免自行服用可能引起过敏的药物")
        == "你档案里记有青霉素过敏，涉及用药请先核对"
    )
    # List-form avoid text also works.
    assert (
        build_allergy_hint(allergies, ["多喝水", "避免使用抗过敏药物前先核对"])
        == "你档案里记有青霉素过敏，涉及用药请先核对"
    )


def test_allergy_hint_negative_cases():
    allergies = [{"name": "青霉素", "kind": "药物"}]
    # No allergy word.
    assert build_allergy_hint(allergies, "避免自行服用感冒药") is None
    # No medication word.
    assert build_allergy_hint(allergies, "注意过敏原，避免接触花粉") is None
    # No avoid text at all.
    assert build_allergy_hint(allergies, None) is None
    assert build_allergy_hint(allergies, "") is None
    # No allergies on file.
    assert build_allergy_hint([], "避免自行服用可能引起过敏的药物") is None
    assert build_allergy_hint(None, "避免自行服用可能引起过敏的药物") is None
    # Hint never names a drug: it only echoes the recorded allergy name.
    hint = build_allergy_hint(allergies, "避免自行服用可能引起过敏的药物")
    assert hint is not None and "氨氯地平" not in hint


def test_session_switch_convention_defaults_on():
    assert BASELINE_REFERENCE_ENABLED_FIELD == "baseline_reference_enabled"
    assert BASELINE_REFERENCE_DISABLED_EVENT == "baseline.reference_disabled"
    assert baseline_reference_enabled(None) is True
    assert baseline_reference_enabled({}) is True
    assert baseline_reference_enabled({"baseline_reference_enabled": True}) is True
    assert baseline_reference_enabled({"baseline_reference_enabled": False}) is False
