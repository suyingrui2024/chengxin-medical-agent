# P2b 测量泛化注册表测试（medagent.measurement_kinds，纯函数模块）。
#
# 接线说明（完整版见 ~/workspace/scratch/p2b-wiring-note.md，协调者接线时必读）：
# 1. add_measurement 泛化（patient_records.py，协调者文件）：把
#    "kind != blood_glucose 就硬拒" 换成 kind_spec(kind) 查表 + normalize_measurement
#    校验；仍按原模式存“患者原值 + 原单位”，趋势读时再归一（不要把归一值覆盖原值）。
#    注意：observations.numeric_value 只有单值列，血压双值建议存 value_text="120/80"
#    且 numeric_value 置空、趋势从 value_text 经注册表解析（或拆成收缩/舒张两条观测，
#    由协调者定夺，见接线说明 §2）。
# 2. 转录确认流程调用 saveable_from_observation 的位置：患者在档案页确认/更正一条
#    document_number 观测之后（revise_observation reason=confirmation/correction、
#    answer_clarification 完成后），前端仅当返回 saveable=True 时才展示“另存为测量”
#    入口；unit_inferred=True 时必须把推断单位明示给患者确认；保存动作走新的服务端
#    入口（协调者新建），内部复用泛化后的 add_measurement，绝不自动保存。
# 3. 趋势泛化：glucose_trend 泛化为按 kind 的 trend 查询时直接复用 trend_summary；
#    趋势只给描述统计，不给高低判断（与既有 glucose_trend 口径一致）。
# 4. 本模块不碰数据库与服务器；范围超限只警告不拒绝（clarity="out_of_range"）。

from decimal import Decimal

import pytest

from medagent.extensions.glucose import convert_glucose
from medagent.measurement_kinds import (
    KNOWN_KINDS,
    MeasurementKindError,
    canonical_unit,
    kind_spec,
    normalize_measurement,
    saveable_from_observation,
    trend_summary,
)


# ---------------------------------------------------------------------------
# 注册表本身
# ---------------------------------------------------------------------------

def test_known_kinds_cover_the_five_p2b_kinds():
    assert set(KNOWN_KINDS) == {
        "blood_glucose",
        "blood_pressure",
        "temperature",
        "weight",
        "blood_oxygen",
    }


def test_kind_spec_and_unknown_kind():
    spec = kind_spec("temperature")
    assert spec["canonical_unit"] == "°C"
    assert "°F" in spec["units"] or "℉" in spec["units"]
    assert canonical_unit("weight") == "kg"
    with pytest.raises(MeasurementKindError):
        kind_spec("heart_rate")


def test_unknown_unit_and_bad_value_are_rejected():
    with pytest.raises(MeasurementKindError):
        normalize_measurement("temperature", "37", "kg")
    with pytest.raises(MeasurementKindError):
        normalize_measurement("weight", "abc", "kg")
    with pytest.raises(MeasurementKindError):
        normalize_measurement("weight", "-5", "kg")
    with pytest.raises(MeasurementKindError):
        normalize_measurement("blood_pressure", "120", "mmHg")


# ---------------------------------------------------------------------------
# blood_glucose：沿用既有语义（系数 18），并与 convert_glucose 交叉校验
# ---------------------------------------------------------------------------

def test_glucose_normal_value_and_units():
    result = normalize_measurement("blood_glucose", "6.1", "mmol/L")
    assert result["values"]["value"] == Decimal("6.1")
    assert result["clarity"] == "clear"
    assert result["warnings"] == []


