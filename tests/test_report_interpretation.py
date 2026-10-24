"""Tests for the P3a deterministic report interpretation module."""

from medagent.report_interpretation import (
    DISCLAIMER,
    build_interpretation,
    grade_observation_value,
    interpretation_summary,
    lint_explanation,
    load_ranges,
)

RANGES = {
    "version": "1.0",
    "items": {
        # Contiguous bands: low red <= 3.0, yellow [3.0, 3.9),
        # green [3.9, 6.1], yellow (6.1, 7.0], red >= 7.0.
        "fasting_glucose": {
            "name": "空腹血糖",
            "unit": "mmol/L",
            "ref_low": 3.9,
            "ref_high": 6.1,
            "yellow_low": 3.0,
            "yellow_high": 7.0,
            "red_low": 3.0,
            "red_high": 7.0,
        },
        "wbc": {
            "name": "白细胞计数",
            "unit": "10^9/L",
            "ref_low": 4.0,
            "ref_high": 10.0,
            "yellow_low": 3.0,
            "yellow_high": 12.0,
            "red_low": 2.0,
            "red_high": 15.0,
        },
        # No thresholds at all: must always grade 无法判断.
        "mystery_item": {"name": "未定项", "unit": "U/L"},
    },
}


def _obs(observation_id, value, unit="mmol/L", status="patient_confirmed",
         item_key="fasting_glucose", display_name="空腹血糖"):
    return {
        "observation_id": observation_id,
        "current_revision_id": f"{observation_id}-r1",
        "verification_status": status,
        "display_name": display_name,
        "value_text": value,
        "unit": unit,
        "item_key": item_key,
    }


def test_normal_value_is_green_with_observe_action():
    sheet = build_interpretation("e1", [_obs("o1", "5.0")], RANGES)
    assert sheet["contract"] == "report-interpretation/v1"
    assert sheet["disclaimer"] == DISCLAIMER
    (item,) = sheet["items"]
    assert item["grade"] == "绿"
    assert item["action"] == "观察"
    assert item["result_text"] == "5.0mmol/L"
    assert item["name"] == "空腹血糖"
    assert sheet["summary"]["green"] == 1
    assert sheet["excluded"] == []


def test_single_abnormal_yellow_and_red():
    yellow = build_interpretation("e1", [_obs("o1", "6.5")], RANGES)
    assert yellow["items"][0]["grade"] == "黄"
    assert yellow["items"][0]["action"] == "复查"
    red = build_interpretation("e1", [_obs("o1", "8.0")], RANGES)
    assert red["items"][0]["grade"] == "红"
    assert red["items"][0]["action"] == "尽快就诊"
    low_red = build_interpretation("e1", [_obs("o1", "2.5")], RANGES)
    assert low_red["items"][0]["grade"] == "红"


def test_multiple_abnormal_items_counted():
    sheet = build_interpretation(
        "e1",
        [
            _obs("o1", "8.0"),
            _obs("o2", "11.0", unit="10^9/L", item_key="wbc",
                 display_name="白细胞计数"),
            _obs("o3", "5.0"),
        ],
        RANGES,
    )
    grades = [item["grade"] for item in sheet["items"]]
    assert grades == ["红", "黄", "绿"]
    assert sheet["summary"] == {
        "total_confirmed": 3,
        "green": 1,
        "yellow": 1,
        "red": 1,
        "unknown": 0,
        "excluded": 0,
        "text": "其中有红档项目，建议尽快就诊，并把这份报告带给医生看。",
    }


def test_unconfirmed_observations_excluded_and_not_in_summary():
    sheet = build_interpretation(
        "e1",
        [
            _obs("o1", "5.0"),
            _obs("o2", "99.0", status="model_extracted"),
            _obs("o3", "99.0", status="needs_confirmation"),
        ],
        RANGES,
    )
    assert [item["observation_ref"] for item in sheet["items"]] == ["o1"]
    assert len(sheet["excluded"]) == 2
    assert all(e["reason"] == "未确认观测不进入解读" for e in sheet["excluded"])
    summary = interpretation_summary(sheet)
    assert "99.0" not in summary
    assert "5.0mmol/L" in summary


