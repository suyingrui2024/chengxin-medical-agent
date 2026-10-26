"""Explicit first-party implementation map; manifests cannot import code."""
from decimal import Decimal, localcontext
from importlib import resources
import json
import re

from ..domain import Assertion, ObservationQuality, Source
from ..interpreter import (
    Interpretation,
    Interpreter,
    ObservationInputError,
    UNKNOWN_PHRASES,
)
from .registry import Capability, CapabilityRegistry
from ..assertion_evidence import has_uncertainty
from ..measurement_reports import temperature_report
from .glucose import convert_glucose


def observation(text, choice, parser):
    if choice in {"not_available", "unable", "skip"}:
        return Interpretation(
            {"reason": choice},
            Assertion.UNKNOWN,
            Source.PATIENT,
            ObservationQuality.NOT_DONE,
        )
    if parser == "location":
        labels = {
            "head": "头/面部",
            "chest": "胸部",
            "abdomen": "腹部",
            "back": "背/腰部",
            "limbs": "四肢",
            "other": "其他部位",
        }
        if choice not in labels:
            raise ObservationInputError("请先选择图示部位；文字补充应由问诊理解，不能直接当成位置记录。如说不清具体部位，可点“说不清”或跳过这一步。")
        return Interpretation(
            labels[choice],
            Assertion.PRESENT,
            Source.PATIENT,
            ObservationQuality.NOT_ASSESSED,
        )
    report = temperature_report(text)
    value = Interpreter().temperature(report.reading_text)
    if value is not None:
        if report.hypothetical:
            raise ObservationInputError("请填写这次实际测得的读数；假设或举例的数值不会记录。")
        if report.uncertain:
            return Interpretation(
                None, Assertion.UNKNOWN, Source.PATIENT, ObservationQuality.INADEQUATE
            )
    if value is None and not report.uncertain:
        raise ObservationInputError(
            "未能确认这次读数，尚未记录结果。请只填写一个摄氏体温值，例如“38.2℃”；如读数超出当前录入范围或拿不准，请核对设备，也可以选择跳过。"
        )
    return Interpretation(
        value,
        Assertion.PRESENT if value is not None else Assertion.UNKNOWN,
        Source.PATIENT,
        ObservationQuality.NOT_ASSESSED
        if value is not None
        else ObservationQuality.INADEQUATE,
    )


def convert_units(arguments):
    raw = arguments["value"]
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", raw):
        raise ValueError("数值请使用非负十进制，整数和小数各最多 12 位")
    units = {
        "kg": ("mass", "1000"),
        "g": ("mass", "1"),
        "mg": ("mass", "0.001"),
        "L": ("volume", "1000"),
        "mL": ("volume", "1"),
    }
    before, after = units[arguments["from_unit"]], units[arguments["to_unit"]]
    if before[0] != after[0]:
        raise ValueError("质量与体积不能直接换算；缺少密度时拒绝计算")
    with localcontext() as context:
        context.prec = 48
        value = Decimal(raw) * Decimal(before[1]) / Decimal(after[1])
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
    return {
        "value": rendered,
        "unit": arguments["to_unit"],
        "formula": f"{raw} × {before[1]} ÷ {after[1]} = {rendered} {arguments['to_unit']}",
        "method": "相同量纲内按固定 SI 前缀比例换算；使用 Decimal 十进制计算",
        "limitations": ["只换算输入数值，不验证输入对应的实际测量。", "不推导摄入量、药物剂量或医疗建议。"],
    }


IMPLEMENTATIONS = {
    "observe.temperature": {
        "interpret": lambda text, choice: observation(text, choice, "temperature")
    },
    "observe.location": {
        "interpret": lambda text, choice: observation(text, choice, "location")
    },
    "calculate.units": {"execute": convert_units},
    "calculate.glucose": {"execute": convert_glucose},
}


def templates():
    root = resources.files("medagent.extensions").joinpath("templates")
    return [
        json.loads(file.read_text(encoding="utf-8"))
        for file in sorted(root.iterdir(), key=lambda p: p.name)
        if file.name.endswith(".json")
    ]


def load_registry(template_id="general"):
    template = next((item for item in templates() if item["id"] == template_id), None)
    if template is None:
        raise ValueError("问诊模板未注册")
    root = resources.files("medagent.extensions").joinpath("packs")
    capabilities = []
    for file in sorted(root.iterdir(), key=lambda p: p.name):
        if not file.name.endswith(".json"):
            continue
        manifest = json.loads(file.read_text(encoding="utf-8"))
        implementation = IMPLEMENTATIONS.get(manifest["id"])
        if implementation is None:
            raise ValueError("内容包未绑定受信实现")
        capabilities.append(Capability(manifest, **implementation))
    return CapabilityRegistry(capabilities, template)
