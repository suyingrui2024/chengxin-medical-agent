"""Patient-facing projections of controller output; no model code or execution."""

from copy import deepcopy
import hashlib
import json


UI_VERSION = "medical-ui/v1"


def block(kind, identity, data):
    return {"id": str(identity), "type": kind, "data": deepcopy(data)}


def reply_presentation(reply):
    identity = (
        reply.get("action_id")
        or reply.get("question_id")
        or hashlib.sha256(
            json.dumps(reply, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:20]
    )
    blocks = [
        block(
            "message",
            f"message:{identity}",
            {
                "text": reply["message"],
                "kind": reply["kind"],
            },
        )
    ]
    if reply.get("task"):
        blocks.append(block("self_check", f"task:{identity}", reply["task"]))
    if reply.get("summary"):
        if reply["kind"] == "emergency":
            blocks.append(
                block(
                    "notice",
                    f"notice:{identity}",
                    {
                        "title": reply["summary"]["care_route"],
                        "items": reply["summary"].get("triggers", []),
                    },
                )
            )
        else:
            blocks.append(block("summary", f"summary:{identity}", reply["summary"]))
    return {"schema_version": UI_VERSION, "blocks": blocks}


def calculation_presentation(result, input_labels=None):
    identity = (
        result.get("execution_id")
        or hashlib.sha256(
            json.dumps(result["input"], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:20]
    )
    labels = input_labels or {"value": "输入数值", "from_unit": "原单位", "to_unit": "目标单位"}
    data = {
        **deepcopy(result["data"]),
        "inputs": [
            {"key": key, "label": labels.get(key, key), "value": value}
            for key, value in result["input"].items()
        ],
        "input_status": result["input_status"],
        "quality": result["quality"],
        "review_status": result["review_status"],
        "provenance": {
            key: result[key]
            for key in ("capability_id", "version", "registry_sha256", "input_origin")
        },
    }
    return {
        "schema_version": UI_VERSION,
        "blocks": [
            block(
                "calculation", f"calculation:{result['capability_id']}:{identity}", data
            )
        ],
    }
