"""Named-formula engine: the pack names a formula, the host owns the math.

Companion to ``scale_engine``. A scale adds up item scores; a formula is
one function over measured inputs (BMI, MAP, anion gap…). They are kept
as separate declaration blocks because their validation rules differ: a
scale's items are content, a formula's inputs are a fixed signature the
content is not allowed to widen.

Same safety posture as the scale engine: **no expression evaluation**.
The JSON carries a function *name* that must exist in
``formulas.FORMULAS``; the implementation is Python, reviewed here, and
never reachable from content. A pack that names an unknown function, or
that declares inputs differing from the registered signature, is
rejected at load time.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any, Dict, List, Mapping

from .formulas import FORMULAS

ENGINE_ID = "formula-engine/v1"

# Raised to 20 for EASI (4 body regions x [area + 4 severity signs]).
MAX_INPUTS = 20
MAX_BANDS = 12
_MAX_LABEL = 80

_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,40}")
# [0-9] rather than \d on purpose: Python's \d matches full-width digits,
# so a pasted 「１７０」 would pass a \d check and then either parse to an
# unexpected value or fail deep inside Decimal.
_DECIMAL_PATTERN = re.compile(r"-?[0-9]{1,6}(?:\.[0-9]{1,4})?")
# ISO calendar date, ASCII digits only (same reason as above).
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FORBIDDEN_CHARS = set("{}[]\\|&;`$")
_FORBIDDEN_WORDS = ("eval", "exec", "lambda", "import", "__", "function")

_DIRECTIONS = {"higher_is_worse", "higher_is_better", "no_direction"}


class FormulaSpecError(ValueError):
    """The pack's formula declaration is invalid (load-time)."""


class FormulaInputError(ValueError):
    """The submitted values cannot be evaluated (run-time, fail-closed)."""


def _text(value: Any, where: str, limit: int = _MAX_LABEL) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise FormulaSpecError(f"{where} 必须是非空文本")
    if _FORBIDDEN_CHARS & set(value):
        raise FormulaSpecError(f"{where} 含未允许的符号")
    lowered = value.lower()
    if any(word in lowered for word in _FORBIDDEN_WORDS):
        raise FormulaSpecError(f"{where} 含疑似代码片段")
    return value


def _decimal(value: Any, where: str) -> Decimal:
    """Parse a declared bound. Floats are rejected on purpose.

    A JSON float has no exact decimal value, so a bound written as
    ``0.1`` could land on either side of a band edge depending on the
    platform. Bounds are therefore strings and parsed with Decimal.
    """
    if isinstance(value, float):
        raise FormulaSpecError(f"{where} 必须写成十进制字符串，不能用浮点数")
    if not isinstance(value, str) or not _DECIMAL_PATTERN.fullmatch(value):
        raise FormulaSpecError(f"{where} 必须是十进制字符串")
    try:
        return Decimal(value)
    except InvalidOperation:  # pragma: no cover - pattern already guards
        raise FormulaSpecError(f"{where} 无法解析为十进制") from None


def _validate_inputs(inputs: Any, expected: List[Dict[str, Any]]) -> None:
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_INPUTS:
        raise FormulaSpecError("公式输入必须是有界列表")
    if len(inputs) != len(expected):
        raise FormulaSpecError("公式输入项数量与已注册签名不一致")
    for index, (item, signature) in enumerate(zip(inputs, expected)):
        where = f"输入[{index}]"
        if not isinstance(item, dict) or set(item) != set(signature):
            raise FormulaSpecError(f"{where} 字段必须与已注册签名完全一致")
        for key, value in item.items():
            if value != signature[key]:
                raise FormulaSpecError(f"{where}.{key} 不得偏离已注册签名")


def _validate_output(output: Any, expected: Dict[str, Any]) -> None:
    if not isinstance(output, dict) or set(output) != set(expected):
        raise FormulaSpecError("输出声明字段必须与已注册签名完全一致")
    for key, value in output.items():
        if value != expected[key]:
            raise FormulaSpecError(f"输出声明 {key} 不得偏离已注册签名")


