"""Patient-controlled, non-diagnostic handoff projection."""

from __future__ import annotations

from typing import Any


LABELS = {
    "consult_for": "咨询对象",
    "age_years": "年龄",
    "chief_complaint": "主要问题",
    "onset": "开始时间",
    "trajectory": "变化趋势",
    "severity": "影响程度",
    "associated_symptoms": "伴随不适",
    "medications_taken": "已报告用药与处理",
    "allergies": "过敏信息",
    "relevant_history": "相关病史",
    "temperature_c": "报告的腋温",
    "resp_breathing": "呼吸情况",
    "resp_cough_details": "咳嗽细节",
    "resp_intake": "进食饮水与排尿",
    "gi_details": "腹部细节",
    "head_details": "头痛细节",
    "dizzy_details": "头晕细节",
    "back_details": "腰背细节",
    "urinary_details": "排尿细节",
    "skin_details": "皮疹细节",
    "emergency_screen": "急症筛查",
    "patient_goal": "本次咨询目标",
}


def build_handoff(state) -> dict[str, Any]:
    facts = []
    missing = []
    for key, fact in state.facts.items():
        row = {
            "label": LABELS.get(key, key),
            "assertion": fact.assertion.value,
            "value": fact.value,
            "evidence": fact.evidence,
            "source": fact.source.value,
            "quality": fact.quality.value,
        }
        (missing if fact.assertion.value == "unknown" else facts).append(row)
    return {
        "schema": "patient-handoff/v1",
        "title": "就诊交接摘要（患者核对版）",
        "limitations": "这是患者提供信息的整理，不构成诊断、治疗建议或临床检验复核。",
        "risk": state.risk.to_dict(),
        "prior_case": (state.runtime_snapshot or {}).get("prior_case"),
        "facts": facts,
        "needs_confirmation": missing,
        "report_items": [
            {
                key: item.get(key)
                for key in (
                    "name",
                    "reported_value",
                    "unit",
                    "reference_range",
                    "status",
                    "record_title",
                    "measured_at",
                    "source",
                )
            }
            for item in state.report_items
        ],
    }
