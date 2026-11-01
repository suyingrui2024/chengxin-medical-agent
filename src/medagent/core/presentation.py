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
    if reply.get("subject_change"):
        blocks.append(
            block(
                "subject_change", f"subject-change:{identity}", reply["subject_change"]
            )
        )
    if reply.get("calculation_input"):
        data = deepcopy(reply["calculation_input"])
        if reply.get("calculation_followup"):
            data["followup"] = deepcopy(reply["calculation_followup"])
        blocks.append(
            block(
                "calculation_input",
                f"calculation-input:{identity}",
                data,
            )
        )
    if reply.get("calculation_result"):
        results = calculation_presentation(reply["calculation_result"])["blocks"]
        if reply.get("calculation_followup"):
            results[0]["data"]["followup"] = deepcopy(reply["calculation_followup"])
        blocks.extend(results)
    if (
        reply.get("calculation_followup")
        and not reply.get("calculation_input")
        and not reply.get("calculation_result")
        and reply["kind"] != "emergency"
    ):
        blocks.append(
            block(
                "interaction_followup",
                f"calculation-followup:{identity}",
                reply["calculation_followup"],
            )
        )
    if (
        (question or reply.get("adaptive_question"))
        and reply["kind"] in {"question", "clarification"}
        and not reply.get("task")
    ):
        adaptive = deepcopy(reply.get("adaptive_question"))
        question_id = question["id"] if question else adaptive["topic"]
        # A bridging anchor is a host sentence prepended to the *same* bank
        # question ("你说…，再问一句：" + prompt).  Comparing the whole message
        # to the prompt alone marked those questions non-canonical, which made
        # the UI drop their visual spec (the 6-item emergency signal grid and
        # the short title).  Treat "prompt is the message, or its suffix" as
        # canonical; a genuine clarification never ends with the bank prompt.
        message = reply["message"]
        prompt = question["prompt"] if question else None
        canonical = bool(question) and (
            message == prompt or message.endswith(prompt)
        )
        question_data = {
            "question_id": question_id,
            "prompt": message,
            "canonical": canonical,
            "input_kind": question["input"] if question else "adaptive",
            "choices": reply.get("quick_replies", []),
        }
        if adaptive:
            question_data.update(
                {
                    "adaptive": True,
                    "selection_mode": adaptive["selection_mode"],
                    "choices": adaptive["options"],
                    "allow_other": adaptive["allow_other"],
                    **(
                        {
                            "other_label": adaptive["other_label"],
                            "other_placeholder": adaptive["other_placeholder"],
                        }
                        if adaptive["allow_other"]
                        else {}
                    ),
                }
            )
        blocks.append(
            block(
                "question",
                f"question:{identity}",
                question_data,
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
    if reply.get("conclusion"):
        blocks.append(
            block("conclusion", f"conclusion:{identity}", reply["conclusion"])
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
                        "level": "emergency",
                        "action_label": "现在拨打 120",
                        "action_href": "tel:120",
                    },
                )
            )
        else:
            blocks.append(block("summary", f"summary:{identity}", reply["summary"]))
    if reply.get("follow_up"):
        blocks.append(
            block("follow_up", f"followup:{identity}", reply["follow_up"])
        )
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