def _validate_bands(bands: Any) -> List[Dict[str, Any]]:
    if bands is None or bands == []:
        return []
    if not isinstance(bands, list) or not 1 <= len(bands) <= MAX_BANDS:
        raise FormulaSpecError("分档必须是有界列表")
    output: List[Dict[str, Any]] = []
    previous_upper = None
    keys: set = set()
    for index, band in enumerate(bands):
        where = f"分档[{index}]"
        if not isinstance(band, dict):
            raise FormulaSpecError(f"{where} 必须是对象")
        allowed = {"gte", "lt", "key", "label"}
        if set(band) - allowed or "key" not in band or "label" not in band:
            raise FormulaSpecError(f"{where} 字段无效")
        key = band["key"]
        if not isinstance(key, str) or not _ID_PATTERN.fullmatch(key) or key in keys:
            raise FormulaSpecError(f"{where} key 无效或重复")
        keys.add(key)
        _text(band["label"], f"{where} label")
        entry: Dict[str, Any] = {"key": key, "label": band["label"]}
        if "gte" in band:
            entry["gte"] = _decimal(band["gte"], f"{where} gte")
        if "lt" in band:
            entry["lt"] = _decimal(band["lt"], f"{where} lt")
        if index == 0:
            if "gte" in entry:
                raise FormulaSpecError(f"{where} 首档不能有下界")
        else:
            if "gte" not in entry:
                raise FormulaSpecError(f"{where} 缺少下界")
            if previous_upper is None or entry["gte"] != previous_upper:
                raise FormulaSpecError(f"{where} 与上一档存在缺口或重叠")
        if "lt" in entry and "gte" in entry and entry["lt"] <= entry["gte"]:
            raise FormulaSpecError(f"{where} 区间倒置")
        previous_upper = entry.get("lt")
        output.append(entry)
    if "lt" in output[-1]:
        raise FormulaSpecError("末档必须是开区间（只写上界 gte）")
    return output


