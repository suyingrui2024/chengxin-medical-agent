"""Declarative scale engine: one trusted interpreter, content is data only.

A scale pack declares its items, integer scores and score bands in JSON.
This module is the ONLY place that turns those declarations into a score;
manifests can never supply code (same contract as ``builtin.py``).

Deliberately NOT an expression evaluator. There is no ``eval``, no AST
walker, and no formula string. What the dialect can express:

- ``sum``: weighted sum of per-item integer scores.
- ``sum_percent``: ``sum / max * 100`` rounded half-up (ODI / NDI).
- ``choice`` items: the patient picks one option, the option's ``value``
  is the raw answer, and ``(reverse_of - value) * weight`` is the score.
- ``threshold_table`` items: a monotone step function mapping a measured
  number to a declared integer score (NEWS2-style vitals).

What it cannot express, and must not pretend to: powers, logs,
regression equations, population norm tables. BMI, eGFR, MELD, GRACE,
ASCVD, FRAX and growth Z-scores are out of scope by design — they need a
dedicated implementation with its own review, not a JSON dialect.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Mapping

ENGINE_ID = "scale-engine/v1"

# Rendered on every scale result. Fixed here, not in the pack, so a
# content edit cannot silently drop the disclaimer.
DISCLAIMER = (
    "本结果由量表计分程序按公开量表规则计算，仅作记录与参考，"
    "不构成诊断，也不替代医生判断。"
)

# Characters that have no business in a label and are the building
# blocks of an injection attempt. Arithmetic symbols are deliberately
# NOT listed: medical text legitimately contains them ("PHQ-8",
# "mmol/L", "≥ 7"), and nothing here is ever evaluated anyway.
_FORBIDDEN_CHARS = set("{}[]\\|&;`$")
_FORBIDDEN_WORDS = ("eval", "exec", "lambda", "import", "__", "function")

_ITEM_KINDS = {"choice", "threshold_table"}
_AGGREGATE_MODES = {"sum", "sum_percent"}
_DIRECTIONS = {"higher_is_worse", "higher_is_better"}
_ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,40}")

_MAX_ITEMS = 60
_MAX_OPTIONS = 12
_MAX_BANDS = 12
_MAX_LABEL = 60
# Raised from 200 when the department catalog added scales whose raw sum
# legitimately exceeds it (IBS-SSS 500, PSI 395, TCM constitution 300).
# The bound still exists so a typo in a pack cannot declare a 10^6 range.
_MAX_TOTAL = 600


class ScaleSpecError(ValueError):
    """The pack's scale declaration is invalid (load-time)."""


class ScaleInputError(ValueError):
    """The patient's answers cannot be scored (run-time, fail-closed)."""


def _reject_expression(value: str, where: str, limit: int = _MAX_LABEL) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ScaleSpecError(f"{where} 必须是非空文本")
    if len(value) > limit:
        raise ScaleSpecError(f"{where} 过长")
    if _FORBIDDEN_CHARS & set(value):
        raise ScaleSpecError(f"{where} 含未允许的符号")
    lowered = value.lower()
    if any(word in lowered for word in _FORBIDDEN_WORDS):
        raise ScaleSpecError(f"{where} 含疑似代码片段")


def _require_int(value: Any, where: str, low: int, high: int) -> int:
    # ``type is int`` rejects booleans and floats on purpose: a float
    # weight would make the score non-reproducible across platforms.
    if type(value) is not int or not low <= value <= high:
        raise ScaleSpecError(f"{where} 必须是 {low}..{high} 的整数")
    return value


