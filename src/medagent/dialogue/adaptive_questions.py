"""Validation and answer projection for model-authored patient questions.

The model may choose the wording and number of options.  The host owns the
wire format, bounds, plain-text rendering, stale-action checks, and urgent
signals attached to a selected answer.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable


SELECTION_MODES = {"single", "multiple", "text"}
MAX_OPTIONS = 8

# These tags describe what the patient selected. They are never accepted from
# hidden model reasoning, and each tag must be supported by visible option text.
RISK_TAG_CUES = {
    "unresponsive": ("叫不醒", "失去意识", "意识不清", "昏迷"),
    "seizure": ("抽搐",),
    "cyanosis": ("嘴唇发紫", "口唇发紫", "皮肤发紫"),
    "severe_breathing": (
        "严重呼吸困难",
        "喘不上气",
        "不能呼吸",
        "说不完整句话",
    ),
    "chest_discomfort": ("胸痛", "胸口", "胸部", "胸闷"),
    "major_bleeding": ("大出血", "血止不住", "大量呕血"),
    "cold_sweat": ("冷汗", "大汗"),
    "nausea_or_dizziness": ("恶心", "呕吐", "头晕", "晕厥", "昏倒"),
    "exertional": ("走路", "爬楼", "活动", "运动", "劳累", "紧张"),
    "jaw_or_upper_body": ("下颌", "肩", "手臂", "胳膊", "颈", "背部"),
}

DIRECT_EMERGENCY_TAGS = {
    "unresponsive",
    "seizure",
    "cyanosis",
    "severe_breathing",
    "chest_discomfort",
    "major_bleeding",
}

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,39}")
_PROHIBITED_PATIENT_ACTIONS = (
    "自行停药",
    "自行加量",
    "自行减量",
    "立即服用",
    "建议服用",
    "可以排除",
    "已经确诊",
)
_NEGATION_CUES = ("没有", "无", "否认", "不伴", "未出现", "不会", "不是")


class AdaptiveQuestionError(ValueError):
    pass


def _bounded_text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise AdaptiveQuestionError(f"{name}必须是文字")
    text = " ".join(value.split())
    if not text or len(text) > maximum:
        raise AdaptiveQuestionError(f"{name}长度无效")
    if any(ord(char) < 32 for char in text):
        raise AdaptiveQuestionError(f"{name}包含控制字符")
    return text


def _has_positive_cue(text: str, cues: tuple[str, ...]) -> bool:
    """Require a risk cue in a clause that does not negate that cue."""

    clauses = re.split(r"[，。！？；,;!?\n]|但是|不过|然而|但", text)
    return any(
        cue in clause and not any(negation in clause for negation in _NEGATION_CUES)
        for clause in clauses
        for cue in cues
    )


def validate_adaptive_question(
    value: Any, *, allowed_topics: Iterable[str] | None = None
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AdaptiveQuestionError("动态问题必须是对象")
    required = {"topic", "question", "selection_mode", "options", "allow_other"}
    optional = {"other_label", "other_placeholder"}
    if not required <= set(value) or set(value) - required - optional:
        raise AdaptiveQuestionError("动态问题字段不完整")

    topic = _bounded_text(value["topic"], "topic", 80)
    if allowed_topics is not None and topic not in set(allowed_topics):
        raise AdaptiveQuestionError("动态问题主题未注册")
    question = _bounded_text(value["question"], "question", 240)
    if any(phrase in question for phrase in _PROHIBITED_PATIENT_ACTIONS):
        raise AdaptiveQuestionError("动态问题包含未允许的医疗指令")
    mode = value["selection_mode"]
    if mode not in SELECTION_MODES:
        raise AdaptiveQuestionError("动态问题选择模式无效")
    if not isinstance(value["allow_other"], bool):
        raise AdaptiveQuestionError("allow_other必须是布尔值")
    if not isinstance(value["options"], list) or len(value["options"]) > MAX_OPTIONS:
        raise AdaptiveQuestionError("动态问题选项数量无效")
    if mode == "text" and value["options"]:
        raise AdaptiveQuestionError("文字问题不能同时提供选项")
    if mode == "text" and value["allow_other"]:
        raise AdaptiveQuestionError("文字问题直接使用输入框，无需其他选项")
    if mode != "text" and not value["options"]:
        raise AdaptiveQuestionError("选择问题至少需要一个选项")

    options = []
    identities = set()
    for raw in value["options"]:
        if not isinstance(raw, dict):
            raise AdaptiveQuestionError("动态选项必须是对象")
        allowed = {
            "id",
            "label",
            "response_text",
            "risk_tags",
            "exclusive",
            "requires_detail",
            "detail_placeholder",
        }
        if set(raw) - allowed or not {"id", "label", "response_text"} <= set(raw):
            raise AdaptiveQuestionError("动态选项字段无效")
        identity = raw["id"]
        if not isinstance(identity, str) or not _IDENTIFIER.fullmatch(identity):
            raise AdaptiveQuestionError("动态选项ID无效")
        if identity in identities:
            raise AdaptiveQuestionError("动态选项ID重复")
        identities.add(identity)
        label = _bounded_text(raw["label"], "选项文字", 100)
        response = _bounded_text(raw["response_text"], "选项回答", 300)
        if label not in response:
            raise AdaptiveQuestionError("选项回答必须保留患者看到的按钮原文")
        if any(phrase in label + response for phrase in _PROHIBITED_PATIENT_ACTIONS):
            raise AdaptiveQuestionError("动态选项包含未允许的医疗指令")
        tags = raw.get("risk_tags", [])
        if (
            not isinstance(tags, list)
            or len(tags) > 4
            or len(set(tags)) != len(tags)
            or any(tag not in RISK_TAG_CUES for tag in tags)
        ):
            raise AdaptiveQuestionError("动态选项风险标签无效")
        for tag in tags:
            if not _has_positive_cue(label, RISK_TAG_CUES[tag]) or not _has_positive_cue(
                response, RISK_TAG_CUES[tag]
            ):
                raise AdaptiveQuestionError("风险标签没有可见文字依据")
        exclusive = raw.get("exclusive", False)
        requires_detail = raw.get("requires_detail", False)
        if not isinstance(exclusive, bool) or not isinstance(requires_detail, bool):
            raise AdaptiveQuestionError("动态选项行为标记无效")
        if mode == "multiple" and requires_detail:
            raise AdaptiveQuestionError("多选问题请使用统一的其他情况输入")
        item = {
            "id": identity,
            "label": label,
            "response_text": response,
            "risk_tags": list(tags),
            "exclusive": exclusive,
            "requires_detail": requires_detail,
        }
        if requires_detail:
            item["detail_placeholder"] = _bounded_text(
                raw.get("detail_placeholder", "请补充具体情况"),
                "补充提示",
                100,
            )
        elif "detail_placeholder" in raw:
            raise AdaptiveQuestionError("无需补充的选项不能设置输入提示")
        options.append(item)

    allow_other = value["allow_other"]
    output = {
        "topic": topic,
        "question": question,
        "selection_mode": mode,
        "options": options,
        "allow_other": allow_other,
    }
    if allow_other:
        output["other_label"] = _bounded_text(
            value.get("other_label", "其他情况"), "其他选项文字", 40
        )
        output["other_placeholder"] = _bounded_text(
            value.get("other_placeholder", "请补充具体情况"), "其他输入提示", 100
        )
    elif "other_label" in value or "other_placeholder" in value:
        raise AdaptiveQuestionError("未启用其他输入时不能设置相关文字")
    return output


def project_selected_answer(
    spec: dict[str, Any], choice_value: str | None, submitted_text: str
) -> dict[str, Any] | None:
    """Validate a browser selection against the latest published question."""

    if not choice_value or not choice_value.startswith("adaptive_"):
        return None
    options = {item["id"]: item for item in spec["options"]}
    selected: list[dict[str, Any]]
    detail = submitted_text.strip()
    if choice_value == "adaptive_other":
        if not spec.get("allow_other") or not detail or len(detail) > 2000:
            raise AdaptiveQuestionError("其他情况需要填写有效内容")
        return {"text": detail, "selected": [], "risk_tags": [], "kind": "other"}
    if choice_value.startswith("adaptive_detail:"):
        identity = choice_value.split(":", 1)[1]
        item = options.get(identity)
        if not item or not item.get("requires_detail") or not detail:
            raise AdaptiveQuestionError("补充回答与当前选项不匹配")
        prefix = item["response_text"]
        text = detail if detail.startswith(prefix) else f"{prefix}：{detail}"
        return {
            "text": text,
            "selected": [identity],
            "risk_tags": list(item["risk_tags"]),
            "kind": "detail",
        }
    if choice_value.startswith("adaptive_single:"):
        identities = [choice_value.split(":", 1)[1]]
        if spec["selection_mode"] != "single":
            raise AdaptiveQuestionError("单选回答与当前问题不匹配")
    elif choice_value.startswith("adaptive_multiple:"):
        encoded = choice_value.split(":", 1)[1]
        identities = encoded.split(",") if encoded else []
        if spec["selection_mode"] != "multiple":
            raise AdaptiveQuestionError("多选回答与当前问题不匹配")
    else:
        raise AdaptiveQuestionError("动态回答标识无效")
    if not identities or len(identities) != len(set(identities)):
        raise AdaptiveQuestionError("动态回答选择无效")
    try:
        selected = [options[identity] for identity in identities]
    except KeyError:
        raise AdaptiveQuestionError("动态回答已经失效") from None
    if any(item["requires_detail"] for item in selected):
        raise AdaptiveQuestionError("这个选项需要补充文字")
    if len(selected) > 1 and any(item["exclusive"] for item in selected):
        raise AdaptiveQuestionError("互斥选项不能与其他选项同时提交")
    expected = "；".join(item["response_text"] for item in selected)
    if submitted_text.strip() != expected:
        raise AdaptiveQuestionError("动态回答文字与当前选项不一致")
    return {
        "text": expected,
        "selected": identities,
        "risk_tags": sorted({tag for item in selected for tag in item["risk_tags"]}),
        "kind": spec["selection_mode"],
    }


def public_question(value: dict[str, Any]) -> dict[str, Any]:
    """Return an isolated copy suitable for patient-facing serialization."""

    return deepcopy(value)
