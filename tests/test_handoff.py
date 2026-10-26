"""Handoff v2 tests (design doc 6.1, P4-A)."""

import glob
import json
from pathlib import Path

from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.handoff import (
    LABELS,
    REGISTERED_FACT_KEYS,
    build_handoff,
    handoff_lines,
    handoff_text,
)

REPO = Path(__file__).resolve().parents[1]
CONTENT = REPO / "src" / "medagent" / "content"
PACKS = REPO / "src" / "medagent" / "extensions" / "packs"


def _fact(key, value, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t1",
        evidence="原话",
    )


def _sheet(
    *,
    directions_visible=True,
    department_visible=True,
    directions_mode=None,
    visit_notes=None,
):
    sheet = {
        "disposition": {
            "level": "routine_visit",
            "text": "可以在家先观察；请在 72 小时内到线下医疗机构就诊评估。",
            "window_hours": 72,
            "department_hint": "呼吸内科",
        },
        "directions": [
            {
                "hypothesis_id": "uri",
                "patient_label": "普通感冒",
                "group": "likely",
                "certainty": "可能",
                "support_quotes": ["患者原话引用"],
                "against_note": "你否认了发热，这一点不支持它",
            }
        ],
        "directions_visible": directions_visible,
        "department_visible": department_visible,
        "self_care": {
            "do": ["多休息"],
            "avoid": ["避免劳累"],
            "measure": ["留意体温变化"],
            "allergy_hint": "你档案里记有青霉素过敏，涉及用药请先核对",
        },
        "return_now_if": ["呼吸困难加重"],
    }
    # v1.3 additive fields: only present when the caller passes them,
    # so legacy sheets (no mode) keep the phase-A shape untouched.
    if directions_mode is not None:
        sheet["directions_mode"] = directions_mode
    if visit_notes is not None:
        sheet["visit_notes"] = visit_notes
    return sheet


def _state(*, sheet=None, baseline=None, follow_up=None):
    state = CaseState(session_id="s1")
    state.record(_fact("chief_complaint", "咳嗽三天"))
    state.record(_fact("sex", "女"))
    state.record(_fact("pregnancy_status", "否"))
    state.record(_fact("onset", None, Assertion.UNKNOWN))
    state.report_items = [
        {
            "name": "血糖",
            "reported_value": "6.1",
            "unit": "mmol/L",
            "reference_range": "",
            "status": "confirmed_by_patient",
            "record_title": "血糖自测",
            "measured_at": "2026-10-24",
            "source": "patient_record",
            "secret_extra": "must-not-leak",
        }
    ]
    if sheet is not None:
        state.conclusion = sheet
    if baseline is not None:
        state.profile_baseline = baseline
    if follow_up is not None:
        state.follow_up = follow_up
    return state


def _baseline():
    return {
        "display_name": "张三",
        "birth_year": 1980,
        "sex": "女",
        "chronic_conditions": ["高血压"],
        "allergies": [{"kind": "drug", "name": "青霉素", "extra": "drop"}],
        "medications": [{"name": "氨氯地平", "purpose": "降压"}],
        "surgical_history": "阑尾切除",
        "recent_measurements": {"blood_glucose": {"latest": "6.1"}},
        "truncated": False,
        "surprise_key": "must-not-leak",
    }


def test_v2_schema_and_v1_keys_preserved():
    handoff = build_handoff(_state())
    assert handoff["schema"] == "patient-handoff/v2"
    assert handoff["title"] == "就诊摘要（预问诊版）"
    # v1 keys keep their exact shapes: facts rows, needs_confirmation,
    # report_items (8 keys only), risk dict, prior_case.
    assert handoff["facts"][0]["label"] == "主要问题"
    assert handoff["facts"][0]["value"] == "咳嗽三天"
    assert [row["label"] for row in handoff["needs_confirmation"]] == ["开始时间"]
    assert set(handoff["report_items"][0]) == {
        "name",
        "reported_value",
        "unit",
        "reference_range",
        "status",
        "record_title",
        "measured_at",
        "source",
    }
    assert handoff["risk"]["level"] == "undetermined"
    assert handoff["prior_case"] is None


def test_conclusion_section_complete_when_visible():
    handoff = build_handoff(_state(sheet=_sheet()))
    conclusion = handoff["conclusion"]
    assert conclusion["disposition"]["text"].startswith("可以在家先观察")
    assert conclusion["disposition"]["window_hours"] == 72
    assert conclusion["disposition"]["department_hint"] == "呼吸内科"
    direction = conclusion["directions"][0]
    assert direction["patient_label"] == "普通感冒"
    assert direction["certainty"] == "可能"
    assert direction["against_note"] == "你否认了发热，这一点不支持它"
    # Patient verbatim evidence is dropped from the visit summary.
    assert "support_quotes" not in direction
    assert conclusion["self_care"]["do"] == ["多休息"]
    assert conclusion["self_care"]["avoid"] == ["避免劳累"]
    assert conclusion["self_care"]["measure"] == ["留意体温变化"]
    assert conclusion["self_care"]["allergy_hint"].startswith("你档案里记有")
    assert conclusion["return_now_if"] == ["呼吸困难加重"]