def _validate_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list) or not 1 <= len(items) <= _MAX_ITEMS:
        raise ScaleSpecError("量表条目必须是有界列表")
    seen: set = set()
    output: List[Dict[str, Any]] = []
    for index, item in enumerate(items):
        where = f"条目[{index}]"
        if not isinstance(item, dict):
            raise ScaleSpecError(f"{where} 必须是对象")
        allowed = {"id", "label", "kind", "weight", "options", "reverse_of", "thresholds"}
        if set(item) - allowed:
            raise ScaleSpecError(f"{where} 含未支持的字段")
        identity = item.get("id")
        if not isinstance(identity, str) or not _ID_PATTERN.fullmatch(identity):
            raise ScaleSpecError(f"{where} id 无效")
        if identity in seen:
            raise ScaleSpecError(f"{where} id 重复")
        seen.add(identity)
        _reject_expression(item.get("label"), f"{where} label")
        kind = item.get("kind")
        if kind not in _ITEM_KINDS:
            raise ScaleSpecError(f"{where} kind 未注册")
        weight = _require_int(item.get("weight", 1), f"{where} weight", 1, 10)
        if kind == "choice":
            options = item.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= _MAX_OPTIONS:
                raise ScaleSpecError(f"{where} 选项必须是有界列表")
            values: set = set()
            normalised = []
            for option in options:
                if not isinstance(option, dict) or set(option) != {"value", "label"}:
                    raise ScaleSpecError(f"{where} 选项字段无效")
                value = _require_int(option["value"], f"{where} 选项值", 0, 99)
                if value in values:
                    raise ScaleSpecError(f"{where} 选项值重复")
                values.add(value)
                _reject_expression(option["label"], f"{where} 选项文本")
                normalised.append({"value": value, "label": option["label"]})
            reverse_of = item.get("reverse_of")
            if reverse_of is not None:
                reverse_of = _require_int(reverse_of, f"{where} reverse_of", 0, 99)
                # ``score = reverse_of - value`` must stay non-negative
                # for every option, so the pivot cannot sit below the
                # largest option value. Equality is the normal case: a
                # 0/1 item reversed by 1 maps 0→1 and 1→0.
                if reverse_of < max(values):
                    raise ScaleSpecError(f"{where} reverse_of 不能小于最大选项值")
            output.append(
                {
                    "id": identity,
                    "label": item["label"],
                    "kind": kind,
                    "weight": weight,
                    "options": normalised,
                    "reverse_of": reverse_of,
                }
            )
        else:
            thresholds = item.get("thresholds")
            if not isinstance(thresholds, list) or not 1 <= len(thresholds) <= 40:
                raise ScaleSpecError(f"{where} 阈值表必须是有界列表")
            normalised = []
            for position, entry in enumerate(thresholds):
                if not isinstance(entry, dict):
                    raise ScaleSpecError(f"{where} 阈值项必须是对象")
                if set(entry) - {"gte", "lt", "score"} or "score" not in entry:
                    raise ScaleSpecError(f"{where} 阈值项字段无效")
                score = _require_int(entry["score"], f"{where} 阈值得分", 0, 99)
                rule = {"score": score}
                for bound in ("gte", "lt"):
                    if bound in entry:
                        raw = entry[bound]
                        # Bounds may be fractional (temperature,
                        # creatinine); scores and weights may not.
                        if type(raw) not in (int, float) or raw != raw:
                            raise ScaleSpecError(f"{where} 阈值边界必须是有限数值")
                        rule[bound] = raw
                if "gte" in rule and "lt" in rule and rule["gte"] >= rule["lt"]:
                    raise ScaleSpecError(f"{where} 阈值区间倒置")
                if len(rule) == 1 and position != len(thresholds) - 1:
                    raise ScaleSpecError(f"{where} 只有最后一项可以是默认档")
                normalised.append(rule)
            terminal = normalised[-1]
            # A terminal rule is either a bare default or an open-ended
            # lower bound ("gte" with no "lt"); either way it catches
            # every remaining value.
            if len(terminal) == 2 and "gte" not in terminal:
                raise ScaleSpecError(f"{where} 阈值表缺少默认档")
            output.append(
                {
                    "id": identity,
                    "label": item["label"],
                    "kind": kind,
                    "weight": weight,
                    "thresholds": normalised,
                }
            )
    return output


