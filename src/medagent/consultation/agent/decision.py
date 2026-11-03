"""Telling the model how to repair a decision it got wrong.

When the model's output is malformed, the difference between "the model made
a mistake" and "the transport failed" matters: only the first can be
repaired by asking again.  A JSON syntax error is the model's mistake; a
connection reset is not.

The guidance is specific about what to change -- which key is missing, what
shape the tool call must have -- because a vague correction produces the
same malformed output a second time.
"""
from __future__ import annotations

import json
from typing import Optional


_DECISION_ENVELOPE_ERRORS = {
    "invalid decision size": (
        "决策输出超过长度上限或不是文本：请只输出一个简短的 "
        "{tool, arguments} JSON 对象，不要附加解释或重复内容。"
    ),
    "decision must contain only tool and arguments": (
        "决策 JSON 必须只含 tool 和 arguments 两个键：请删掉多余键、"
        "补齐缺失键后按注册定义重提。"
    ),
    "invalid decision envelope": (
        "决策信封无效：tool 必须是字符串、arguments 必须是对象，"
        "请按工具目录的注册定义重提。"
    ),
}

def _decision_envelope_guidance(exc: BaseException) -> Optional[str]:
    """Repair guidance for a repairable decision-envelope error.

    Returns None for anything else. Bad JSON surfaces as
    ``json.JSONDecodeError`` (a ValueError subclass) from the same
    parser, so it is repairable too; every other exception type or
    message is a genuine backend failure, not a model output error.
    """
    if isinstance(exc, json.JSONDecodeError):
        return (
            "决策输出不是合法 JSON：请只输出一个 {tool, arguments} "
            "JSON 对象，不要附加解释、代码围栏或多余文本。"
        )
    if isinstance(exc, ValueError):
        return _DECISION_ENVELOPE_ERRORS.get(str(exc))
    return None


__all__ = [
    "_decision_envelope_guidance",
]
