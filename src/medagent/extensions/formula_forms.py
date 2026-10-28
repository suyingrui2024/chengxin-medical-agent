"""Registered formula form families; manifests cannot supply executable policy.

Mirror of ``scale_forms.py`` for named formulas. It proves that a pack's
``input_schema`` and its ``formula`` block describe the same form, and
hands the question layer the constants it needs to build the input card.
The arithmetic lives in ``formulas.py``; this module never computes.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .formula_engine import (
    ENGINE_ID,
    FormulaSpecError,
    expected_properties,
    validate_formula,
)

FORMULA_FORM = "formula_form/v1"
FORMULA_RENDERER = "formula/v1"

AUDIENCES = {"patient", "clinician", "both"}

INTENTS = (
    "算一下",
    "计算",
    "帮我算",
    "换算",
    "多少",
    "评估",
    # Kept in step with the scale form: patients say 「帮我做个 BMI」 far
    # more often than 「计算 BMI」, and a narrower list here silently made
    # 21 of the 59 patient-facing capabilities unreachable.
    "做个",
    "帮我做",
    "做一下",
    "测一下",
    "测一测",
    "怎么算",
    "如何算",
)
CANCELS = ("不算了", "不用算", "先不算", "取消", "跳过")

OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "value",
        "unit",
        "band",
        "formula",
        "method",
        "scoring_note",
        "disclaimer",
        "limitations",
        "result_renderer",
    ],
    "properties": {
        "value": {"type": "string", "title": "结果"},
        "unit": {"type": "string", "title": "单位"},
        "band": {
            "type": "object",
            "additionalProperties": False,
            "required": [],
            "properties": {
                "key": {"type": "string"},
                "label": {"type": "string"},
                "range": {"type": "string"},
            },
        },
        "formula": {"type": "string", "title": "计算式"},
        "method": {"type": "string", "title": "计算方法"},
        "scoring_note": {"type": "string"},
        "disclaimer": {"type": "string"},
        "limitations": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "result_renderer": {"type": "string", "enum": [FORMULA_RENDERER]},
    },
}


def formula_form_spec(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one formula pack and return the form policy."""
    if manifest.get("kind") != "calculation" or manifest.get("ui") != "calculation":
        raise FormulaSpecError("公式必须注册为 calculation 能力")
    if manifest.get("result_renderer") != FORMULA_RENDERER:
        raise FormulaSpecError("公式内容包必须声明 formula/v1 结果渲染器")
    audience = manifest.get("audience")
    if audience not in AUDIENCES:
        raise FormulaSpecError("公式内容包必须声明受众")
    if audience == "clinician" and manifest.get("patient_form"):
        raise FormulaSpecError("仅医生使用的公式不得声明患者表单")
    if audience in {"patient", "both"} and manifest.get("patient_form") != FORMULA_FORM:
        raise FormulaSpecError("患者可见公式必须声明 formula_form/v1")
    if not manifest.get("references"):
        raise FormulaSpecError("公式必须声明来源")
    if not manifest.get("validation"):
        raise FormulaSpecError("公式必须声明验证范围")

    spec = validate_formula(manifest)

    schema = manifest.get("input_schema")
    if not isinstance(schema, dict):
        raise FormulaSpecError("公式缺少输入契约")
    properties = schema.get("properties") or {}
    expected = expected_properties(spec)
    if set(properties) != set(expected):
        raise FormulaSpecError("输入契约与公式输入不一致")
    if set(schema.get("required", [])):
        # Inputs are filled by the patient/clinician, never pre-filled
        # by the model; completeness is enforced at submit time.
        raise FormulaSpecError("公式输入契约不得声明必填项")
    for key, signature in expected.items():
        declared = properties[key]
        if declared.get("type") != "string":
            raise FormulaSpecError(f"输入契约字段 {key} 必须是字符串")
        if declared.get("title") != signature["title"]:
            raise FormulaSpecError(f"输入契约字段 {key} 的标题与签名不一致")
        if "enum" in signature and set(declared.get("enum", [])) != set(
            signature["enum"]
        ):
            raise FormulaSpecError(f"输入契约字段 {key} 的选项与签名不一致")

    if manifest.get("output_schema") != OUTPUT_SCHEMA:
        raise FormulaSpecError("公式输出契约必须与引擎结果结构一致")

    return {
        "form": FORMULA_FORM,
        "renderer": FORMULA_RENDERER,
        "audience": audience,
        "inputs": [dict(item) for item in spec["inputs"]],
        "output": dict(spec["output"]),
        "bands": spec["bands"],
        "aliases": list(manifest.get("aliases") or []),
        "intents": INTENTS,
        "cancels": CANCELS,
        "scope": "只做公式计算，不评价结果高低，也不提示处理方式。",
        "feedback": "未完成计算。请检查每一项数值与单位；超出范围的值不会被计算。",
    }


def is_formula_manifest(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("patient_form") == FORMULA_FORM
        or manifest.get("result_renderer") == FORMULA_RENDERER
        or manifest.get("formula", {}).get("engine") == ENGINE_ID
    )
