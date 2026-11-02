from __future__ import annotations
import json
from typing import Any, Dict, List

def _estimate_roundtrip_tokens(messages: List[Dict[str, str]], raw_command: Any) -> int:
    """Coarse token estimate for one backend round trip (P2-B-D10 fuse).

    Roughly two characters per token for mixed Chinese/English text.
    Deliberately coarse: this is a fuse for backends that emit no
    ``model.responded`` usage events at all, not metering — it only
    ever stands in for calls that produced no new usage event, so a
    fully metered backend keeps exactly its event-driven behaviour.
    """
    prompt_chars = sum(
        len(str(item.get("content", "")))
        for item in messages
        if isinstance(item, dict)
    )
    if isinstance(raw_command, str):
        response_chars = len(raw_command)
    elif isinstance(raw_command, dict):
        response_chars = len(json.dumps(raw_command, ensure_ascii=False))
    else:
        response_chars = 0
    return (prompt_chars + response_chars + 1) // 2
