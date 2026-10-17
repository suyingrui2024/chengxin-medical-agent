"""Patient form proposals are data; only explicit confirmed parameters compute."""
from copy import deepcopy
import re
from uuid import uuid4

from .extensions.contracts import validate
from .extensions.input_evidence import units_evidence, ALIASES
from .extensions.conversion_forms import conversion_spec

UNIT_LABELS = {"kg": "千克 kg", "g": "克 g", "mg": "毫克 mg", "L": "升 L", "mL": "毫升 mL"}
INTENTS = ("换算", "换成", "转成", "多少克", "多少千克", "多少公斤", "多少毫克", "多少毫升", "多少升", "转换单位")
CANCELS = ("不换算", "不用换算", "先不算", "不用算", "取消换算", "跳过换算")
UNITS = re.compile(r"(?<![A-Za-z])(?:kg|mg|mL|g|L|千克|公斤|毫克|毫升|克|升)(?![A-Za-z])")


def request_draft(registry, identity, arguments, quote, text, turn_id):
    manifest = next(
        (item for item in registry.descriptions() if item["id"] == identity), None
    )
    if not manifest:
        raise ValueError("该计算未启用或尚无受信的患者输入组件")
    spec = conversion_spec(manifest)
    if (
        not isinstance(quote, str)
        or not 1 <= len(quote) <= 1024
        or quote not in text
        or not any(word in text for word in INTENTS)
        or any(word in text for word in CANCELS)
    ):
        raise ValueError("仅在患者明确请求换算时展示输入卡，并引用本轮请求原文")
    if spec.get("subject_check"):
        spec["subject_check"](quote)
    fields = manifest["input_schema"]["properties"]
    if not isinstance(arguments, dict) or set(arguments) - fields.keys():
        raise ValueError("换算参数包含未注册字段")
    arguments = {
        key: value
        for key, value in arguments.items()
        if value is not None and value != ""
    }
    for key, value in arguments.items():
        validate(value, fields[key])
    initial = {}
    if arguments.get("value") and arguments.get("from_unit"):
        spec["evidence"](arguments, quote, [{"id": turn_id, "text": text}])
        initial.update(value=arguments["value"], from_unit=arguments["from_unit"])
    # Target intent is still a proposal. Do not prefill a unit absent from the request.
    pattern = re.compile(
        r"(?<![A-Za-z])(?:"
        + "|".join(re.escape(v) for v in sorted(spec["aliases"], key=len, reverse=True))
        + r")(?![A-Za-z])"
    )
    mentioned = {spec["aliases"][m.group()] for m in pattern.finditer(quote)}
    if arguments.get("to_unit") in mentioned:
        initial["to_unit"] = arguments["to_unit"]
    return {
        "proposal_id": str(uuid4()),
        "capability_id": identity,
        "name": manifest["name"],
        "version": manifest["version"],
        "registry_sha256": registry.revision,
        "input": initial,
        "original_input": deepcopy(initial),
        "source_quote": quote,
        "source_turn_id": turn_id,
        "unit_options": [
            {"value": key, "label": value} for key, value in spec["labels"].items()
        ],
        "form_kind": manifest["patient_form"],
        "subject_label": spec["subject"],
        "scope_note": spec["scope"],
        "feedback_hint": spec["feedback"],
        "feedback": "",
    }


def confirmed_parameters(registry, draft, values):
    if registry.revision != draft["registry_sha256"]:
        raise ValueError("能力组合已变化，请重新提出换算请求")
    manifest = next(
        (
            item
            for item in registry.descriptions()
            if item["id"] == draft["capability_id"]
        ),
        None,
    )
    if not manifest or manifest["version"] != draft["version"]:
        raise ValueError("换算版本已变化，请重新核对")
    validate(values, manifest["input_schema"])
    # Registry execution performs the fixed arithmetic and dimension validation.
    return deepcopy(values)
