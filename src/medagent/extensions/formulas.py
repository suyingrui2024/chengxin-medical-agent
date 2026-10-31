"""Trusted formula implementations. Content may name them, never define them.

Each entry declares the exact input/output signature a pack must
reproduce, the arithmetic itself, and the audit cases used by tests.
Constants are written as ``Decimal`` literals so results are identical on
every platform; ``audit_cases`` are string-equality fixtures, not
``assertAlmostEqual``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

DISCLAIMER = (
    "本结果由公式程序按标注来源计算，仅作记录与参考，"
    "不构成诊断，也不替代医生判断。"
)


@dataclass(frozen=True)
class FormulaSpec:
    key: str
    citation: str
    inputs: List[Dict[str, Any]]
    output: Dict[str, Any]
    method: str
    limitations: Sequence[str]
    fn: Callable[[Mapping[str, Any]], Decimal]
    render: Callable[[Mapping[str, Any], str], str]
    audit_cases: Sequence[Tuple[Dict[str, str], str]]
    disclaimer: str = DISCLAIMER


def _number(identity, label, unit, low, high, step="0.1"):
    return {
        "id": identity,
        "label": label,
        "type": "number",
        "unit": unit,
        "min": low,
        "max": high,
        "step": step,
    }


def _output(label, unit, low, high, places, direction):
    return {
        "label": label,
        "unit": unit,
        "min": low,
        "max": high,
        "precision": {"places": places},
        "direction": direction,
    }


def _bmi(values):
    height = values["height_cm"] / Decimal(100)
    return values["weight_kg"] / (height * height)


def _bsa(values):
    return (
        Decimal("0.0061") * values["height_cm"]
        + Decimal("0.0128") * values["weight_kg"]
        - Decimal("0.1529")
    )


def _map(values):
    return (values["sbp"] + Decimal(2) * values["dbp"]) / Decimal(3)


def _corrected_calcium(values):
    return values["calcium_mmol_l"] + Decimal("0.02") * (
        Decimal(40) - values["albumin_g_l"]
    )


def _anion_gap(values):
    return values["sodium"] - values["chloride"] - values["bicarbonate"]


def _osmolality(values):
    return (
        Decimal(2) * values["sodium"]
        + values["glucose_mmol_l"]
        + values["urea_mmol_l"]
    )


def _shock_index(values):
    return values["heart_rate"] / values["sbp"]


def _child_weight(values):
    return values["age_years"] * Decimal(2) + Decimal(8)


FORMULAS: Dict[str, FormulaSpec] = {
    "bmi": FormulaSpec(
        key="bmi",
        citation="WHO-BMI-2004",
        inputs=[
            _number("height_cm", "身高", "cm", "50", "250"),
            _number("weight_kg", "体重", "kg", "2", "400"),
        ],
        output=_output("体质指数", "kg/m²", "5", "200", 1, "higher_is_worse"),
        method="体重（kg）除以身高（m）的平方",
        limitations=[
            "仅适用于成人；孕妇、运动员、儿童与老年人不适用。",
            "不区分肌肉与脂肪，不构成肥胖诊断。",
        ],
        fn=_bmi,
        render=lambda v, shown: (
            f"{v['weight_kg']} ÷ ({v['height_cm']} ÷ 100)² = {shown} kg/m²"
        ),
        audit_cases=[
            ({"height_cm": "170", "weight_kg": "65"}, "22.5"),
            ({"height_cm": "150", "weight_kg": "45"}, "20.0"),
            ({"height_cm": "180", "weight_kg": "120"}, "37.0"),
        ],
    ),
    "bsa": FormulaSpec(
        key="bsa",
        citation="CN-BSA-1999",
        inputs=[
            _number("height_cm", "身高", "cm", "50", "250"),
            _number("weight_kg", "体重", "kg", "2", "400"),
        ],
        output=_output("体表面积", "m²", "0.1", "4", 2, "no_direction"),
        method="0.0061 × 身高(cm) + 0.0128 × 体重(kg) − 0.1529",
        limitations=[
            "中国成人常用经验公式，个体差异存在。",
            "不用于儿童，也不用于药物剂量换算。",
        ],
        fn=_bsa,
        render=lambda v, shown: (
            f"0.0061×{v['height_cm']} + 0.0128×{v['weight_kg']} − 0.1529 = {shown} m²"
        ),
        audit_cases=[
            ({"height_cm": "170", "weight_kg": "65"}, "1.72"),
            ({"height_cm": "160", "weight_kg": "55"}, "1.53"),
        ],
    ),
    "map": FormulaSpec(
        key="map",
        citation="AHA-MAP",
        inputs=[
            _number("sbp", "收缩压", "mmHg", "50", "300", "1"),
            _number("dbp", "舒张压", "mmHg", "30", "200", "1"),
        ],
        output=_output("平均动脉压", "mmHg", "20", "200", 1, "no_direction"),
        method="(收缩压 + 2 × 舒张压) ÷ 3",
        limitations=[
            "只做算术换算，不评价血压高低，也不提示是否需处理。",
            "心律失常或测量误差会影响真实性。",
        ],
        fn=_map,
        render=lambda v, shown: (
            f"({v['sbp']} + 2×{v['dbp']}) ÷ 3 = {shown} mmHg"
        ),
        audit_cases=[
            ({"sbp": "120", "dbp": "80"}, "93.3"),
            ({"sbp": "90", "dbp": "60"}, "70.0"),
        ],
    ),
    "corrected_calcium": FormulaSpec(
        key="corrected_calcium",
        citation="CALC-CORRECTED",
        inputs=[
            _number("calcium_mmol_l", "血清总钙", "mmol/L", "1", "4", "0.01"),
            _number("albumin_g_l", "血清白蛋白", "g/L", "10", "60", "0.1"),
        ],
        output=_output("校正血钙", "mmol/L", "0.5", "5", 2, "no_direction"),
        method="血钙(mmol/L) + 0.02 × (40 − 白蛋白 g/L)",
        limitations=[
            "仅在低白蛋白时用于估算，不替代实测离子钙。",
            "不评价血钙是否异常，也不提示处理方式。",
        ],
        fn=_corrected_calcium,
        render=lambda v, shown: (
            f"{v['calcium_mmol_l']} + 0.02×({40} − {v['albumin_g_l']}) = {shown} mmol/L"
        ),
        audit_cases=[
            ({"calcium_mmol_l": "2.0", "albumin_g_l": "30"}, "2.20"),
            ({"calcium_mmol_l": "2.2", "albumin_g_l": "40"}, "2.20"),
        ],
    ),
    "anion_gap": FormulaSpec(
        key="anion_gap",
        citation="AG-CALC",
        inputs=[
            _number("sodium", "血钠", "mmol/L", "100", "180", "1"),
            _number("chloride", "血氯", "mmol/L", "60", "140", "1"),
            _number("bicarbonate", "碳酸氢根", "mmol/L", "5", "50", "1"),
        ],
        output=_output("阴离子间隙", "mmol/L", "-10", "60", 1, "no_direction"),
        method="血钠 − 血氯 − 碳酸氢根",
        limitations=[
            "未校正白蛋白，低白蛋白时会低估。",
            "不判断酸碱失衡类型，也不提示处理方式。",
        ],
        fn=_anion_gap,
        render=lambda v, shown: (
            f"{v['sodium']} − {v['chloride']} − {v['bicarbonate']} = {shown} mmol/L"
        ),
        audit_cases=[
            ({"sodium": "140", "chloride": "104", "bicarbonate": "24"}, "12.0"),
            ({"sodium": "135", "chloride": "100", "bicarbonate": "20"}, "15.0"),
        ],
    ),
    "osmolality": FormulaSpec(
        key="osmolality",
        citation="OSM-CALC",
        inputs=[
            _number("sodium", "血钠", "mmol/L", "100", "180", "1"),
            _number("glucose_mmol_l", "血糖", "mmol/L", "0", "60", "0.1"),
            _number("urea_mmol_l", "尿素", "mmol/L", "0", "60", "0.1"),
        ],
        output=_output("血浆渗透压", "mOsm/kg", "200", "400", 1, "no_direction"),
        method="2 × 血钠 + 血糖 + 尿素（均为 mmol/L）",
        limitations=[
            "为估算值，未计入乙醇等渗透性物质。",
            "不判断高渗状态，也不提示补液方案。",
        ],
        fn=_osmolality,
        render=lambda v, shown: (
            f"2×{v['sodium']} + {v['glucose_mmol_l']} + {v['urea_mmol_l']} = {shown} mOsm/kg"
        ),
        audit_cases=[
            (
                {"sodium": "140", "glucose_mmol_l": "5", "urea_mmol_l": "5"},
                "290.0",
            ),
            (
                {"sodium": "150", "glucose_mmol_l": "10", "urea_mmol_l": "8"},
                "318.0",
            ),
        ],
    ),
    "shock_index": FormulaSpec(
        key="shock_index",
        citation="SHOCK-INDEX",
        inputs=[
            _number("heart_rate", "心率", "次/分", "20", "250", "1"),
            _number("sbp", "收缩压", "mmHg", "50", "300", "1"),
        ],
        output=_output("休克指数", "", "0.1", "5", 2, "higher_is_worse"),
        method="心率 ÷ 收缩压",
        limitations=[
            "只做比值计算，不构成休克诊断。",
            "受心率药物、起搏器与测量误差影响。",
        ],
        fn=_shock_index,
        render=lambda v, shown: f"{v['heart_rate']} ÷ {v['sbp']} = {shown}",
        audit_cases=[
            ({"heart_rate": "100", "sbp": "100"}, "1.00"),
            ({"heart_rate": "120", "sbp": "80"}, "1.50"),
        ],
    ),
    "child_weight": FormulaSpec(
        key="child_weight",
        citation="CN-PED-WEIGHT",
        inputs=[_number("age_years", "年龄", "岁", "1", "12", "0.5")],
        output=_output("估算体重", "kg", "5", "50", 1, "no_direction"),
        method="年龄（岁）× 2 + 8",
        limitations=[
            "仅为 1–12 岁儿童的粗略估算，个体差异大。",
            "不用于药物剂量计算，也不能替代实测体重。",
        ],
        fn=_child_weight,
        render=lambda v, shown: f"{v['age_years']} × 2 + 8 = {shown} kg",
        audit_cases=[
            ({"age_years": "5"}, "18.0"),
            ({"age_years": "10"}, "28.0"),
        ],
    ),
}


# Department-catalog formulas live in a sibling module to keep this file
# reviewable. The import sits at the end on purpose: ``formulas_extra``
# imports ``FormulaSpec`` and the field helpers from here, so those names
# must already exist when this line runs.
from .formulas_extra import EXTRA_FORMULAS  # noqa: E402

FORMULAS.update(EXTRA_FORMULAS)