def test_directions_and_department_shadow_hidden():
    handoff = build_handoff(
        _state(sheet=_sheet(directions_visible=False, department_visible=False))
    )
    conclusion = handoff["conclusion"]
    assert conclusion["directions"] is None
    assert "department_hint" not in conclusion["disposition"]
    lines = handoff_lines(handoff)
    assert not any("普通感冒" in line for line in lines)
    assert not any("呼吸内科" in line for line in lines)
    # The disposition text itself is not shadow-gated.
    assert any("可以在家先观察" in line for line in lines)


def test_no_conclusion_gives_none_and_lines_skip_section():
    handoff = build_handoff(_state())
    assert handoff["conclusion"] is None
    assert handoff["baseline"] is None
    assert handoff["follow_up"] is None
    lines = handoff_lines(handoff)
    assert "【结论】" not in lines
    assert lines[0] == handoff["title"]
    assert lines[-1] == handoff["limitations"]


def test_baseline_whitelist_and_display():
    handoff = build_handoff(_state(baseline=_baseline()))
    baseline = handoff["baseline"]
    assert baseline["display_name"] == "张三"
    assert baseline["chronic_conditions"] == ["高血压"]
    assert baseline["allergies"] == [{"kind": "drug", "name": "青霉素"}]
    assert baseline["medications"] == [{"name": "氨氯地平", "purpose": "降压"}]
    assert baseline["surgical_history"] == "阑尾切除"
    assert "recent_measurements" not in baseline
    assert "surprise_key" not in baseline
    text = handoff_text(handoff)
    assert "慢病：高血压" in text
    assert "过敏：青霉素（drug）" in text
    assert "长期用药：氨氯地平（降压）" in text


def test_follow_up_from_stored_plan():
    plan = {
        "kind": "routine_checkin",
        "status": "scheduled",
        "due_in_hours": 72,
        "reason": "摘要已确认，例行回访确认恢复情况",
    }
    handoff = build_handoff(_state(follow_up=plan))
    assert handoff["follow_up"] == plan
    assert any("例行回访确认恢复情况" in line for line in handoff_lines(handoff))


def test_new_fact_keys_get_chinese_labels():
    state = CaseState(session_id="s1")
    state.record(_fact("sex", "女"))
    state.record(_fact("pregnancy_status", "否"))
    state.record(_fact("recent_exposures", "无"))
    state.record(_fact("reported_temperature", "37.8"))
    state.record(_fact("symptom_location", "头部"))
    labels = [row["label"] for row in build_handoff(state)["facts"]]
    assert labels == ["性别", "妊娠情况", "近期接触与暴露", "自述体温", "不适部位"]


def test_label_drift_guard_against_sources():
    questions = json.loads((CONTENT / "questions.json").read_text())
    registered = {q["fact_key"] for q in questions["questions"]}

    def _walk(node):
        if isinstance(node, dict):
            if "result_key" in node:
                registered.add(node["result_key"])
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    for path in glob.glob(str(PACKS / "*.json")):
        _walk(json.loads(Path(path).read_text()))
    assert registered == set(REGISTERED_FACT_KEYS)
    missing = sorted(key for key in registered if key not in LABELS)
    assert missing == []


def test_limitations_previsit_wording():
    handoff = build_handoff(_state())
    assert "未经医生审核" in handoff["limitations"]
    assert "不构成诊断" in handoff["limitations"]
    assert "仅供就诊时参考" in handoff["limitations"]


def test_handoff_lines_single_assembly_order():
    handoff = build_handoff(
        _state(
            sheet=_sheet(),
            baseline=_baseline(),
            follow_up={"kind": "routine_checkin", "due_in_hours": 72},
        )
    )
    lines = handoff_lines(handoff)
    assert lines[0] == "就诊摘要（预问诊版）"
    assert lines[-1] == handoff["limitations"]
    assert lines.index("【结论】") < lines.index("【健康档案基线】")
    assert lines.index("【健康档案基线】") < lines.index("【已记录】")
    assert any(line.startswith("随访：") for line in lines)
    assert any("出现以下情况请立即就医：呼吸困难加重" in line for line in lines)


