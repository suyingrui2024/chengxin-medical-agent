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
- ``directions`` appear only when the sheet's mode allows them
  (design §13.6: ``directions_mode`` ``restricted`` or ``on``; a legacy
  sheet without a mode falls back to ``directions_visible``);
  ``department_hint`` only when the sheet says ``department_visible``.
  Shadow/off content never leaks through the handoff.
- ``directions_mode`` and ``visit_notes`` pass through the whitelist
  verbatim (design §13.4); the visit-notes action list keeps its
  evidence-verb quotes even though ``support_quotes`` are still dropped
  from directions themselves.
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
    # P3 off-pathway symptom-domain warning screen (out_of_pathway.py).
    # Labels only: these keys are not in REGISTERED_FACT_KEYS because
    # that registry is derived from questions.json + capability packs,
    # and the P3 contract keeps warning content out of both. They still
    # reach the handoff whenever an off-pathway consultation records
    # them, and an unlabelled key would render as its raw id.
    "warning_意识": "危险信号：意识",
    "warning_呼吸": "危险信号：呼吸",
    "warning_出血": "危险信号：出血",
    "warning_剧痛": "危险信号：剧痛",
    "warning_持续加重": "危险信号：持续加重",
    "warning_功能骤降": "危险信号：功能骤降",
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

    Directions are gated on the sheet's own ``directions_mode``
    (design §13.6: ``restricted`` or ``on`` passes the list, ``off``
    yields ``None``) and the department hint on ``department_visible``.
    A legacy sheet without ``directions_mode`` falls back to the
    phase-A ``directions_visible`` flag, so the old shape keeps its
    old behaviour; a present-but-unknown mode is treated as not
    allowed (fail-closed). Support quotes (patient verbatim evidence)
    are dropped from directions -- the visit summary does not need
    them and they add nothing a doctor can act on -- except inside
    ``visit_notes``: that list IS the action checklist for the doctor,
    so its evidence-verb entries are kept (design §13.6).

    Shape contract (P2-2): the ``directions`` key is always present in
    this section; under shadow/off it is ``None``, never a leaked list
    and never dropped. Consumers must judge by mode/flag (``handoff_lines``
    gates on ``directions_mode`` with a legacy ``directions_visible``
    fallback, and the print module gates on the mode plus a
    null-tolerant list coercion), never by key presence. Removing the
    key would break the fixed v2 shape and the shadow test that pins
    ``directions is None``; the ``None`` value itself is the gate
    working, not a leak. No hypothesis id is carried: directions keep
    only their patient-facing fields.

    Single-source contract (modular-refactor design §4.4): this
    endpoint's job is transmission trimming ONLY -- it carries the
    direction/department content only when the sheet's mode/flag says
    so, AND it passes the mode and both flag fields themselves through
    untouched, so downstream renderers judge by the mode/flag, never by
    "field present therefore visible". The trimming is a transport
    optimization, not a second gate: a renderer that receives a hint
    with a missing or false flag must still hide it (fail-closed), and
    ``app.js`` must not re-derive the flag from the live sheet or fall
    back to "directions present".
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
        # Flags pass through verbatim (bool-coerced; a missing flag
        # becomes False, i.e. invisible) so renderers consume the
        # composer's single source instead of re-deriving visibility.
        "directions_visible": bool(sheet.get("directions_visible")),
        "department_visible": bool(sheet.get("department_visible")),
        "self_care": {},
        "return_now_if": list(sheet.get("return_now_if") or []),
    }
    # v1.3 (§13.4/§13.6): the mode flag passes through verbatim, and
    # the visit-notes action list passes through as strings only.
    # Both are optional additive fields: absent on a legacy sheet means
    # no mode key / no visit-notes section (old behaviour), never an
    # invented value.
    mode = sheet.get("directions_mode")
    if isinstance(mode, str) and mode:
        section["directions_mode"] = mode
        directions_allowed = mode in ("restricted", "on")
    else:
        # Legacy sheet without a mode: the full-visibility flag is the
        # gate (phase-A shape). A present-but-unknown mode stays not
        # allowed via the branch above (fail-closed).
        directions_allowed = bool(sheet.get("directions_visible"))
    if isinstance(sheet.get("visit_notes"), list):
        section["visit_notes"] = [
            item for item in sheet["visit_notes"] if isinstance(item, str)
        ]
    if sheet.get("department_visible") and disposition.get("department_hint"):
        section["disposition"]["department_hint"] = disposition["department_hint"]
    if directions_allowed:
        directions = []
        for item in sheet.get("directions") or []:
            if not isinstance(item, dict) or not item.get("patient_label"):
                continue
            directions.append(
                {
                    key: item[key]
                    for key in (
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

    Visibility is consumed, never recomputed and never inferred from
    list presence (design §13.6): directions render only when the
    handoff's ``directions_mode`` allows them (``restricted``/``on``),
    falling back to ``directions_visible`` on legacy dicts without a
    mode, and to list presence only on dicts with neither; the
    department line likewise requires ``department_visible is True``
    whenever that flag is present. The visit-notes list is read
    as-is (it is already the sheet's list, §13.4) and rendered only
    when non-empty.
    """
    lines = [handoff["title"]]
    conclusion = handoff.get("conclusion") or {}
    if conclusion:
        lines.append("【结论】")
        disposition = conclusion.get("disposition") or {}
        if disposition.get("text"):
            lines.append(disposition["text"])
        dept_flag = conclusion.get("department_visible")
        dept_shown = (
            dept_flag is True if "department_visible" in conclusion else True
        )
        if disposition.get("department_hint") and dept_shown:
            lines.append(
                f"建议科室：{disposition['department_hint']}（仅供参考，以医生判断为准）"
            )
        mode = conclusion.get("directions_mode")
        if isinstance(mode, str) and mode:
            directions_shown = mode in ("restricted", "on")
        elif "directions_visible" in conclusion:
            directions_shown = conclusion.get("directions_visible") is True
        else:
            directions_shown = True
        for direction in (conclusion.get("directions") or []) if directions_shown else []:
            label = direction.get("patient_label") or ""
            certainty = direction.get("certainty") or ""
            lines.append(
                f"可能方向：{label}" + (f"（{certainty}）" if certainty else "")
            )
            if direction.get("against_note"):
                lines.append(f"  {direction['against_note']}")
        for item in conclusion.get("visit_notes") or []:
            lines.append(f"就诊带话：{item}")
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