def _validate_bands(bands: Any, minimum: int, maximum: int) -> List[Dict[str, Any]]:
    # Bands are optional: a scale that reports a score without an
    # interpretation is legitimate (and preferred where thresholds are
    # population-dependent).
    if bands is None or bands == []:
        return []
    if not isinstance(bands, list) or not 1 <= len(bands) <= _MAX_BANDS:
        raise ScaleSpecError("分档必须是有界列表")
    output: List[Dict[str, Any]] = []
    cursor = minimum
    keys: set = set()
    for index, band in enumerate(bands):
        where = f"分档[{index}]"
        if not isinstance(band, dict) or set(band) != {"min", "max", "key", "label"}:
            raise ScaleSpecError(f"{where} 字段无效")
        low = _require_int(band["min"], f"{where} min", minimum, maximum)
        high = _require_int(band["max"], f"{where} max", minimum, maximum)
        if low > high:
            raise ScaleSpecError(f"{where} 区间倒置")
        if low != cursor:
            raise ScaleSpecError(f"{where} 与上一档存在缺口或重叠")
        key = band["key"]
        if not isinstance(key, str) or not _ID_PATTERN.fullmatch(key) or key in keys:
            raise ScaleSpecError(f"{where} key 无效或重复")
        keys.add(key)
        _reject_expression(band["label"], f"{where} label")
        output.append({"min": low, "max": high, "key": key, "label": band["label"]})
        cursor = high + 1
    if cursor != maximum + 1:
        raise ScaleSpecError("分档未覆盖到量表满分")
    return output