def validate_formula(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one pack's ``formula`` block; return the normalised spec."""
    block = manifest.get("formula")
    if not isinstance(block, dict):
        raise FormulaSpecError("计算能力缺少公式声明")
    allowed = {"engine", "function", "citation", "inputs", "output", "bands", "note"}
    if set(block) - allowed:
        raise FormulaSpecError("公式声明含未支持的字段")
    if block.get("engine") != ENGINE_ID:
        raise FormulaSpecError("公式引擎版本未注册")
    name = block.get("function")
    if not isinstance(name, str) or name not in FORMULAS:
        raise FormulaSpecError("公式函数未注册")
    spec = FORMULAS[name]

    citation = block.get("citation")
    if citation != spec.citation:
        raise FormulaSpecError("公式来源标识与已注册实现不一致")
    references = manifest.get("references") or []
    if not any(citation in str(ref.get("section", "")) for ref in references):
        raise FormulaSpecError("公式来源必须出现在 references 的 section 中")

    _validate_inputs(block.get("inputs"), spec.inputs)
    _validate_output(block.get("output"), spec.output)
    bands = _validate_bands(block.get("bands"))
    if block.get("note") is not None:
        _text(block["note"], "formula.note", limit=300)

    return {
        "engine": ENGINE_ID,
        "function": name,
        "citation": citation,
        "inputs": [dict(item) for item in spec.inputs],
        "output": dict(spec.output),
        "bands": bands,
        "note": block.get("note"),
        "spec": spec,
    }


def expected_properties(spec: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The input_schema properties a pack must declare for this formula."""
    properties: Dict[str, Dict[str, Any]] = {}
    for item in spec["inputs"]:
        if item["type"] == "number":
            properties[item["id"]] = {
                "type": "string",
                "title": item["label"],
                "maxLength": 12,
            }
        elif item["type"] == "date":
            properties[item["id"]] = {
                "type": "string",
                "title": item["label"],
                "maxLength": 10,
            }
        else:
            properties[item["id"]] = {
                "type": "string",
                "title": item["label"],
                "enum": [option["value"] for option in item["options"]],
            }
    return properties


def _parse_values(spec: Mapping[str, Any], values: Mapping[str, Any]):
    parsed: Dict[str, Any] = {}
    for item in spec["inputs"]:
        key = item["id"]
        if key not in values:
            raise FormulaInputError(f"缺少输入：{item['label']}")
        raw = values[key]
        if item["type"] == "enum":
            allowed = {option["value"] for option in item["options"]}
            if raw not in allowed:
                raise FormulaInputError(f"{item['label']} 不在可选范围内")
            parsed[key] = raw
            continue
        if item["type"] == "date":
            if not isinstance(raw, str) or not _DATE_PATTERN.fullmatch(raw):
                raise FormulaInputError(f"{item['label']} 必须是 YYYY-MM-DD 格式的日期")
            try:
                moment = date.fromisoformat(raw)
            except ValueError:
                raise FormulaInputError(f"{item['label']} 不是有效日期") from None
            if "min" in item and moment < date.fromisoformat(item["min"]):
                raise FormulaInputError(f"{item['label']} 早于可计算范围")
            if "max" in item and moment > date.fromisoformat(item["max"]):
                raise FormulaInputError(f"{item['label']} 晚于可计算范围")
            parsed[key] = moment
            continue
        if not isinstance(raw, str) or not _DECIMAL_PATTERN.fullmatch(raw):
            raise FormulaInputError(f"{item['label']} 必须是十进制数值")
        value = Decimal(raw)
        low = Decimal(item["min"])
        high = Decimal(item["max"])
        if not low <= value <= high:
            raise FormulaInputError(
                f"{item['label']} 超出可计算范围（{item['min']}–{item['max']} {item['unit']}）"
            )
        parsed[key] = value
    extra = set(values) - {item["id"] for item in spec["inputs"]}
    if extra:
        raise FormulaInputError("提交包含未注册的输入：" + "、".join(sorted(extra)))
    return parsed


def _round(value: Decimal, precision: Mapping[str, Any]) -> Decimal:
    if "places" in precision:
        places = int(precision["places"])
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    significant = int(precision["significant"])
    if value == 0:
        return Decimal(0)
    exponent = value.adjusted() - significant + 1
    return value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_UP)


def _band_for(bands: List[Dict[str, Any]], value: Decimal) -> Dict[str, Any] | None:
    for band in bands:
        if "gte" in band and value < band["gte"]:
            continue
        if "lt" in band and value >= band["lt"]:
            continue
        return band
    return None


def _render(value: Decimal, precision: Mapping[str, Any]) -> str:
    if "places" in precision:
        return f"{value:.{int(precision['places'])}f}"
    return format(value.normalize(), "f")


def run_formula(spec: Mapping[str, Any], values: Mapping[str, Any]) -> Dict[str, Any]:
    """Evaluate one submission. No partial result, no silent clamping."""
    parsed = _parse_values(spec, values)
    formula = spec["spec"]
    with localcontext() as context:
        context.prec = 28
        try:
            raw = formula.fn(parsed)
        except FormulaInputError:
            raise
        except (ArithmeticError, ValueError) as exc:
            raise FormulaInputError(f"无法完成计算：{exc}") from None
        if not isinstance(raw, Decimal) or not raw.is_finite():
            raise FormulaInputError("计算结果无效")
        output = spec["output"]
        low = Decimal(output["min"])
        high = Decimal(output["max"])
        if not low <= raw <= high:
            raise FormulaInputError("计算结果超出该公式的可信范围")
        rounded = _round(raw, output["precision"])

    band = _band_for(spec["bands"], rounded)
    shown = _render(rounded, output["precision"])
    return {
        "value": shown,
        "unit": output["unit"],
        "band": (
            {"key": band["key"], "label": band["label"], "range": ""}
            if band is not None
            else {}
        ),
        "formula": formula.render(parsed, shown),
        "method": formula.method,
        "scoring_note": spec.get("note") or "",
        "disclaimer": formula.disclaimer,
        "limitations": list(formula.limitations),
        "result_renderer": "formula/v1",
    }