def test_glucose_mg_dl_conversion_matches_existing_converter():
    result = normalize_measurement("blood_glucose", "180", "mg/dL")
    receipt = convert_glucose(
        {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"}
    )
    assert result["values"]["value"] == Decimal(receipt["value"])
    assert result["canonical_unit"] == "mmol/L"
    # 反向：mmol/L -> mg/dL 同样是系数 18。
    back = normalize_measurement("blood_glucose", "10", "mmol/L")
    assert back["values"]["value"] == Decimal("10")
    assert normalize_measurement("blood_glucose", "10", "mmol/L")["values"][
        "value"
    ] * Decimal("18") == Decimal("180")


def test_glucose_out_of_range_warns_but_accepts():
    result = normalize_measurement("blood_glucose", "45", "mmol/L")
    assert result["values"]["value"] == Decimal("45")
    assert result["clarity"] == "out_of_range"
    assert len(result["warnings"]) == 1


def test_glucose_trend_and_empty_sample():
    samples = [
        normalize_measurement("blood_glucose", "5.5", "mmol/L"),
        normalize_measurement("blood_glucose", "180", "mg/dL"),
        normalize_measurement("blood_glucose", "6.0", "mmol/L"),
    ]
    summary = trend_summary("blood_glucose", samples)
    assert summary["count"] == 3
    assert summary["canonical_unit"] == "mmol/L"
    series = summary["series"]["value"]
    assert series["minimum"] == "5.5"
    assert series["maximum"] == "10"
    assert series["latest"] == "6"
    # mean = (5.5 + 10 + 6) / 3 = 7.1666... -> 4 位小数
    assert series["mean"] == "7.1667"
    assert trend_summary("blood_glucose", []) is None
    assert trend_summary("blood_glucose", None) is None


def test_glucose_trend_accepts_raw_samples():
    summary = trend_summary(
        "blood_glucose",
        [
            {"value": "90", "unit": "mg/dL"},
            {"value": "6", "unit": "mmol/L"},
        ],
    )
    assert summary["series"]["value"]["minimum"] == "5"
    assert summary["series"]["value"]["maximum"] == "6"


# ---------------------------------------------------------------------------
# blood_pressure：双值
# ---------------------------------------------------------------------------

def test_blood_pressure_normal_pair():
    result = normalize_measurement("blood_pressure", "120/80", "mmHg")
    assert result["values"] == {"systolic": Decimal("120"), "diastolic": Decimal("80")}
    assert result["clarity"] == "clear"
    assert result["warnings"] == []
    # dict / tuple 输入等价。
    assert normalize_measurement(
        "blood_pressure", {"systolic": 120, "diastolic": 80}, "mmHg"
    )["values"] == result["values"]
    assert normalize_measurement("blood_pressure", (120, 80), None)["values"] == result["values"]


def test_blood_pressure_out_of_range_and_reversed_warn_but_accept():
    high = normalize_measurement("blood_pressure", "280/90", "mmHg")
    assert high["clarity"] == "out_of_range"
    assert any("收缩压" in warning for warning in high["warnings"])
    reversed_pair = normalize_measurement("blood_pressure", "80/120", "mmHg")
    assert any("写反" in warning for warning in reversed_pair["warnings"])


def test_blood_pressure_trend_has_two_series():
    summary = trend_summary(
        "blood_pressure",
        [
            {"systolic": 120, "diastolic": 80, "unit": "mmHg"},
            {"systolic": 130, "diastolic": 85, "unit": "mmHg"},
        ],
    )
    assert summary["series"]["systolic"] == {
        "minimum": "120",
        "maximum": "130",
        "mean": "125",
        "latest": "130",
        "count": 2,
    }
    assert summary["series"]["diastolic"]["latest"] == "85"
    assert trend_summary("blood_pressure", []) is None


# ---------------------------------------------------------------------------
# temperature：C / F 归一到 °C
# ---------------------------------------------------------------------------

def test_temperature_normal_and_fahrenheit_conversion():
    assert normalize_measurement("temperature", "36.5", "°C")["values"]["value"] == Decimal(
        "36.5"
    )
    result = normalize_measurement("temperature", "98.6", "°F")
    assert result["canonical_unit"] == "°C"
    assert result["values"]["value"] == Decimal("37")
    assert result["clarity"] == "clear"


def test_temperature_out_of_range_warns_but_accepts():
    result = normalize_measurement("temperature", "34", "°C")
    assert result["clarity"] == "out_of_range"
    assert len(result["warnings"]) == 1


def test_temperature_trend_and_empty():
    summary = trend_summary(
        "temperature",
        [
            {"value": "36.5", "unit": "°C"},
            {"value": "98.6", "unit": "°F"},
            {"value": "37.2", "unit": "°C"},
        ],
    )
    series = summary["series"]["value"]
    assert series["minimum"] == "36.5"
    assert series["maximum"] == "37.2"
    assert series["latest"] == "37.2"
    assert trend_summary("temperature", []) is None


# ---------------------------------------------------------------------------
# weight：kg / lb / 斤 归一到 kg
# ---------------------------------------------------------------------------

def test_weight_normal_and_conversions():
    assert normalize_measurement("weight", "70", "kg")["values"]["value"] == Decimal("70")
    lb = normalize_measurement("weight", "154.323583529", "lb")
    assert lb["values"]["value"].quantize(Decimal("0.0001")) == Decimal("70.0000")
    assert normalize_measurement("weight", "140", "斤")["values"]["value"] == Decimal("70")


def test_weight_out_of_range_warns_but_accepts():
    result = normalize_measurement("weight", "400", "kg")
    assert result["clarity"] == "out_of_range"
    assert result["warnings"]


def test_weight_trend_and_empty():
    summary = trend_summary(
        "weight",
        [
            {"value": "70", "unit": "kg"},
            {"value": "140", "unit": "斤"},
            {"value": "71", "unit": "kg"},
        ],
    )
    series = summary["series"]["value"]
    assert series["minimum"] == "70"
    assert series["maximum"] == "71"
    assert series["mean"] == "70.3333"
    assert series["latest"] == "71"
    assert trend_summary("weight", []) is None


# ---------------------------------------------------------------------------
# blood_oxygen：%
# ---------------------------------------------------------------------------

def test_blood_oxygen_normal_value():
    result = normalize_measurement("blood_oxygen", "98", "%")
    assert result["values"]["value"] == Decimal("98")
    assert result["clarity"] == "clear"


def test_blood_oxygen_out_of_range_warns_but_accepts():
    result = normalize_measurement("blood_oxygen", "65", "%")
    assert result["clarity"] == "out_of_range"
    assert result["warnings"]


def test_blood_oxygen_trend_and_empty():
    summary = trend_summary(
        "blood_oxygen",
        [
            {"value": "98", "unit": "%"},
            {"value": "97", "unit": "%"},
            {"value": "99", "unit": "%"},
        ],
    )
    series = summary["series"]["value"]
    assert series["minimum"] == "97"
    assert series["maximum"] == "99"
    assert series["mean"] == "98"
    assert series["latest"] == "99"
    assert trend_summary("blood_oxygen", []) is None


# ---------------------------------------------------------------------------
# saveable_from_observation：转录观测 -> 测量候选
# ---------------------------------------------------------------------------

def _observation(**overrides):
    base = {
        "kind": "document_number",
        "display_name": "血压",
        "value_text": "120/80",
        "context": "血压 120/80 mmHg",
        "clarity": "clear",
        "verification_status": "patient_confirmed",
    }
    base.update(overrides)
    return base


def test_saveable_blood_pressure_from_confirmed_transcription():
    result = saveable_from_observation(_observation())
    assert result["saveable"] is True
    assert result["kind"] == "blood_pressure"
    assert result["values"]["values"] == {
        "systolic": Decimal("120"),
        "diastolic": Decimal("80"),
    }
    assert result["extraction_rule"].startswith("bp_slash_pair")


def test_saveable_temperature_with_explicit_unit():
    result = saveable_from_observation(
        _observation(
            display_name="体温",
            value_text="37.2",
            context="体温 37.2℃",
        )
    )
    assert result["saveable"] is True
    assert result["kind"] == "temperature"
    assert result["values"]["values"]["value"] == Decimal("37.2")
    assert result["unit_inferred"] is False


def test_saveable_temperature_unit_inferred_but_flagged():
    result = saveable_from_observation(
        _observation(display_name="体温", value_text="37.2", context="体温 37.2")
    )
    assert result["saveable"] is True
    assert result["kind"] == "temperature"
    assert result["unit"] == "°C"
    assert result["unit_inferred"] is True


def test_saveable_glucose_never_infers_unit():
    result = saveable_from_observation(
        _observation(display_name="血糖", value_text="6.1", context="血糖 6.1")
    )
    assert result["saveable"] is False
    assert "单位" in result["reason"]
    confirmed = saveable_from_observation(
        _observation(display_name="血糖", value_text="6.1", context="血糖 6.1 mmol/L")
    )
    assert confirmed["saveable"] is True
    assert confirmed["kind"] == "blood_glucose"


def test_saveable_requires_patient_confirmation():
    for status in ("model_extracted", "needs_confirmation"):
        result = saveable_from_observation(_observation(verification_status=status))
        assert result["saveable"] is False
        assert result["kind"] is None
    corrected = saveable_from_observation(
        _observation(verification_status="patient_corrected")
    )
    assert corrected["saveable"] is True


def test_saveable_rejects_non_number_observations_and_ambiguity():
    content = saveable_from_observation(
        _observation(kind="document_content", value_text="血压正常")
    )
    assert content["saveable"] is False
    ambiguous = saveable_from_observation(
        _observation(
            display_name="体温体重",
            value_text="37",
            context="体温 37 体重 70kg",
        )
    )
    assert ambiguous["saveable"] is False
    assert "多个" in ambiguous["reason"]
    no_pair = saveable_from_observation(
        _observation(value_text="120", context="血压 120")
    )
    assert no_pair["saveable"] is False
    assert "双值" in no_pair["reason"]
    assert saveable_from_observation("not-a-mapping")["saveable"] is False
