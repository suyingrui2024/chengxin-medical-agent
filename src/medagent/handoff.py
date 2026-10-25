"""Patient-controlled, non-diagnostic handoff projection.

Schema ``patient-handoff/v2`` (design doc 6.1, P4-A).

v1 -> v2 changes (v1 callers keep working: the entry point is still
:func:`build_handoff`, and every v1 key keeps its exact shape):

- ``schema`` is now ``patient-handoff/v2``; ``title`` and ``limitations``
  moved to pre-visit wording: this is an AI-organized summary for a
  doctor visit, not a reviewed clinical document.
- New sections, all read-only projections of existing state:
  ``conclusion`` (the conclusion sheet's core, read never recomputed),
  ``baseline`` (the P2 ``profile_baseline`` projection, whitelisted
  again here so no new field can leak through), and ``follow_up``
  (the stored follow-up plan set at close).
- ``directions`` appear only when the sheet itself says
  ``directions_visible``; ``department_hint`` only when the sheet says
  ``department_visible``. Shadow content never leaks through the handoff.
- ``handoff_lines`` is the single text assembly shared by the print
  page and the .md/.txt exports: do not write a second copy of this
  wording anywhere else (the frontend dialog renders the same sections
  from the same dict, it does not re-derive text).

Privacy discipline, unchanged from v1: this projection is
patient-controlled and non-diagnostic. It never invents a diagnosis,
never writes back into the case, and never adds a field that is not
already on the case or the sheet.
"""

from __future__ import annotations

import json
from typing import Any


LABELS = {
    "consult_for": "咨询对象",
    "sex": "性别",
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
    "reported_temperature": "自述体温",
    "pregnancy_status": "妊娠情况",
    "recent_exposures": "近期接触与暴露",
    "symptom_location": "不适部位",
    "resp_breathing": "呼吸情况",
    "resp_cough_details": "咳嗽细节",
    "resp_intake": "进食饮水与排尿",
    "gi_details": "腹部细节",
    "head_details": "头痛细节",
    "dizzy_details": "头晕细节",
    "back_details": "腰背细节",
    "urinary_details": "排尿细节",
    "skin_details": "皮疹细节",
    "infant_observable_signs": "婴幼儿可观察表现",
    "eye_warning_signs": "眼部警示表现",
    "emergency_screen": "急症筛查",
    "patient_goal": "本次咨询目标",
}

# The fact-key registry the drift-guard test checks LABELS against:
# every fact_key in content/questions.json plus every self-check
# task.result_key in extensions/packs/*.json. A key that can land in
# state.facts but has no patient-facing label fails that test.
REGISTERED_FACT_KEYS = frozenset(
    {
        "consult_for",
        "sex",
        "age_years",
        "chief_complaint",
        "emergency_screen",
        "onset",
        "trajectory",
        "severity",
        "associated_symptoms",
        "gi_details",
        "head_details",
        "dizzy_details",
        "back_details",
        "urinary_details",
        "pregnancy_status",
        "skin_details",
        "infant_observable_signs",
        "eye_warning_signs",
        "recent_exposures",
        "resp_breathing",
        "resp_cough_details",
        "resp_intake",
        "reported_temperature",
        "medications_taken",
        "allergies",
        "relevant_history",
        "patient_goal",
        "temperature_c",
        "symptom_location",
    }
)

LIMITATIONS = (
    "本摘要由 AI 根据本次问诊整理，未经医生审核，不构成诊断、治疗建议或临床检验复核，"
    "仅供就诊时参考；请以医生当面评估为准。"
)

# Baseline keys copied from the P2 profile_baseline projection, in
# display order. Anything else on the projection (e.g. recent
# measurements, truncation flags) is deliberately not carried over:
# the visit summary is about history and medication, not raw readings.
_BASELINE_KEYS = (
    "display_name",
    "birth_year",
    "birth_year_month",
    "sex",
    "chronic_conditions",
    "allergies",
    "medications",
    "surgical_history",
)


def _fact_row(key: str, fact: Any) -> dict[str, Any]:
    return {
        "label": LABELS.get(key, key),
        "assertion": fact.assertion.value,
        "value": fact.value,
        "evidence": fact.evidence,
        "source": fact.source.value,
        "quality": fact.quality.value,
    }