def test_no_range_always_unknown_fail_closed():
    sheet = build_interpretation(
        "e1", [_obs("o1", "123.0", unit="U/L", item_key="mystery_item",
                      display_name="未定项")], RANGES)
    (item,) = sheet["items"]
    assert item["grade"] == "无法判断"
    assert item["action"] == "观察"
    assert "无可用参考区间" in item["reason"]


def test_unknown_item_key_is_unknown_not_graded():
    sheet = build_interpretation(
        "e1", [_obs("o1", "5.0", item_key="not_in_ranges")], RANGES)
    assert sheet["items"][0]["grade"] == "无法判断"
    assert sheet["items"][0]["reason"] == "无对应参考区间"


def test_missing_threshold_on_value_side_is_unknown():
    # Urea low side has a ref bound (3.2) but no yellow_low: a low value
    # cannot be told apart as yellow vs red, so it fails closed.
    urea = {
        "name": "尿素", "unit": "mmol/L",
        "ref_low": 3.2, "ref_high": 7.1,
        "yellow_high": 12.0, "red_high": 20.0,
    }
    grade, reason, _ = grade_observation_value("2.0", "mmol/L", urea)
    assert grade == "无法判断"
    assert "缺少分级阈值" in reason
    # The high side of the same item is fully banded: beyond yellow -> red.
    grade, _, _ = grade_observation_value("15.0", "mmol/L", urea)
    assert grade == "红"


def test_beyond_yellow_band_is_red_not_unknown():
    # Creatinine 200 sits between yellow_high (150) and red_high (300):
    # beyond the yellow band means red, even though it has not reached
    # the nominal red edge.
    creatinine = {
        "name": "肌酐", "unit": "μmol/L",
        "ref_high": 104, "yellow_high": 150, "red_high": 300,
    }
    grade, _, _ = grade_observation_value("200", "μmol/L", creatinine)
    assert grade == "红"
    # One-sided: no lower ref bound, so a low creatinine is green.
    grade, _, _ = grade_observation_value("30", "μmol/L", creatinine)
    assert grade == "绿"


def test_corpus_style_call_with_numeric_values():
    # The grading corpus calls build_interpretation(observations) with
    # numeric "value" keys and no entry id / display names.
    sheet = build_interpretation([
        {"item_key": "fasting_glucose", "value": 5.2, "unit": "mmol/L",
         "verification_status": "patient_confirmed"},
        {"item_key": "fasting_glucose", "value": 20.0, "unit": "mmol/L",
         "verification_status": "model_extracted"},
    ], ranges=RANGES)
    grades = {item["item_key"]: item["grade"] for item in sheet["items"]}
    assert grades == {"fasting_glucose": "绿"}
    assert len(sheet["excluded"]) == 1


def test_unit_conversion_glucose_mg_dl_factor_18():
    # 108 mg/dL / 18 = 6.0 mmol/L -> green; 144 mg/dL / 18 = 8.0 -> red.
    green = build_interpretation(
        "e1", [_obs("o1", "108", unit="mg/dL")], RANGES)
    assert green["items"][0]["grade"] == "绿"
    red = build_interpretation(
        "e1", [_obs("o1", "144", unit="mg/dL")], RANGES)
    assert red["items"][0]["grade"] == "红"


def test_unit_conversion_temperature_fahrenheit():
    ranges = {
        "version": "1.0",
        "items": {
            "temperature": {
                "name": "体温", "unit": "°C",
                "ref_low": 36.0, "ref_high": 37.3,
                "yellow_low": 35.0, "yellow_high": 38.5,
                "red_low": 35.0, "red_high": 38.5,
            }
        },
    }
    # 98.6 F = 37.0 C -> green; 103 F = 39.4 C -> red.
    sheet = build_interpretation(
        "e1",
        [_obs("o1", "98.6", unit="°F", item_key="temperature",
              display_name="体温")],
        ranges,
    )
    assert sheet["items"][0]["grade"] == "绿"
    sheet = build_interpretation(
        "e1",
        [_obs("o1", "103.0", unit="°F", item_key="temperature",
              display_name="体温")],
        ranges,
    )
    assert sheet["items"][0]["grade"] == "红"


