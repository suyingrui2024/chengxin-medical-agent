"""Registered conversion input families; manifests cannot supply executable policy."""
from .input_evidence import ALIASES, units_evidence
from .glucose import UNIT_ALIASES, glucose_evidence, glucose_subject


POLICIES = {
    "mass_or_volume": {
        "labels": {"kg": "千克 kg", "g": "克 g", "mg": "毫克 mg", "L": "升 L", "mL": "毫升 mL"},
        "aliases": ALIASES,
        "evidence": units_evidence,
        "subject": "单位换算",
        "scope": "仅同量纲换算，不判断摄入量或用药剂量。",
        "feedback": "未完成计算。请检查数值、单位；质量与体积不能直接互换。",
    },
    "blood_glucose": {
        "labels": {"mmol/L": "毫摩尔/升 mmol/L", "mg/dL": "毫克/分升 mg/dL"},
        "aliases": UNIT_ALIASES,
        "evidence": glucose_evidence,
        "subject_check": glucose_subject,
        "subject": "血糖（葡萄糖）单位换算",
        "scope": "只换算明确的血糖读数，不评价高低或推算用药。",
        "feedback": "未完成换算。请核对血糖数值；原单位和目标单位只可选mmol/L或mg/dL。",
    },
}


def conversion_spec(manifest):
    form = manifest.get("patient_form")
    if form == "unit_conversion/v1":
        policy_id = "mass_or_volume"
    elif form == "unit_conversion/v2":
        policy_id = manifest.get("patient_input_policy")
    else:
        raise ValueError("患者换算输入组件未注册")
    if policy_id not in POLICIES:
        raise ValueError("患者输入出处策略未注册")
    policy = POLICIES[policy_id]
    if policy_id == "blood_glucose" and not manifest.get("references"):
        raise ValueError("血糖公式必须声明来源")
    schema = manifest["input_schema"]
    fields = schema.get("properties", {})
    if (
        set(fields) != {"value", "from_unit", "to_unit"}
        or set(schema.get("required", [])) != set(fields)
        or any(fields[k].get("type") != "string" for k in fields)
        or any(
            set(fields[k].get("enum", [])) != set(policy["labels"])
            for k in ["from_unit", "to_unit"]
        )
    ):
        raise ValueError("患者输入组件或参数契约不兼容")
    return policy
