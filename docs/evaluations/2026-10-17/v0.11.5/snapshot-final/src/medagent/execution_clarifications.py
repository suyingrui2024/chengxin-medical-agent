"""Strict host records for user answers to live research execution questions."""

import math
from uuid import UUID

ACTIVE_CLARIFICATION_STATES = {"pending", "submitted"}


def validate_clarification(value):
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "id",
        "turn_id",
        "session_id",
        "question",
        "context",
        "options",
        "created_at",
        "deadline_at",
        "status",
        "answer",
        "reason",
    }:
        raise ValueError("澄清记录格式无效")
    if value["schema"] != "execution-clarification/v1":
        raise ValueError("澄清记录版本无效")
    for key in ["id", "turn_id"]:
        if not isinstance(value[key], str) or str(UUID(value[key])) != value[key]:
            raise ValueError("澄清标识无效")
    for key, maximum, empty in [
        ("session_id", 256, False),
        ("question", 240, False),
        ("context", 320, True),
    ]:
        item = value[key]
        if (
            not isinstance(item, str)
            or len(item) > maximum
            or (not empty and not item.strip())
            or any(ord(c) < 32 and c not in "\n\t" for c in item)
        ):
            raise ValueError("澄清文字无效")
        item.encode("utf-8")
    options = value["options"]
    if not isinstance(options, list) or len(options) not in {0, 2, 3}:
        raise ValueError("澄清选项无效")
    for option in options:
        if (
            not isinstance(option, str)
            or not 1 <= len(option) <= 80
            or option != option.strip()
            or any(ord(c) < 32 for c in option)
        ):
            raise ValueError("澄清选项无效")
        option.encode("utf-8")
    if len(set(options)) != len(options):
        raise ValueError("澄清选项重复")
    for key in ["created_at", "deadline_at"]:
        if (
            type(value[key]) not in {int, float}
            or not math.isfinite(value[key])
            or not 0 < value[key] < 1e15
        ):
            raise ValueError("澄清时间无效")
    if value["deadline_at"] < value["created_at"]:
        raise ValueError("澄清时限无效")
    if value["status"] not in {
        "pending",
        "submitted",
        "answered",
        "closed",
        "interrupted",
    }:
        raise ValueError("澄清状态无效")
    answer = value["answer"]
    if answer is not None and (
        not isinstance(answer, str)
        or not 1 <= len(answer) <= 2000
        or not answer.strip()
        or any(ord(c) < 32 and c not in "\n\t" for c in answer)
    ):
        raise ValueError("澄清回答无效")
    if answer is not None:
        answer.encode("utf-8")
    if (
        value["status"] == "pending"
        and answer is not None
        or value["status"] in {"submitted", "answered"}
        and answer is None
    ):
        raise ValueError("澄清回答与状态不符")
    reason = value["reason"]
    if reason is not None and (
        not isinstance(reason, str) or not 1 <= len(reason) <= 128
    ):
        raise ValueError("澄清关闭原因无效")
    return {**value, "options": list(options)}
