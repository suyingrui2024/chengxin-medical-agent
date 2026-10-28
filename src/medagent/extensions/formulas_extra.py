"""Additional named formulas for the department catalog.

Imported by ``formulas.py`` at the end of its module body, so
``FormulaSpec`` and the field helpers are already defined when this
module runs. Content still cannot add formulas: every name here is
imported by code, never by a pack.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .formulas import FormulaSpec, _number, _output

D = Decimal


def _enum(identity, label, options):
    return {"id": identity, "label": label, "type": "enum", "options": options}


def _date(identity, label, low, high):
    return {"id": identity, "label": label, "type": "date", "min": low, "max": high}


SEX = [
    {"value": "male", "label": "男"},
    {"value": "female", "label": "女"},
]

EXTRA_FORMULAS = {}


def add(spec):
    EXTRA_FORMULAS[spec.key] = spec


add(
    FormulaSpec(
        key="rpp",
        citation="RPP-CALC",
        inputs=[
            _number("heart_rate", "心率", "次/分", "20", "250", "1"),
            _number("sbp", "收缩压", "mmHg", "50", "300", "1"),
        ],
        output=_output("心率血压乘积", "mmHg·次/分", "1000", "75000", 0, "no_direction"),
        method="心率 × 收缩压",
        limitations=["只做乘法，不评价心肌耗氧是否过高。"],
        fn=lambda v: v["heart_rate"] * v["sbp"],
        render=lambda v, shown: f"{v['heart_rate']} × {v['sbp']} = {shown}",
        audit_cases=[({"heart_rate": "80", "sbp": "120"}, "9600")],
    )
)

add(
    FormulaSpec(
        key="six_min_walk",
        citation="6MWD-ENRIGHT",
        inputs=[
            _number("height_cm", "身高", "cm", "100", "220", "0.1"),
            _number("age_years", "年龄", "岁", "18", "100", "1"),
            _number("weight_kg", "体重", "kg", "25", "200", "0.1"),
            _enum("sex", "性别", SEX),
        ],
        output=_output("预计 6 分钟步行距离", "m", "100", "900", 0, "no_direction"),
        method="男性 7.57×身高−5.02×年龄−1.76×体重−309；女性 2.11×身高−2.29×体重−5.78×年龄+667",
        limitations=["为欧美人群回归方程，中国人群仅为参考。", "不能替代实测步行距离。"],
        fn=lambda v: (
            D("7.57") * v["height_cm"]
            - D("5.02") * v["age_years"]
            - D("1.76") * v["weight_kg"]
            - D(309)
            if v["sex"] == "male"
            else D("2.11") * v["height_cm"]
            - D("2.29") * v["weight_kg"]
            - D("5.78") * v["age_years"]
            + D(667)
        ),
        render=lambda v, shown: f"按性别回归方程估算 = {shown} m",
        audit_cases=[
            ({"height_cm": "170", "age_years": "60", "weight_kg": "70", "sex": "male"}, "554"),
            ({"height_cm": "160", "age_years": "60", "weight_kg": "60", "sex": "female"}, "520"),
        ],
    )
)

add(
    FormulaSpec(
        key="pf_ratio",
        citation="PF-RATIO",
        inputs=[
            _number("pao2", "动脉氧分压", "mmHg", "20", "600", "1"),
            _number("fio2", "吸氧浓度", "小数", "0.21", "1", "0.01"),
        ],
        output=_output("氧合指数", "mmHg", "20", "600", 0, "higher_is_better"),
        method="PaO2 ÷ FiO2",
        limitations=["需血气结果；吸氧浓度按小数填写（如 0.4）。", "不判断是否符合呼吸衰竭标准。"],
        fn=lambda v: v["pao2"] / v["fio2"],
        render=lambda v, shown: f"{v['pao2']} ÷ {v['fio2']} = {shown} mmHg",
        audit_cases=[({"pao2": "80", "fio2": "0.4"}, "200")],
    )
)

add(
    FormulaSpec(
        key="aa_gradient",
        citation="AA-GRADIENT",
        inputs=[
            _number("pao2", "动脉氧分压", "mmHg", "20", "600", "1"),
            _number("paco2", "动脉二氧化碳分压", "mmHg", "10", "120", "1"),
            _number("fio2", "吸氧浓度", "小数", "0.21", "1", "0.01"),
        ],
        output=_output("肺泡-动脉氧梯度", "mmHg", "-50", "600", 1, "higher_is_worse"),
        method="FiO2×(760−47) − PaCO2÷0.8 − PaO2",
        limitations=["按海平面大气压估算，高原地区不适用。", "不判断低氧原因。"],
        fn=lambda v: (
            v["fio2"] * D(713) - v["paco2"] / D("0.8") - v["pao2"]
        ),
        render=lambda v, shown: (
            f"{v['fio2']}×713 − {v['paco2']}÷0.8 − {v['pao2']} = {shown} mmHg"
        ),
        audit_cases=[({"pao2": "80", "paco2": "40", "fio2": "0.21"}, "19.7")],
    )
)

add(
    FormulaSpec(
        key="fib4",
        citation="FIB-4",
        inputs=[
            _number("age_years", "年龄", "岁", "18", "100", "1"),
            _number("ast", "AST", "U/L", "1", "2000", "0.1"),
            _number("alt", "ALT", "U/L", "1", "2000", "0.1"),
            _number("platelet", "血小板", "×10⁹/L", "1", "1000", "1"),
        ],
        output=_output("FIB-4 指数", "", "0", "100", 2, "higher_is_worse"),
        method="年龄 × AST ÷ (血小板 × √ALT)",
        limitations=["需同日化验值；不构成肝纤维化诊断。"],
        fn=lambda v: (
            v["age_years"] * v["ast"] / (v["platelet"] * v["alt"].sqrt())
        ),
        render=lambda v, shown: (
            f"{v['age_years']}×{v['ast']} ÷ ({v['platelet']}×√{v['alt']}) = {shown}"
        ),
        audit_cases=[
            ({"age_years": "50", "ast": "40", "alt": "40", "platelet": "200"}, "1.58")
        ],
    )
)

add(
    FormulaSpec(
        key="apri",
        citation="APRI",
        inputs=[
            _number("ast", "AST", "U/L", "1", "2000", "0.1"),
            _number("ast_uln", "AST 正常上限", "U/L", "10", "100", "1"),
            _number("platelet", "血小板", "×10⁹/L", "1", "1000", "1"),
        ],
        output=_output("APRI 指数", "", "0", "100", 2, "higher_is_worse"),
        method="(AST ÷ AST 上限) × 100 ÷ 血小板",
        limitations=["需与本院检验上限一致；不构成肝纤维化诊断。"],
        fn=lambda v: (v["ast"] / v["ast_uln"]) * D(100) / v["platelet"],
        render=lambda v, shown: (
            f"({v['ast']}÷{v['ast_uln']})×100 ÷ {v['platelet']} = {shown}"
        ),
        audit_cases=[({"ast": "40", "ast_uln": "40", "platelet": "200"}, "0.50")],
    )
)

add(
    FormulaSpec(
        key="waist_height",
        citation="WHtR",
        inputs=[
            _number("waist_cm", "腰围", "cm", "30", "200", "0.1"),
            _number("height_cm", "身高", "cm", "100", "220", "0.1"),
        ],
        output=_output("腰高比", "", "0.2", "2", 2, "higher_is_worse"),
        method="腰围 ÷ 身高",
        limitations=["只做比值，不判断中心性肥胖。"],
        fn=lambda v: v["waist_cm"] / v["height_cm"],
        render=lambda v, shown: f"{v['waist_cm']} ÷ {v['height_cm']} = {shown}",
        audit_cases=[({"waist_cm": "80", "height_cm": "170"}, "0.47")],
    )
)

add(
    FormulaSpec(
        key="homa_ir",
        citation="HOMA-IR",
        inputs=[
            _number("glucose_mmol_l", "空腹血糖", "mmol/L", "1", "40", "0.1"),
            _number("insulin_uiu_ml", "空腹胰岛素", "µIU/mL", "0.1", "300", "0.1"),
        ],
        output=_output("HOMA-IR", "", "0", "200", 2, "higher_is_worse"),
        method="空腹血糖 × 空腹胰岛素 ÷ 22.5",
        limitations=["需空腹采血；不构成胰岛素抵抗诊断。"],
        fn=lambda v: v["glucose_mmol_l"] * v["insulin_uiu_ml"] / D("22.5"),
        render=lambda v, shown: (
            f"{v['glucose_mmol_l']}×{v['insulin_uiu_ml']} ÷ 22.5 = {shown}"
        ),
        audit_cases=[({"glucose_mmol_l": "5.0", "insulin_uiu_ml": "9"}, "2.00")],
    )
)

add(
    FormulaSpec(
        key="upcr",
        citation="UPCR",
        inputs=[
            _number("urine_protein_mg_dl", "尿蛋白", "mg/dL", "0", "2000", "0.1"),
            _number("urine_creatinine_mg_dl", "尿肌酐", "mg/dL", "1", "500", "0.1"),
        ],
        output=_output("尿蛋白肌酐比", "mg/mg", "0", "20", 2, "higher_is_worse"),
        method="尿蛋白 ÷ 尿肌酐",
        limitations=["建议晨尿；不构成肾脏病诊断。"],
        fn=lambda v: v["urine_protein_mg_dl"] / v["urine_creatinine_mg_dl"],
        render=lambda v, shown: (
            f"{v['urine_protein_mg_dl']} ÷ {v['urine_creatinine_mg_dl']} = {shown} mg/mg"
        ),
        audit_cases=[({"urine_protein_mg_dl": "30", "urine_creatinine_mg_dl": "100"}, "0.30")],
    )
)

add(
    FormulaSpec(
        key="free_water_deficit",
        citation="FWD-CALC",
        inputs=[
            _number("weight_kg", "体重", "kg", "2", "300", "0.1"),
            _number("sodium", "血钠", "mmol/L", "100", "200", "1"),
            _enum("sex", "性别", SEX),
        ],
        output=_output("自由水缺失", "L", "0", "30", 2, "higher_is_worse"),
        method="系数 × 体重 × (血钠 ÷ 140 − 1)，男性系数 0.6、女性 0.5",
        limitations=["为估算值，不构成补液方案；补液速度与种类须由医生决定。"],
        fn=lambda v: (
            (D("0.6") if v["sex"] == "male" else D("0.5"))
            * v["weight_kg"]
            * (v["sodium"] / D(140) - D(1))
        ),
        render=lambda v, shown: (
            f"系数×{v['weight_kg']}×({v['sodium']}÷140−1) = {shown} L"
        ),
        audit_cases=[({"weight_kg": "60", "sodium": "160", "sex": "male"}, "5.14")],
    )
)

add(
    FormulaSpec(
        key="fena",
        citation="FENA",
        inputs=[
            _number("urine_sodium", "尿钠", "mmol/L", "0", "300", "1"),
            _number("plasma_creatinine", "血肌酐", "µmol/L", "10", "2000", "1"),
            _number("plasma_sodium", "血钠", "mmol/L", "100", "200", "1"),
            _number("urine_creatinine", "尿肌酐", "µmol/L", "100", "50000", "1"),
        ],
        output=_output("尿钠排泄分数", "%", "0", "100", 2, "no_direction"),
        method="(尿钠 × 血肌酐) ÷ (血钠 × 尿肌酐) × 100",
        limitations=["利尿剂会影响结果；不判断急性肾损伤病因。"],
        fn=lambda v: (
            (v["urine_sodium"] * v["plasma_creatinine"])
            / (v["plasma_sodium"] * v["urine_creatinine"])
            * D(100)
        ),
        render=lambda v, shown: f"按公式计算 = {shown} %",
        audit_cases=[
            (
                {
                    "urine_sodium": "40",
                    "plasma_creatinine": "100",
                    "plasma_sodium": "140",
                    "urine_creatinine": "10000",
                },
                "0.29",
            )
        ],
    )
)

add(
    FormulaSpec(
        key="rfi",
        citation="RFI",
        inputs=[
            _number("urine_sodium", "尿钠", "mmol/L", "0", "300", "1"),
            _number("plasma_creatinine", "血肌酐", "µmol/L", "10", "2000", "1"),
            _number("urine_creatinine", "尿肌酐", "µmol/L", "100", "50000", "1"),
        ],
        output=_output("肾衰指数", "", "0", "100", 2, "no_direction"),
        method="(尿钠 × 血肌酐) ÷ 尿肌酐",
        limitations=["不判断急性肾损伤病因。"],
        fn=lambda v: (
            v["urine_sodium"] * v["plasma_creatinine"] / v["urine_creatinine"]
        ),
        render=lambda v, shown: (
            f"({v['urine_sodium']}×{v['plasma_creatinine']}) ÷ {v['urine_creatinine']} = {shown}"
        ),
        audit_cases=[
            (
                {"urine_sodium": "40", "plasma_creatinine": "100", "urine_creatinine": "10000"},
                "0.40",
            )
        ],
    )
)

add(
    FormulaSpec(
        key="retic_corrected",
        citation="RETIC-CORRECTED",
        inputs=[
            _number("retic_percent", "网织红细胞百分比", "%", "0", "30", "0.1"),
            _number("hematocrit", "红细胞压积", "%", "10", "70", "0.1"),
        ],
        output=_output("校正网织红细胞", "%", "0", "30", 2, "no_direction"),
        method="网织红细胞% × (实测红细胞压积 ÷ 45)",
        limitations=["按正常压积 45% 校正；不判断贫血类型。"],
        fn=lambda v: v["retic_percent"] * (v["hematocrit"] / D(45)),
        render=lambda v, shown: (
            f"{v['retic_percent']}×({v['hematocrit']}÷45) = {shown} %"
        ),
        audit_cases=[({"retic_percent": "2", "hematocrit": "30"}, "1.33")],
    )
)

add(
    FormulaSpec(
        key="anc",
        citation="ANC",
        inputs=[
            _number("wbc", "白细胞", "×10⁹/L", "0.1", "100", "0.1"),
            _number("neutrophil_percent", "中性粒细胞百分比", "%", "0", "100", "0.1"),
        ],
        output=_output("中性粒细胞绝对计数", "×10⁹/L", "0", "100", 2, "higher_is_better"),
        method="白细胞 × 中性粒细胞% ÷ 100",
        limitations=["未计入杆状核；不判断感染风险。"],
        fn=lambda v: v["wbc"] * v["neutrophil_percent"] / D(100),
        render=lambda v, shown: (
            f"{v['wbc']}×{v['neutrophil_percent']}÷100 = {shown} ×10⁹/L"
        ),
        audit_cases=[({"wbc": "4.0", "neutrophil_percent": "50"}, "2.00")],
    )
)

add(
    FormulaSpec(
        key="tsat",
        citation="TSAT",
        inputs=[
            _number("iron_umol_l", "血清铁", "µmol/L", "0", "100", "0.1"),
            _number("tibc_umol_l", "总铁结合力", "µmol/L", "10", "150", "0.1"),
        ],
        output=_output("转铁蛋白饱和度", "%", "0", "100", 1, "no_direction"),
        method="血清铁 ÷ 总铁结合力 × 100",
        limitations=["受近期补铁与炎症影响；不判断铁缺乏或过载。"],
        fn=lambda v: v["iron_umol_l"] / v["tibc_umol_l"] * D(100),
        render=lambda v, shown: (
            f"{v['iron_umol_l']} ÷ {v['tibc_umol_l']} × 100 = {shown} %"
        ),
        audit_cases=[({"iron_umol_l": "10", "tibc_umol_l": "50"}, "20.0")],
    )
)

add(
    FormulaSpec(
        key="fever_days",
        citation="FEVER-DAYS",
        inputs=[
            _date("onset", "发热开始日期", "2000-01-01", "2100-12-31"),
            _date("measure", "记录日期", "2000-01-01", "2100-12-31"),
        ],
        output=_output("发热持续天数", "天", "0", "365", 0, "higher_is_worse"),
        method="记录日期 − 发热开始日期",
        limitations=["只做日期相减，不判断发热原因。"],
        fn=lambda v: D((v["measure"] - v["onset"]).days),
        render=lambda v, shown: (
            f"{v['measure'].isoformat()} − {v['onset'].isoformat()} = {shown} 天"
        ),
        audit_cases=[
            ({"onset": "2026-10-01", "measure": "2026-10-04"}, "3"),
        ],
    )
)

add(
    FormulaSpec(
        key="prostate_volume",
        citation="PROSTATE-VOL",
        inputs=[
            _number("length_cm", "前后径", "cm", "1", "15", "0.1"),
            _number("width_cm", "左右径", "cm", "1", "15", "0.1"),
            _number("height_cm", "上下径", "cm", "1", "15", "0.1"),
        ],
        output=_output("前列腺体积", "mL", "1", "500", 1, "higher_is_worse"),
        method="0.52 × 前后径 × 左右径 × 上下径",
        limitations=["按椭球公式估算，与实测体积有差异。", "不判断良性增生或恶性。"],
        fn=lambda v: D("0.52") * v["length_cm"] * v["width_cm"] * v["height_cm"],
        render=lambda v, shown: (
            f"0.52×{v['length_cm']}×{v['width_cm']}×{v['height_cm']} = {shown} mL"
        ),
        audit_cases=[({"length_cm": "4", "width_cm": "4", "height_cm": "3"}, "25.0")],
    )
)

add(
    FormulaSpec(
        key="psa_density",
        citation="PSA-DENSITY",
        inputs=[
            _number("psa_ng_ml", "PSA", "ng/mL", "0", "1000", "0.01"),
            _number("volume_ml", "前列腺体积", "mL", "1", "500", "0.1"),
        ],
        output=_output("PSA 密度", "ng/mL/mL", "0", "10", 2, "higher_is_worse"),
        method="PSA ÷ 前列腺体积",
        limitations=["体积测量误差会放大密度误差。", "不构成前列腺癌诊断。"],
        fn=lambda v: v["psa_ng_ml"] / v["volume_ml"],
        render=lambda v, shown: f"{v['psa_ng_ml']} ÷ {v['volume_ml']} = {shown}",
        audit_cases=[({"psa_ng_ml": "4", "volume_ml": "40"}, "0.10")],
    )
)

add(
    FormulaSpec(
        key="ppofev1",
        citation="PPO-FEV1",
        inputs=[
            _number("fev1_l", "术前 FEV1", "L", "0.3", "8", "0.01"),
            _number("resected_segments", "切除肺段数", "段", "0", "19", "1"),
            _number("total_segments", "总肺段数", "段", "10", "19", "1"),
        ],
        output=_output("术后预计 FEV1", "L", "0.1", "8", 2, "higher_is_better"),
        method="FEV1 × (1 − 切除肺段数 ÷ 总肺段数)",
        limitations=["按肺段计数估算，未计入通气血流分布。", "不判断能否耐受手术。"],
        fn=lambda v: v["fev1_l"] * (D(1) - v["resected_segments"] / v["total_segments"]),
        render=lambda v, shown: (
            f"{v['fev1_l']}×(1−{v['resected_segments']}÷{v['total_segments']}) = {shown} L"
        ),
        audit_cases=[
            ({"fev1_l": "2.0", "resected_segments": "5", "total_segments": "19"}, "1.47")
        ],
    )
)

add(
    FormulaSpec(
        key="light_criteria",
        citation="LIGHT-CRITERIA",
        inputs=[
            _number("pleural_protein", "胸水蛋白", "g/L", "0", "100", "0.1"),
            _number("serum_protein", "血清蛋白", "g/L", "10", "120", "0.1"),
            _number("pleural_ldh", "胸水 LDH", "U/L", "0", "10000", "1"),
            _number("serum_ldh", "血清 LDH", "U/L", "10", "10000", "1"),
        ],
        output=_output("Light 标准判定", "分类", "0", "1", 0, "no_direction"),
        method="满足任一项即为渗出液：胸水/血清蛋白 > 0.5；胸水/血清 LDH > 0.6；胸水 LDH > 血清上限 2/3（此处用 0.67×血清 LDH 近似）",
        limitations=["为简化判定，未引入血清 LDH 正常上限。", "不构成病因诊断。"],
        fn=lambda v: D(1)
        if (
            v["pleural_protein"] / v["serum_protein"] > D("0.5")
            or v["pleural_ldh"] / v["serum_ldh"] > D("0.6")
            or v["pleural_ldh"] > D("0.67") * v["serum_ldh"]
        )
        else D(0),
        render=lambda v, shown: f"按 Light 标准判定 = {shown}",
        audit_cases=[
            (
                {
                    "pleural_protein": "50",
                    "serum_protein": "70",
                    "pleural_ldh": "300",
                    "serum_ldh": "200",
                },
                "1",
            ),
            (
                {
                    "pleural_protein": "20",
                    "serum_protein": "70",
                    "pleural_ldh": "80",
                    "serum_ldh": "200",
                },
                "0",
            ),
        ],
    )
)

add(
    FormulaSpec(
        key="edd",
        citation="GEST-AGE",
        inputs=[
            _date("lmp", "末次月经第一天", "2000-01-01", "2100-12-31"),
            _date("today", "记录日期", "2000-01-01", "2100-12-31"),
        ],
        output=_output("孕周", "周", "0", "45", 1, "no_direction"),
        method="(记录日期 − 末次月经) ÷ 7",
        limitations=["按末次月经推算，月经不规律者误差大。", "不替代超声核对孕周。"],
        fn=lambda v: D((v["today"] - v["lmp"]).days) / D(7),
        render=lambda v, shown: (
            f"({v['today'].isoformat()} − {v['lmp'].isoformat()}) ÷ 7 = {shown} 周"
        ),
        audit_cases=[({"lmp": "2026-01-01", "today": "2026-03-01"}, "8.4")],
    )
)

add(
    FormulaSpec(
        key="pph_volume",
        citation="PPH-WEIGHING",
        inputs=[
            _number("wet_weight_g", "吸血后敷料湿重", "g", "0", "10000", "1"),
            _number("dry_weight_g", "敷料干重", "g", "0", "10000", "1"),
        ],
        output=_output("估计出血量", "mL", "0", "5000", 0, "higher_is_worse"),
        method="(湿重 − 干重) ÷ 1.05",
        limitations=["称重法为估算，未计入产道积血与纱布残留。", "不判断出血原因。"],
        fn=lambda v: (v["wet_weight_g"] - v["dry_weight_g"]) / D("1.05"),
        render=lambda v, shown: (
            f"({v['wet_weight_g']}−{v['dry_weight_g']}) ÷ 1.05 = {shown} mL"
        ),
        audit_cases=[({"wet_weight_g": "1050", "dry_weight_g": "0"}, "1000")],
    )
)

add(
    FormulaSpec(
        key="child_height",
        citation="CN-PED-HEIGHT",
        inputs=[_number("age_years", "年龄", "岁", "2", "12", "0.5")],
        output=_output("估算身高", "cm", "70", "170", 1, "no_direction"),
        method="年龄（岁）× 7 + 75",
        limitations=["仅为 2–12 岁儿童的粗略估算。", "不能替代实测身高。"],
        fn=lambda v: v["age_years"] * D(7) + D(75),
        render=lambda v, shown: f"{v['age_years']} × 7 + 75 = {shown} cm",
        audit_cases=[({"age_years": "5"}, "110.0")],
    )
)

add(
    FormulaSpec(
        key="burn_area",
        citation="RULE-OF-NINES",
        inputs=[
            _number("head", "头颈", "%", "0", "100", "0.5"),
            _number("trunk_front", "躯干前", "%", "0", "100", "0.5"),
            _number("trunk_back", "躯干后", "%", "0", "100", "0.5"),
            _number("arm_left", "左上肢", "%", "0", "100", "0.5"),
            _number("arm_right", "右上肢", "%", "0", "100", "0.5"),
            _number("leg_left", "左下肢", "%", "0", "100", "0.5"),
            _number("leg_right", "右下肢", "%", "0", "100", "0.5"),
            _number("perineum", "会阴", "%", "0", "100", "0.5"),
            _number("other", "其他", "%", "0", "100", "0.5"),
        ],
        output=_output("烧伤面积", "%TBSA", "0", "100", 1, "higher_is_worse"),
        method="各部位体表面积百分比相加（九分法）",
        limitations=["成人九分法；儿童头部比例不同，不适用。", "不判断烧伤深度，也不决定补液量。"],
        fn=lambda v: (
            v["head"]
            + v["trunk_front"]
            + v["trunk_back"]
            + v["arm_left"]
            + v["arm_right"]
            + v["leg_left"]
            + v["leg_right"]
            + v["perineum"]
            + v["other"]
        ),
        render=lambda v, shown: f"各部位相加 = {shown} %TBSA",
        audit_cases=[
            (
                {
                    "head": "9",
                    "trunk_front": "18",
                    "trunk_back": "18",
                    "arm_left": "9",
                    "arm_right": "9",
                    "leg_left": "18",
                    "leg_right": "18",
                    "perineum": "1",
                    "other": "0",
                },
                "100.0",
            )
        ],
    )
)

add(
    FormulaSpec(
        key="logmar",
        citation="LOGMAR",
        inputs=[_number("decimal_acuity", "小数视力", "", "0.01", "2", "0.01")],
        output=_output("LogMAR 视力", "logMAR", "-2", "2", 2, "higher_is_worse"),
        method="−log10(小数视力)",
        limitations=["只做换算，不判断视力是否正常。"],
        fn=lambda v: -(v["decimal_acuity"].log10()),
        render=lambda v, shown: f"−log10({v['decimal_acuity']}) = {shown}",
        audit_cases=[({"decimal_acuity": "1.0"}, "0.00"), ({"decimal_acuity": "0.1"}, "1.00")],
    )
)

add(
    FormulaSpec(
        key="dmft",
        citation="DMFT",
        inputs=[
            _number("decayed", "龋齿数", "颗", "0", "32", "1"),
            _number("missing", "缺失牙数", "颗", "0", "32", "1"),
            _number("filled", "充填牙数", "颗", "0", "32", "1"),
        ],
        output=_output("DMFT 指数", "颗", "0", "32", 0, "higher_is_worse"),
        method="龋齿 + 缺失 + 充填",
        limitations=["同一颗牙只计一次；不判断龋坏程度。"],
        fn=lambda v: v["decayed"] + v["missing"] + v["filled"],
        render=lambda v, shown: (
            f"{v['decayed']} + {v['missing']} + {v['filled']} = {shown} 颗"
        ),
        audit_cases=[({"decayed": "2", "missing": "1", "filled": "3"}, "6")],
    )
)

add(
    FormulaSpec(
        key="vaccine_due",
        citation="VACCINE-INTERVAL",
        inputs=[
            _date("last_dose", "上一剂接种日期", "2000-01-01", "2100-12-31"),
            _number("interval_months", "接种间隔", "月", "1", "120", "1"),
            _date("today", "记录日期", "2000-01-01", "2100-12-31"),
        ],
        output=_output("距下次接种", "天", "-3650", "3650", 0, "no_direction"),
        method="(上一剂日期 + 间隔月数×30.44) − 记录日期",
        limitations=["按平均月长估算，不替代接种程序表。", "不判断是否应接种。"],
        fn=lambda v: D(
            (
                date.fromordinal(
                    v["last_dose"].toordinal()
                    + int(round(float(v["interval_months"]) * 30.44))
                )
                - v["today"]
            ).days
        ),
        render=lambda v, shown: f"按间隔推算，距下次接种 {shown} 天",
        audit_cases=[
            ({"last_dose": "2026-01-01", "interval_months": "1", "today": "2026-01-01"}, "30"),
        ],
    )
)

add(
    FormulaSpec(
        key="abi",
        citation="ABI",
        inputs=[
            _number("ankle_sbp", "踝部收缩压", "mmHg", "20", "300", "1"),
            _number("brachial_sbp", "肱动脉收缩压", "mmHg", "20", "300", "1"),
        ],
        output=_output("踝肱指数", "", "0.1", "2", 2, "higher_is_better"),
        method="踝部收缩压 ÷ 肱动脉收缩压",
        limitations=["需静息平卧测量；不构成外周动脉病诊断。"],
        fn=lambda v: v["ankle_sbp"] / v["brachial_sbp"],
        render=lambda v, shown: f"{v['ankle_sbp']} ÷ {v['brachial_sbp']} = {shown}",
        audit_cases=[({"ankle_sbp": "100", "brachial_sbp": "120"}, "0.83")],
    )
)


# ---------------------------------------------------------------------------
# Second tranche: clinician-only scores that are arithmetic rather than
# item sums. EASI/PASI multiply an area score by a severity sum per body
# region, CPI takes the worst sextant, CAM is a boolean rule, and the two
# renal formulas carry age/sex branches.
# ---------------------------------------------------------------------------

SEVERITY_0_3 = [
    {"value": "0", "label": "无"},
    {"value": "1", "label": "轻度"},
    {"value": "2", "label": "中度"},
    {"value": "3", "label": "重度"},
]
AREA_0_6 = [
    {"value": "0", "label": "0%"},
    {"value": "1", "label": "1–9%"},
    {"value": "2", "label": "10–19%"},
    {"value": "3", "label": "20–29%"},
    {"value": "4", "label": "30–49%"},
    {"value": "5", "label": "50–69%"},
    {"value": "6", "label": "70–100%"},
]


def _enum(identity, label, options):
    return {"id": identity, "label": label, "type": "enum", "options": options}


def _easi_inputs():
    out = []
    for region, name in (("head", "头颈"), ("arms", "上肢"), ("trunk", "躯干"), ("legs", "下肢")):
        out.append(_enum(f"{region}_area", f"{name}受累面积", AREA_0_6))
        for sign, label in (
            ("e", "红斑"),
            ("i", "水肿浸润"),
            ("x", "抓痕"),
            ("l", "苔藓化"),
        ):
            out.append(_enum(f"{region}_{sign}", f"{name}{label}", SEVERITY_0_3))
    return out


def _easi(values):
    total = D(0)
    for region, coefficient in (
        ("head", D("0.1")),
        ("arms", D("0.2")),
        ("trunk", D("0.3")),
        ("legs", D("0.4")),
    ):
        area = D(values[f"{region}_area"])
        severity = (
            D(values[f"{region}_e"])
            + D(values[f"{region}_i"])
            + D(values[f"{region}_x"])
            + D(values[f"{region}_l"])
        )
        total += coefficient * area * severity
    return total


add(
    FormulaSpec(
        key="easi",
        citation="EASI",
        inputs=_easi_inputs(),
        output=_output("湿疹面积与严重度指数", "分", "0", "72", 1, "higher_is_worse"),
        method="Σ 区域系数 × 面积分 ×（红斑+水肿浸润+抓痕+苔藓化），系数 0.1/0.2/0.3/0.4",
        limitations=["面积与体征均为目视分级，不同评估者间存在差异。", "不构成湿疹诊断，也不判断治疗方案。"],
        fn=_easi,
        render=lambda v, shown: f"按四区面积×体征加权求和 = {shown} 分",
        audit_cases=[
            (
                {
                    "head_area": "2", "head_e": "1", "head_i": "1", "head_x": "0", "head_l": "0",
                    "arms_area": "2", "arms_e": "1", "arms_i": "1", "arms_x": "0", "arms_l": "0",
                    "trunk_area": "2", "trunk_e": "1", "trunk_i": "1", "trunk_x": "0", "trunk_l": "0",
                    "legs_area": "2", "legs_e": "1", "legs_i": "1", "legs_x": "0", "legs_l": "0",
                },
                "4.0",
            ),
        ],
    )
)


def _pasi_inputs():
    out = []
    for region, name in (("head", "头颈"), ("arms", "上肢"), ("trunk", "躯干"), ("legs", "下肢")):
        out.append(_enum(f"{region}_area", f"{name}受累面积", AREA_0_6))
        for sign, label in (("e", "红斑"), ("i", "浸润"), ("d", "脱屑")):
            out.append(_enum(f"{region}_{sign}", f"{name}{label}", SEVERITY_0_3))
    return out


def _pasi(values):
    total = D(0)
    for region, coefficient in (
        ("head", D("0.1")),
        ("arms", D("0.2")),
        ("trunk", D("0.3")),
        ("legs", D("0.4")),
    ):
        area = D(values[f"{region}_area"])
        severity = (
            D(values[f"{region}_e"])
            + D(values[f"{region}_i"])
            + D(values[f"{region}_d"])
        )
        total += coefficient * area * severity
    return total


add(
    FormulaSpec(
        key="pasi",
        citation="PASI",
        inputs=_pasi_inputs(),
        output=_output("银屑病面积与严重度指数", "分", "0", "54", 1, "higher_is_worse"),
        method="Σ 区域系数 × 面积分 ×（红斑+浸润+脱屑），系数 0.1/0.2/0.3/0.4",
        limitations=["面积与体征均为目视分级，不同评估者间存在差异。", "不构成银屑病诊断，也不判断治疗方案。"],
        fn=_pasi,
        render=lambda v, shown: f"按四区面积×体征加权求和 = {shown} 分",
        audit_cases=[
            (
                {
                    "head_area": "1", "head_e": "1", "head_i": "1", "head_d": "1",
                    "arms_area": "1", "arms_e": "1", "arms_i": "1", "arms_d": "1",
                    "trunk_area": "1", "trunk_e": "1", "trunk_i": "1", "trunk_d": "1",
                    "legs_area": "1", "legs_e": "1", "legs_i": "1", "legs_d": "1",
                },
                "3.0",
            ),
        ],
    )
)

SEXTANT_0_4 = [
    {"value": "0", "label": "0 健康"},
    {"value": "1", "label": "1 牙龈出血"},
    {"value": "2", "label": "2 牙石"},
    {"value": "3", "label": "3 牙周袋 4–5 mm"},
    {"value": "4", "label": "4 牙周袋 6 mm 以上"},
]
add(
    FormulaSpec(
        key="cpi",
        citation="CPI",
        inputs=[
            _enum("s1", "右上后牙区", SEXTANT_0_4),
            _enum("s2", "上前牙区", SEXTANT_0_4),
            _enum("s3", "左上后牙区", SEXTANT_0_4),
            _enum("s4", "右下后牙区", SEXTANT_0_4),
            _enum("s5", "下前牙区", SEXTANT_0_4),
            _enum("s6", "左下后牙区", SEXTANT_0_4),
        ],
        output=_output("社区牙周指数", "级", "0", "4", 0, "higher_is_worse"),
        method="六个分区分别记分，取最高分作为个体指数",
        limitations=["只做取最大值，不判断牙周炎分期。", "需探诊，不能自评。"],
        fn=lambda v: max(D(v[f"s{i}"]) for i in range(1, 7)),
        render=lambda v, shown: f"六分区最高分 = {shown} 级",
        audit_cases=[
            (
                {"s1": "1", "s2": "2", "s3": "1", "s4": "3", "s5": "0", "s6": "2"},
                "3",
            ),
        ],
    )
)

YESNO = [{"value": "0", "label": "否"}, {"value": "1", "label": "是"}]
add(
    FormulaSpec(
        key="cam",
        citation="CAM",
        inputs=[
            _enum("acute_onset", "急性起病或病程波动", YESNO),
            _enum("inattention", "注意力不集中", YESNO),
            _enum("disorganized", "思维混乱", YESNO),
            _enum("altered_consciousness", "意识水平改变", YESNO),
        ],
        output=_output("CAM 判定", "结果", "0", "1", 0, "higher_is_worse"),
        method="（急性起病或波动 且 注意力不集中）且（思维混乱 或 意识改变）",
        limitations=["为筛查判定，不构成谵妄诊断。", "需结合病史与检查，不能替代临床评估。"],
        fn=lambda v: D(1)
        if (
            v["acute_onset"] == "1"
            and v["inattention"] == "1"
            and (v["disorganized"] == "1" or v["altered_consciousness"] == "1")
        )
        else D(0),
        render=lambda v, shown: f"按 CAM 规则判定 = {shown}",
        audit_cases=[
            ({"acute_onset": "1", "inattention": "1", "disorganized": "1", "altered_consciousness": "0"}, "1"),
            ({"acute_onset": "1", "inattention": "0", "disorganized": "1", "altered_consciousness": "1"}, "0"),
        ],
    )
)

add(
    FormulaSpec(
        key="egfr_ckdepi2021",
        citation="CKD-EPI-2021",
        inputs=[
            _number("age_years", "年龄", "岁", "18", "120", "1"),
            _enum("sex", "性别", SEX),
            _number("creatinine_umol_l", "血肌酐", "µmol/L", "10", "2000", "1"),
        ],
        output=_output("估算肾小球滤过率", "mL/min/1.73m²", "0", "200", 1, "higher_is_better"),
        method="142 × min(Scr/k,1)^a × max(Scr/k,1)^−1.200 × 0.9938^年龄 ×（女性 ×1.012），k 男 0.9、女 0.7，a 男 −0.302、女 −0.241",
        limitations=["仅成人；肌肉量、妊娠、急性肾损伤时不可靠。", "不构成慢性肾脏病诊断或分期。"],
        fn=lambda v: (
            D(142)
            * min(
                v["creatinine_umol_l"] / D("88.4") / (D("0.7") if v["sex"] == "female" else D("0.9")),
                D(1),
            )
            ** (D("-0.241") if v["sex"] == "female" else D("-0.302"))
            * max(
                v["creatinine_umol_l"] / D("88.4") / (D("0.7") if v["sex"] == "female" else D("0.9")),
                D(1),
            )
            ** D("-1.200")
            * D("0.9938") ** v["age_years"]
            * (D("1.012") if v["sex"] == "female" else D(1))
        ),
        render=lambda v, shown: f"按 CKD-EPI 2021 公式计算 = {shown} mL/min/1.73m²",
        audit_cases=[
            ({"age_years": "50", "sex": "male", "creatinine_umol_l": "88"}, "92.2"),
        ],
    )
)

add(
    FormulaSpec(
        key="crcl_cg",
        citation="COCKCROFT-GAULT",
        inputs=[
            _number("age_years", "年龄", "岁", "18", "120", "1"),
            _number("weight_kg", "体重", "kg", "20", "300", "0.1"),
            _enum("sex", "性别", SEX),
            _number("creatinine_umol_l", "血肌酐", "µmol/L", "10", "2000", "1"),
        ],
        output=_output("肌酐清除率", "mL/min", "0", "400", 1, "higher_is_better"),
        method="(140 − 年龄) × 体重 ÷ (72 × Scr mg/dL)，女性 ×0.85",
        limitations=["估算值，肥胖与水肿患者偏差较大。", "不用于药物剂量调整，剂量须由医生决定。"],
        fn=lambda v: (
            (D(140) - v["age_years"])
            * v["weight_kg"]
            / (D(72) * (v["creatinine_umol_l"] / D("88.4")))
            * (D("0.85") if v["sex"] == "female" else D(1))
        ),
        render=lambda v, shown: f"按 Cockcroft-Gault 公式计算 = {shown} mL/min",
        audit_cases=[
            ({"age_years": "60", "weight_kg": "70", "sex": "male", "creatinine_umol_l": "88"}, "78.1"),
        ],
    )
)