def test_unparseable_value_is_unknown():
    for bad in ("120/80", "5-6", "<5.0", "阴性", "", None):
        sheet = build_interpretation(
            "e1", [_obs("o1", bad)], RANGES)
        assert sheet["items"][0]["grade"] == "无法判断", bad
        assert "无法解析" in sheet["items"][0]["reason"], bad


def test_missing_or_mismatched_unit_is_unknown():
    sheet = build_interpretation(
        "e1", [_obs("o1", "5.0", unit="")], RANGES)
    assert sheet["items"][0]["grade"] == "无法判断"
    sheet = build_interpretation(
        "e1", [_obs("o1", "5.0", unit="kg")], RANGES)
    assert sheet["items"][0]["grade"] == "无法判断"


def test_display_name_fallback_resolves_item():
    obs = _obs("o1", "5.0", item_key=None)
    sheet = build_interpretation("e1", [obs], RANGES)
    assert sheet["items"][0]["item_key"] == "fasting_glucose"
    assert sheet["items"][0]["grade"] == "绿"


def test_banned_phrase_draft_is_emptied_not_sheet_voided():
    sheet = build_interpretation(
        "e1", [_obs("o1", "8.0")], RANGES,
        explanations={"fasting_glucose": "这个结果确诊了糖尿病"},
    )
    (item,) = sheet["items"]
    assert item["lint_failed"] is True
    assert item["explanation"] == ""
    assert item["grade"] == "红"  # grade unaffected by prose lint
    assert len(sheet["items"]) == 1


def test_dose_draft_is_emptied():
    sheet = build_interpretation(
        "e1", [_obs("o1", "5.0")], RANGES,
        explanations={"fasting_glucose": "每天服用 2 片降糖药"},
    )
    (item,) = sheet["items"]
    assert item["lint_failed"] is True
    assert item["explanation"] == ""
    clean, failed, _ = lint_explanation("数值在区间内，建议复查时带给医生看。")
    assert clean and failed is False


def test_clean_draft_passes_through():
    sheet = build_interpretation(
        "e1", [_obs("o1", "5.0")], RANGES,
        explanations={"fasting_glucose": "数值在区间内，复查时可带给医生看。"},
    )
    assert sheet["items"][0]["explanation"] == "数值在区间内，复查时可带给医生看。"
    assert sheet["items"][0]["lint_failed"] is False


def test_template_explanations_carry_no_banned_or_dose_wording():
    sheet = build_interpretation(
        "e1",
        [_obs("o1", "5.0"), _obs("o2", "6.5"), _obs("o3", "8.0"),
         _obs("o4", "abc")],
        RANGES,
    )
    for item in sheet["items"]:
        clean, failed, _ = lint_explanation(item["explanation"])
        assert failed is False, item
        assert clean == item["explanation"]


def test_emergency_action_only_with_optional_thresholds():
    ranges = {
        "version": "1.0",
        "items": {
            "fasting_glucose": {
                **RANGES["items"]["fasting_glucose"],
                "emergency_high": 15.0,
            }
        },
    }
    sheet = build_interpretation("e1", [_obs("o1", "16.0")], ranges)
    assert sheet["items"][0]["grade"] == "红"
    assert sheet["items"][0]["action"] == "急诊"
    sheet = build_interpretation("e1", [_obs("o1", "8.0")], ranges)
    assert sheet["items"][0]["action"] == "尽快就诊"


def test_summary_contains_only_confirmed_values_and_grades():
    sheet = build_interpretation(
        "e1",
        [_obs("o1", "5.0"), _obs("o2", "8.0"),
         _obs("o3", "99.0", status="model_extracted")],
        RANGES,
    )
    summary = interpretation_summary(sheet)
    assert "空腹血糖：5.0mmol/L（绿）" in summary
    assert "空腹血糖：8.0mmol/L（红）" in summary
    assert "99.0" not in summary
    assert "绿 1 项、黄 0 项、红 1 项、无法判断 0 项" in summary


def test_load_ranges_missing_file_is_empty_not_error(tmp_path):
    loaded = load_ranges(tmp_path / "nope.json")
    assert loaded == {"version": None, "items": {}}
    sheet = build_interpretation("e1", [_obs("o1", "5.0")], loaded)
    assert sheet["items"][0]["grade"] == "无法判断"
