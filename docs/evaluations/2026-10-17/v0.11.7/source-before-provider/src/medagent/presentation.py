"""Patient-facing projections of controller output; no model code or execution."""

from copy import deepcopy
from functools import lru_cache
from importlib import resources
import hashlib
import json


UI_VERSION = "medical-ui/v1"


@lru_cache(maxsize=1)
def _question_catalog():
    raw = json.loads(
        resources.files("medagent.content")
        .joinpath("questions.json")
        .read_text(encoding="utf-8")
    )
    return {item["id"]: item for item in raw["questions"]}


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
    question = _question_catalog().get(reply.get("question_id"))
    if reply.get("calculation_input"):
        blocks.append(
            block(
                "calculation_input",
                f"calculation-input:{identity}",
                reply["calculation_input"],
            )
        )
    if reply.get("calculation_result"):
        blocks.extend(calculation_presentation(reply["calculation_result"])["blocks"])
    if (
        question
        and reply["kind"] in {"question", "clarification"}
        and not reply.get("task")
    ):
        blocks.append(
            block(
                "question",
                f"question:{identity}",
                {
                    "question_id": question["id"],
                    "prompt": reply["message"],
                    "canonical": reply["message"] == question["prompt"],
                    "input_kind": question["input"],
                    "choices": reply.get("quick_replies", []),
                },
            )
        )
    if reply.get("task"):
        task_data = deepcopy(reply["task"])
        if reply.get("input_error"):
            task_data["feedback"] = reply["message"]
        blocks.append(block("self_check", f"task:{identity}", task_data))
    if reply.get("observation_result"):
        blocks.append(
            block(
                "observation_series",
                f"observation:{identity}",
                reply["observation_result"],
            )
        )
    if reply.get("summary"):
        if reply["kind"] == "emergency":
            blocks.append(
                block(
                    "notice",
                    f"notice:{identity}",
                    {
                        "title": reply["summary"]["care_route"],
                        "message": reply["message"],
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
    if result.get("references"):
        data["references"] = deepcopy(result["references"])
    if "name" in result:
        data["subject_label"] = result["name"]
    for key in ("evidence", "implementation_sha256", "execution_id", "confirmation"):
        if key in result:
            data["provenance"][key] = deepcopy(result[key])
    return {
        "schema_version": UI_VERSION,
        "blocks": [
            block(
                "calculation", f"calculation:{result['capability_id']}:{identity}", data
            )
        ],
    }