def _report_item_row(item: dict[str, Any]) -> dict[str, Any]:
    # The 8-key v1 shape is preserved for note-less rows; the
    # interpretation note is added only when present, so downstream
    # shape assertions and consumers see no null field.
    row = {
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
    if item.get("interpretation_note"):
        row["interpretation_note"] = item["interpretation_note"]
    return row


def _conclusion_section(state: Any) -> dict[str, Any] | None:
    """Core of the conclusion sheet, read-only. None when no sheet exists.

    Directions are gated on the sheet's own ``directions_visible`` flag
    and the department hint on ``department_visible``; support quotes
    (patient verbatim evidence) are dropped -- the visit summary does
    not need them and they add nothing a doctor can act on.

    Shape contract (P2-2): the ``directions`` key is always present in
    this section; under shadow it is ``None``, never a leaked list and
    never dropped. Consumers must judge by value (``is None`` /
    falsiness -- ``handoff_lines`` uses ``.get("directions") or []`` and
    the print module gates on ``directions_visible`` plus a null-tolerant
    list coercion), never by key presence. Removing the key would break
    the fixed v2 shape and the shadow test that pins ``directions is
    None``; the ``None`` value itself is the gate working, not a leak.
    """
    sheet = getattr(state, "conclusion", None) or {}
    if not isinstance(sheet, dict) or not sheet:
        return None
    disposition = sheet.get("disposition") or {}
    section: dict[str, Any] = {
        "disposition": {
            key: disposition[key]
            for key in ("level", "text", "window_hours")
            if disposition.get(key) is not None
        },
        "directions": None,
        "self_care": {},
        "return_now_if": list(sheet.get("return_now_if") or []),
    }
    if sheet.get("department_visible") and disposition.get("department_hint"):
        section["disposition"]["department_hint"] = disposition["department_hint"]
    if sheet.get("directions_visible"):
        directions = []
        for item in sheet.get("directions") or []:
            if not isinstance(item, dict) or not item.get("patient_label"):
                continue
            directions.append(
                {
                    key: item[key]
                    for key in (
                        "hypothesis_id",
                        "patient_label",
                        "group",
                        "certainty",
                        "against_note",
                    )
                    if item.get(key) is not None
                }
            )
        section["directions"] = directions or None
    care = sheet.get("self_care") or {}
    section["self_care"] = {
        key: list(care[key])
        for key in ("do", "avoid", "measure")
        if care.get(key)
    }
    if care.get("allergy_hint"):
        section["self_care"]["allergy_hint"] = care["allergy_hint"]
    return section


def _baseline_section(state: Any) -> dict[str, Any] | None:
    """Bound member's baseline, re-whitelisted from the P2 projection.

    Reads ``state.profile_baseline`` only -- no new field is invented
    here, and the projection itself is never mutated. Allergy and
    medication entries keep just their display fields.
    """
    projection = getattr(state, "profile_baseline", None) or {}
    if not isinstance(projection, dict) or not projection:
        return None
    section: dict[str, Any] = {}
    for key in _BASELINE_KEYS:
        value = projection.get(key)
        if value is None or value == [] or value == "":
            continue
        if key == "allergies":
            value = [
                {k: entry[k] for k in ("kind", "name") if entry.get(k)}
                for entry in value
                if isinstance(entry, dict)
            ]
            if not value:
                continue
        if key == "medications":
            value = [
                {k: entry[k] for k in ("name", "purpose") if entry.get(k)}
                for entry in value
                if isinstance(entry, dict)
            ]
            if not value:
                continue
        section[key] = value
    return section or None


def build_handoff(state) -> dict[str, Any]:
    facts = []
    missing = []
    for key, fact in state.facts.items():
        row = _fact_row(key, fact)
        (missing if fact.assertion.value == "unknown" else facts).append(row)
    # The stored plan (set at close) is the follow-up source. The sheet
    # carries only a {kind, due_in_hours} summary and is not a substitute.
    follow_up = getattr(state, "follow_up", None) or None
    return {
        "schema": "patient-handoff/v2",
        "title": "就诊摘要（预问诊版）",
        "limitations": LIMITATIONS,
        "risk": state.risk.to_dict(),
        "prior_case": (state.runtime_snapshot or {}).get("prior_case"),
        "conclusion": _conclusion_section(state),
        "baseline": _baseline_section(state),
        "follow_up": dict(follow_up) if follow_up else None,
        "facts": facts,
        "needs_confirmation": missing,
        "report_items": [_report_item_row(item) for item in state.report_items],
    }


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _named_list(entries: Any) -> str:
    parts = []
    for entry in entries or []:
        if isinstance(entry, dict):
            name = entry.get("name") or ""
            extra = entry.get("kind") or entry.get("purpose") or ""
            parts.append(f"{name}（{extra}）" if extra else name)
        else:
            parts.append(str(entry))
    return "、".join(part for part in parts if part)


def handoff_lines(handoff: dict[str, Any]) -> list[str]:
    """Single text assembly for print and .md/.txt export.

    Pure: reads only the handoff dict, never touches state. Empty
    sections are omitted entirely (no dangling headers), so a sheetless
    or baseline-less handoff still reads cleanly. This is the one place
    the summary wording lives; exporters must call it, not re-derive.
    """
    lines = [handoff["title"]]
    conclusion = handoff.get("conclusion") or {}
    if conclusion:
        lines.append("【结论】")
        disposition = conclusion.get("disposition") or {}
        if disposition.get("text"):
            lines.append(disposition["text"])
        if disposition.get("department_hint"):
            lines.append(
                f"建议科室：{disposition['department_hint']}（仅供参考，以医生判断为准）"
            )
        for direction in conclusion.get("directions") or []:
            label = direction.get("patient_label") or ""
            certainty = direction.get("certainty") or ""
            lines.append(
                f"可能方向：{label}" + (f"（{certainty}）" if certainty else "")
            )
            if direction.get("against_note"):
                lines.append(f"  {direction['against_note']}")
        care = conclusion.get("self_care") or {}
        for key, heading in (
            ("do", "建议这样做"),
            ("avoid", "请避免"),
            ("measure", "可留意观察"),
        ):
            for item in care.get(key) or []:
                lines.append(f"{heading}：{item}")
        if care.get("allergy_hint"):
            lines.append(care["allergy_hint"])
        for item in conclusion.get("return_now_if") or []:
            lines.append(f"出现以下情况请立即就医：{item}")
    baseline = handoff.get("baseline") or {}
    if baseline:
        lines.append("【健康档案基线】")
        if baseline.get("display_name"):
            lines.append(f"姓名：{baseline['display_name']}")
        if baseline.get("birth_year"):
            lines.append(f"出生年：{baseline['birth_year']}")
        if baseline.get("birth_year_month"):
            lines.append(f"出生年月：{baseline['birth_year_month']}")
        if baseline.get("sex"):
            lines.append(f"性别：{baseline['sex']}")
        if baseline.get("chronic_conditions"):
            lines.append(f"慢病：{'、'.join(baseline['chronic_conditions'])}")
        if baseline.get("allergies"):
            lines.append(f"过敏：{_named_list(baseline['allergies'])}")
        if baseline.get("medications"):
            lines.append(f"长期用药：{_named_list(baseline['medications'])}")
        if baseline.get("surgical_history"):
            lines.append(f"手术/住院史：{_value_text(baseline['surgical_history'])}")
    follow_up = handoff.get("follow_up") or {}
    if follow_up:
        reason = follow_up.get("reason") or ""
        due = follow_up.get("due_in_hours")
        if reason:
            lines.append(f"随访：{reason}")
        elif due is not None:
            lines.append(f"随访：{due} 小时后复核")
    facts = handoff.get("facts") or []
    if facts:
        lines.append("【已记录】")
        for item in facts:
            lines.append(f"{item['label']}：{_value_text(item.get('value'))}")
    report_items = handoff.get("report_items") or []
    if report_items:
        lines.append("本次问诊使用的数据：")
        for item in report_items:
            value = _value_text(item.get("reported_value"))
            unit = item.get("unit") or ""
            lines.append(f"- {item.get('name') or ''} {value}{unit}".strip())
            if item.get("interpretation_note"):
                lines.append(f"  解读：{item['interpretation_note']}")
    needs = handoff.get("needs_confirmation") or []
    if needs:
        lines.append(f"仍待补充：{'、'.join(item['label'] for item in needs)}")
    lines.append(handoff["limitations"])
    return lines


def handoff_text(handoff: dict[str, Any]) -> str:
    """Rendered text form of :func:`handoff_lines` (export entry point)."""
    return "\n".join(handoff_lines(handoff))
