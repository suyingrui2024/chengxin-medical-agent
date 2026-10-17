"""Typed, patient-confirmed location answers. Models propose drafts, never facts."""
from copy import deepcopy
import re
from uuid import uuid4

from .assertion_evidence import has_uncertainty

SCHEMA = "location-answer/v1"
FIELDS = ("region", "area", "side", "extent")
LABELS = {"region": "部位", "area": "具体位置", "side": "左右", "extent": "范围"}


def validate_answer_contract(task):
    contract = task.get("answer_contract")
    if contract is None:
        return
    if (
        task.get("input_kind") != "location"
        or not isinstance(contract, dict)
        or set(contract)
        != {"schema", "fields", "side_options", "extent_options", "area_presets"}
        or contract["schema"] != SCHEMA
        or contract["fields"] != list(FIELDS)
    ):
        raise ValueError("定位输入组件契约不兼容。")

    def options(values):
        if not isinstance(values, list) or not 1 <= len(values) <= 12:
            raise ValueError("定位选项数量无效。")
        codes = []
        for item in values:
            if (
                not isinstance(item, dict)
                or set(item) != {"value", "label"}
                or not isinstance(item["value"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", item["value"])
                or not isinstance(item["label"], str)
                or not 1 <= len(item["label"]) <= 40
            ):
                raise ValueError("定位选项格式无效。")
            codes.append(item["value"])
        if len(codes) != len(set(codes)):
            raise ValueError("定位选项不能重复。")
        return set(codes)

    if options(contract["side_options"]) != {
        "left",
        "right",
        "both",
        "midline",
        "unknown",
    }:
        raise ValueError("左右选项不符合v1契约。")
    if options(contract["extent_options"]) != {"point", "area", "unknown"}:
        raise ValueError("范围选项不符合v1契约。")
    regions = {x["value"] for x in task["options"] if x["value"] != "skip"}
    if regions != {"head", "chest", "abdomen", "back", "limbs", "other"}:
        raise ValueError("大部位选项不符合v1组件。")
    presets = contract["area_presets"]
    if not isinstance(presets, dict) or set(presets) - regions:
        raise ValueError("具体位置预设没有对应部位。")
    for values in presets.values():
        if options(values) & {"unknown", "describe", "back_to_presets"}:
            raise ValueError("具体位置预设不能覆盖输入组件保留项。")


def answer_spec(task, fields):
    contract = task.get("answer_contract")
    validate_answer_contract(task)
    if not contract or contract.get("schema") != SCHEMA:
        raise ValueError("当前部位任务没有注册定位输入组件。")
    if fields is None:
        fields = list(FIELDS)
    if (
        not isinstance(fields, list)
        or not 1 <= len(fields) <= len(FIELDS)
        or any(not isinstance(x, str) or x not in FIELDS for x in fields)
        or len(set(fields)) != len(fields)
    ):
        raise ValueError("answer_fields只能选择region、area、side、extent，不能重复。")
    fields = [x for x in FIELDS if x == "region" or x in fields]
    return {
        **deepcopy(contract),
        "id": str(uuid4()),
        "fields": fields,
        "regions": [deepcopy(x) for x in task["options"] if x["value"] != "skip"],
    }


def _option(options, value):
    if not isinstance(value, str):
        raise ValueError("定位选项必须是注册值。")
    found = next((x for x in options if x["value"] == value), None)
    if not found:
        raise ValueError("定位选项未注册。")
    return found


def validate_draft_evidence(spec, raw, evidence, quotes, source_scope, region):
    """Bounded lexical grounding for proposals; patient review remains required."""
    if not isinstance(quotes, dict) or set(quotes) - set(raw):
        raise ValueError("input_evidence只能引用实际提议的定位维度。")
    scopes = {}
    for field, value in raw.items():
        quote = quotes.get(field, evidence)
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError("每个定位维度的引文须逐字包含于完整定位原话。")
        scope = source_scope(quote)
        if scope not in evidence:
            raise ValueError("每个定位维度的引文须位于完整定位原话的范围内。")
        unknown = value == "unknown" or (
            isinstance(value, dict) and value.get("kind") == "unknown"
        )
        reported_text = isinstance(value, dict) and value.get("kind") == "text"
        if unknown and not has_uncertainty(scope):
            raise ValueError("未提及的定位维度保持空白，不能补成未知。")
        if not unknown and not reported_text and has_uncertainty(scope):
            raise ValueError("定位原话仍有不确定限定，不能预填成确定选项；保留原话或澄清。")
        if (
            reported_text
            and has_uncertainty(scope)
            and not has_uncertainty(value["value"])
        ):
            raise ValueError("位置文字不能删去原话中的不确定限定。")
        scopes[field] = scope
    for field, cues in {
        "side": {
            "left": ("左",),
            "right": ("右",),
            "both": ("两侧", "双侧", "左右", "两边"),
            "midline": ("正中", "中间", "中央", "中心"),
        },
        "extent": {
            "point": ("一个点", "一点", "点状", "一小点"),
            "area": ("一片", "一块", "片状", "整片", "整块", "整个", "一圈", "大片"),
        },
    }.items():
        value = raw.get(field)
        if value in cues and not any(cue in scopes[field] for cue in cues[value]):
            raise ValueError(f"{LABELS[field]}草稿缺少对应原话支持，请省略未给出的维度。")
    area = raw.get("area")
    if (
        isinstance(area, dict)
        and area.get("kind") == "text"
        and area.get("value") not in scopes["area"]
    ):
        raise ValueError("具体位置文字必须引用患者原话，不生成新的位置。")
    if isinstance(area, dict) and area.get("kind") == "preset":
        option = _option(spec["area_presets"].get(region, []), area["value"])
        cue = option["label"].removesuffix("附近")
        if cue not in scopes["area"]:
            raise ValueError("具体位置预设缺少对应原话，请保留患者描述或省略。")
    return scopes


def normalize_answers(spec, raw, complete=True):
    if not isinstance(raw, dict) or set(raw) - set(spec["fields"]):
        raise ValueError("定位答案包含未提问的字段。")
    if complete and set(raw) != set(spec["fields"]):
        raise ValueError("请逐项回答定位问题；说不清可明确选择说不清。")
    result = {}
    for field in spec["fields"]:
        if field not in raw:
            continue
        value = raw[field]
        if field == "area":
            if not isinstance(value, dict) or set(value) != {"kind", "value"}:
                raise ValueError("具体位置需要preset、text或unknown及对应值。")
            kind, area = value["kind"], value["value"]
            if kind == "unknown" and area is None:
                result[field] = {"assertion": "unknown", "value": None, "label": "说不清"}
            elif (
                kind == "text"
                and isinstance(area, str)
                and 1 <= len(area.strip()) <= 120
            ):
                text = area.strip()
                result[field] = {
                    "assertion": "unknown" if has_uncertainty(text) else "present",
                    "value": None if has_uncertainty(text) else text,
                    "label": text,
                    "reported_text": text,
                }
            elif kind == "preset":
                region = raw.get("region")
                option = _option(spec["area_presets"].get(region, []), area)
                result[field] = {
                    "assertion": "present",
                    "value": area,
                    "label": option["label"],
                }
            else:
                raise ValueError("具体位置格式无效；预设必须属于所选部位。")
        else:
            options = spec["regions"] if field == "region" else spec[field + "_options"]
            option = _option(options, value)
            result[field] = {
                "assertion": "unknown" if value == "unknown" else "present",
                "value": None if value == "unknown" else value,
                "label": option["label"],
            }
    return result


def project_answer(spec, answers, note):
    if not isinstance(note, str) or len(note) > 200:
        raise ValueError("定位补充最多200字。")
    normalized = normalize_answers(spec, answers)
    text = "；".join(
        f"{LABELS[key]}：{value['label']}" for key, value in normalized.items()
    )
    note = note.strip()
    if note:
        text += "；补充：" + note
    structured = {
        "schema": SCHEMA,
        "form_id": spec["id"],
        "answers": normalized,
        "note": note,
        "input_method": "patient_confirmed_controls",
    }
    if spec.get("prefill"):
        structured["proposal_source"] = {
            k: deepcopy(spec["prefill"][k])
            for k in ("id", "evidence", "source_turn_id", "answers", "input_evidence")
            if k in spec["prefill"]
        }
    return text, structured