def validate_scale(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate one pack's ``scale`` block; return the normalised spec.

    Called at registry load time, so a malformed pack fails before any
    patient can reach it.
    """
    scale = manifest.get("scale")
    if not isinstance(scale, dict):
        raise ScaleSpecError("计算能力缺少量表声明")
    allowed = {"engine", "aggregate", "items", "bands", "note"}
    if set(scale) - allowed:
        raise ScaleSpecError("量表声明含未支持的字段")
    if scale.get("engine") != ENGINE_ID:
        raise ScaleSpecError("量表引擎版本未注册")

    aggregate = scale.get("aggregate")
    if not isinstance(aggregate, dict) or set(aggregate) != {
        "mode",
        "min",
        "max",
        "unit",
        "direction",
    }:
        raise ScaleSpecError("聚合声明字段无效")
    mode = aggregate["mode"]
    if mode not in _AGGREGATE_MODES:
        raise ScaleSpecError("聚合方式未注册")
    minimum = _require_int(aggregate["min"], "aggregate.min", 0, _MAX_TOTAL)
    maximum = _require_int(aggregate["max"], "aggregate.max", 1, _MAX_TOTAL)
    if minimum >= maximum:
        raise ScaleSpecError("聚合区间倒置")
    if aggregate["direction"] not in _DIRECTIONS:
        raise ScaleSpecError("分值方向未注册")
    _reject_expression(aggregate["unit"], "aggregate.unit")

    items = _validate_items(scale.get("items"))
    # sum_percent emits 0..100 regardless of the raw sum range, so its
    # bands are validated against that scale, not against aggregate.max.
    band_low, band_high = (0, 100) if mode == "sum_percent" else (minimum, maximum)
    bands = _validate_bands(scale.get("bands"), band_low, band_high)
    if scale.get("note") is not None:
        _reject_expression(scale["note"], "scale.note", limit=300)

    return {
        "engine": ENGINE_ID,
        "mode": mode,
        "min": minimum,
        "max": maximum,
        "unit": aggregate["unit"],
        "direction": aggregate["direction"],
        "items": items,
        "bands": bands,
        "note": scale.get("note"),
    }


def expected_properties(spec: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The input_schema properties a pack must declare for this scale.

    Keeping the schema and the items in lockstep at load time is what
    stops the form, the contract and the engine from drifting apart.
    """
    properties: Dict[str, Dict[str, Any]] = {}
    for item in spec["items"]:
        if item["kind"] == "choice":
            entry = {
                "type": "number",
                "title": item["label"],
                "enum": [option["value"] for option in item["options"]],
            }
        else:
            # A threshold table accepts the measured number; bounds live
            # in the table, not in the schema, because the same field can
            # legitimately be fractional (temperature) or integer (BP).
            entry = {"type": "number", "title": item["label"]}
        properties[item["id"]] = entry
    return properties


def _score_choice(item: Mapping[str, Any], answer: int) -> int:
    allowed = {option["value"] for option in item["options"]}
    if answer not in allowed:
        raise ScaleInputError(f"「{item['label']}」的答案不在可选范围内")
    base = item["reverse_of"] - answer if item["reverse_of"] is not None else answer
    return base * item["weight"]


def _score_threshold(item: Mapping[str, Any], measured: float) -> int:
    for entry in item["thresholds"]:
        if "gte" in entry and measured < entry["gte"]:
            continue
        if "lt" in entry and measured >= entry["lt"]:
            continue
        return entry["score"] * item["weight"]
    # validate_scale guarantees a default entry, so this is unreachable.
    raise ScaleInputError(f"「{item['label']}」的阈值表未覆盖该数值")


def _band_for(bands: List[Dict[str, Any]], value: int) -> Dict[str, Any] | None:
    for band in bands:
        if band["min"] <= value <= band["max"]:
            return band
    return None


def run_scale(spec: Mapping[str, Any], arguments: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one submitted form. Missing answers fail closed.

    There is no partial score and no prorating: a scale answered 7 of 9
    is not a 7-of-9 estimate, it is an incomplete form.
    """
    answers = dict(arguments or {})
    items = spec["items"]
    known = {item["id"] for item in items}
    unknown = set(answers) - known
    if unknown:
        raise ScaleInputError("提交包含未注册的条目：" + "、".join(sorted(unknown)))
    missing = [item for item in items if item["id"] not in answers]
    if missing:
        raise ScaleInputError(
            f"未完成：还有 {len(missing)} 项没有作答，请补齐后再计算。"
        )

    total = 0
    detail = []
    for item in items:
        raw = answers[item["id"]]
        if item["kind"] == "choice":
            if type(raw) is not int:
                raise ScaleInputError(f"「{item['label']}」的答案必须是整数")
            score = _score_choice(item, raw)
            label = next(
                option["label"]
                for option in item["options"]
                if option["value"] == raw
            )
        else:
            if type(raw) not in (int, float) or raw != raw:
                raise ScaleInputError(f"「{item['label']}」必须填写一个有限数值")
            score = _score_threshold(item, raw)
            label = f"{raw:g}"
        total += score
        detail.append(
            {
                "id": item["id"],
                "label": item["label"],
                "answer_label": label,
                "score": score,
            }
        )

    if not spec["min"] <= total <= spec["max"]:
        raise ScaleInputError("合计分值超出量表量程，已拒绝计算结果")

    if spec["mode"] == "sum_percent":
        percent = int(
            (Decimal(total) * 100 / Decimal(spec["max"])).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        value = percent
        unit = "%"
        formula = f"{total} ÷ {spec['max']} × 100 = {percent}%"
    else:
        value = total
        unit = spec["unit"]
        parts = "+".join(str(entry["score"]) for entry in detail)
        formula = f"{parts} = {total} {unit}"

    band = _band_for(spec["bands"], value)
    return {
        "value": str(value),
        "unit": unit,
        "max_score": spec["max"] if spec["mode"] == "sum" else 100,
        # An empty object means "this scale declares no bands"; the
        # contract dialect has no nullable type, and fail-closed
        # renderers treat a missing ``key`` as no band.
        "band": (
            {
                "key": band["key"],
                "label": band["label"],
                "range": f"{band['min']}–{band['max']}{unit}",
            }
            if band is not None
            else {}
        ),
        "items": detail,
        "formula": formula,
        "method": "按量表条目声明加权求和；分档区间来自内容包声明",
        "scoring_note": spec.get("note") or "",
        "disclaimer": DISCLAIMER,
        "limitations": [
            "计分只反映本次作答，不验证作答是否真实。",
            "分档仅为量表区间，不构成诊断，也不代表需要何种治疗。",
        ],
        "result_renderer": "scale/v1",
    }
