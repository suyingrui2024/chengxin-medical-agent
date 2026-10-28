"""Registered scale form families; manifests cannot supply executable policy.

Mirror of ``conversion_forms.py`` for multi-item scales. The job here is
narrow on purpose: prove that a pack's declared ``input_schema`` and its
``scale`` items describe the same form, and hand the question layer the
few constants it needs to build the input card.

The score itself is computed by ``scale_engine.run_scale``; this module
never touches arithmetic.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from .scale_engine import (
    ENGINE_ID,
    ScaleSpecError,
    expected_properties,
    validate_scale,
)

SCALE_FORM = "scale_form/v1"
SCALE_RENDERER = "scale/v1"

AUDIENCES = {"patient", "clinician", "both"}

# Intent words that make a scale request explicit. Deliberately about
# the act of filling a scale, not about any disease: the model proposes,
# the patient still has to open and submit the form.
INTENTS = (
    "量表",
    "评分",
    "打分",
    "自评",
    "评估",
    "测一下",
    "测一测",
    "算一下",
    "帮我算",
    "做个",
    "帮我做",
    "做一下",
    "怎么算",
    "如何算",
)

CANCELS = ("不算了", "不用算", "先不算", "取消", "跳过", "不做了")

# Every scale result uses this exact output contract. Packs restate it
# verbatim (the registry validates results against the manifest), and
# this constant is what ``scale_form_spec`` compares them to — so a pack
# cannot quietly widen what the engine is allowed to emit.
OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "value",
        "unit",
        "max_score",
        "band",
        "items",
        "formula",
        "method",
        "scoring_note",
        "disclaimer",
        "limitations",
        "result_renderer",
    ],
    "properties": {
        "value": {"type": "string", "title": "得分"},
        "unit": {"type": "string", "title": "单位"},
        "max_score": {"type": "number", "title": "满分"},
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
        "items": {
            "type": "array",
            "maxItems": 60,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "label", "answer_label", "score"],
                "properties": {
                    "id": {"type": "string"},
                    "label": {"type": "string"},
                    "answer_label": {"type": "string"},
                    "score": {"type": "number"},
                },
            },
        },
        "formula": {"type": "string", "title": "计算式"},
        "method": {"type": "string", "title": "计分方式"},
        "scoring_note": {"type": "string"},
        "disclaimer": {"type": "string"},
        "limitations": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string"},
        },
        "result_renderer": {"type": "string", "enum": [SCALE_RENDERER]},
    },
}


def _validate_identity_fields(manifest: Mapping[str, Any]) -> None:
    if manifest.get("patient_form") not in (None, SCALE_FORM):
        raise ScaleSpecError("量表内容包的患者表单类型未注册")
    if manifest.get("result_renderer") != SCALE_RENDERER:
        raise ScaleSpecError("量表内容包必须声明 scale/v1 结果渲染器")
    if manifest.get("audience") not in AUDIENCES:
        raise ScaleSpecError("量表内容包必须声明受众（patient/clinician/both）")
    aliases = manifest.get("aliases")
    if not isinstance(aliases, list) or not 1 <= len(aliases) <= 12:
        raise ScaleSpecError("量表必须声明 1..12 个触发别名")
    for alias in aliases:
        if not isinstance(alias, str) or not 1 <= len(alias) <= 40:
            raise ScaleSpecError("量表别名无效")


def scale_form_spec(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one scale pack and return the form policy.

    Raises ``ScaleSpecError`` for anything that would let the form, the
    contract and the engine disagree.
    """
    _validate_identity_fields(manifest)
    if manifest.get("kind") != "calculation" or manifest.get("ui") != "calculation":
        raise ScaleSpecError("量表必须注册为 calculation 能力")
    if not manifest.get("references"):
        raise ScaleSpecError("量表必须声明来源")
    if not manifest.get("validation"):
        raise ScaleSpecError("量表必须声明验证范围")

    spec = validate_scale(manifest)

    schema = manifest.get("input_schema")
    if not isinstance(schema, dict):
        raise ScaleSpecError("量表缺少输入契约")
    properties = schema.get("properties") or {}
    if set(properties) != set(expected_properties(spec)):
        raise ScaleSpecError("输入契约与量表条目不一致")
    if set(schema.get("required", [])):
        # Scales are answered item by item; requiring all of them up
        # front would let the model pre-fill scores. The engine enforces
        # completeness at submit time instead.
        raise ScaleSpecError("量表输入契约不得声明必填项")
    for key, expected in expected_properties(spec).items():
        declared = properties[key]
        if declared.get("type") != "number":
            raise ScaleSpecError(f"输入契约字段 {key} 必须是数值")
        if declared.get("title") != expected["title"]:
            raise ScaleSpecError(f"输入契约字段 {key} 的标题与条目不一致")
        if "enum" in expected and set(declared.get("enum", [])) != set(expected["enum"]):
            raise ScaleSpecError(f"输入契约字段 {key} 的选项与条目不一致")

    if manifest.get("output_schema") != OUTPUT_SCHEMA:
        raise ScaleSpecError("量表输出契约必须与引擎结果结构一致")

    audience = manifest["audience"]
    if audience == "clinician" and manifest.get("patient_form"):
        raise ScaleSpecError("仅医生使用的量表不得声明患者表单")
    if audience in {"patient", "both"} and manifest.get("patient_form") != SCALE_FORM:
        raise ScaleSpecError("患者可见量表必须声明 scale_form/v1")
    if audience in {"patient", "both"}:
        # The patient card renders one radio group per item. A threshold
        # table expects a measured number the patient would have to
        # type, which v1 does not offer — so a patient-visible scale may
        # only use choice items. NEWS2-style vitals stay clinician-only.
        if any(item["kind"] != "choice" for item in spec["items"]):
            raise ScaleSpecError("患者可见量表只能使用选项型条目")

    return {
        "form": SCALE_FORM,
        "renderer": SCALE_RENDERER,
        "audience": audience,
        "items": spec["items"],
        "bands": spec["bands"],
        "min": spec["min"],
        "max": spec["max"],
        "mode": spec["mode"],
        "unit": spec["unit"],
        "aliases": list(manifest["aliases"]),
        "intents": INTENTS,
        "cancels": CANCELS,
        "scope": (
            "量表计分只反映本次作答，不构成诊断，也不替代医生判断。"
        ),
        "feedback": "未完成计算。请把每一条都选完再提交；缺项不会给出部分得分。",
    }


def is_scale_manifest(manifest: Mapping[str, Any]) -> bool:
    return (
        manifest.get("patient_form") == SCALE_FORM
        or manifest.get("result_renderer") == SCALE_RENDERER
        or manifest.get("scale", {}).get("engine") == ENGINE_ID
    )