def test_interpretation_note_visible_in_handoff_lines():
    """M1: an attached interpretation summary must reach the visible
    handoff text (and the print/export path that shares handoff_lines),
    not sit only on the raw state.report_items."""
    state = _state()
    state.report_items[0]["interpretation_note"] = (
        "血糖 6.1 mmol/L，无法判断；只作记录，不下判断"
    )
    handoff = build_handoff(state)
    assert (
        handoff["report_items"][0]["interpretation_note"]
        == "血糖 6.1 mmol/L，无法判断；只作记录，不下判断"
    )
    lines = handoff_lines(handoff)
    assert any(
        "解读：血糖 6.1 mmol/L，无法判断" in line for line in lines
    )
    # The whitelist still drops unrelated keys.
    assert "must-not-leak" not in handoff_text(handoff)


def test_no_interpretation_note_produces_no_empty_line():
    lines = handoff_lines(build_handoff(_state()))
    assert not any(line.strip().startswith("解读：") for line in lines)


# --- v1.3 (§13.4/§13.6) mode + passthrough tests (new; no existing
# gate assertion above is deleted or weakened) ---


def test_restricted_mode_passes_directions_despite_full_flag_false():
    # v1.3 composer shape: mode restricted, full flag false (only on
    # is true per §13.3). The restricted list must still pass through.
    handoff = build_handoff(
        _state(
            sheet=_sheet(
                directions_visible=False,
                department_visible=True,
                directions_mode="restricted",
            )
        )
    )
    conclusion = handoff["conclusion"]
    assert conclusion["directions_mode"] == "restricted"
    assert conclusion["directions"][0]["patient_label"] == "普通感冒"
    assert conclusion["disposition"]["department_hint"] == "呼吸内科"
    assert any("普通感冒" in line for line in handoff_lines(handoff))


def test_off_mode_never_leaks_directions():
    handoff = build_handoff(
        _state(
            sheet=_sheet(
                directions_visible=False,
                department_visible=True,
                directions_mode="off",
            )
        )
    )
    conclusion = handoff["conclusion"]
    assert conclusion["directions_mode"] == "off"
    assert conclusion["directions"] is None
    lines = handoff_lines(handoff)
    assert not any("普通感冒" in line for line in lines)
    # The department hint is gated by its own flag, not by the mode.
    assert any("呼吸内科" in line for line in lines)


def test_on_mode_passes_directions():
    handoff = build_handoff(
        _state(
            sheet=_sheet(
                directions_visible=True,
                department_visible=True,
                directions_mode="on",
            )
        )
    )
    conclusion = handoff["conclusion"]
    assert conclusion["directions_mode"] == "on"
    assert conclusion["directions"][0]["patient_label"] == "普通感冒"
    assert any("普通感冒" in line for line in handoff_lines(handoff))


def test_visit_notes_passthrough_and_lines_keep_quotes():
    # §13.6 exception: support_quotes are dropped from directions, but
    # the visit-notes action list keeps its evidence-verb entries.
    notes = ["咳嗽三天", "患者原话引用"]
    handoff = build_handoff(
        _state(
            sheet=_sheet(
                directions_visible=False,
                directions_mode="restricted",
                visit_notes=notes,
            )
        )
    )
    conclusion = handoff["conclusion"]
    assert conclusion["visit_notes"] == notes
    assert "support_quotes" not in conclusion["directions"][0]
    assert "hypothesis_id" not in conclusion["directions"][0]
    lines = handoff_lines(handoff)
    assert any("就诊带话：咳嗽三天" in line for line in lines)
    assert any("就诊带话：患者原话引用" in line for line in lines)


def test_handoff_lines_mode_gate_does_not_trust_list_presence():
    # A hand-built dict with a leaked list + off mode must still hide
    # directions; a leaked hint + false department flag must hide too.
    handoff = {
        "title": "就诊摘要（预问诊版）",
        "limitations": "仅供就诊时参考",
        "conclusion": {
            "disposition": {
                "text": "可以在家先观察",
                "department_hint": "呼吸内科",
            },
            "directions_mode": "off",
            "directions_visible": False,
            "department_visible": False,
            "directions": [{"patient_label": "普通感冒"}],
            "self_care": {},
            "return_now_if": [],
        },
        "facts": [],
        "needs_confirmation": [],
        "report_items": [],
    }
    lines = handoff_lines(handoff)
    assert not any("普通感冒" in line for line in lines)
    assert not any("呼吸内科" in line for line in lines)
    # Legacy dicts without a mode fall back to the flag (old behaviour).
    handoff["conclusion"]["directions_visible"] = True
    del handoff["conclusion"]["directions_mode"]
    handoff["conclusion"]["department_visible"] = True
    lines = handoff_lines(handoff)
    assert any("普通感冒" in line for line in lines)
    assert any("呼吸内科" in line for line in lines)
