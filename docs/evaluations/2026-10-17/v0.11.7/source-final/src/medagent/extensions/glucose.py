"""Blood-glucose-only unit conversion using the documented conventional factor 18."""
from decimal import Decimal, ROUND_HALF_UP, localcontext
import re

from .input_evidence import CapabilityError


UNIT_ALIASES = {
    "mmol/L": "mmol/L",
    "mmol/l": "mmol/L",
    "mmol／L": "mmol/L",
    "毫摩尔/升": "mmol/L",
    "毫摩尔每升": "mmol/L",
    "mg/dL": "mg/dL",
    "mg/dl": "mg/dL",
    "mg／dL": "mg/dL",
    "毫克/分升": "mg/dL",
    "毫克每分升": "mg/dL",
}
UNIT_PATTERN = "|".join(
    re.escape(v) for v in sorted(UNIT_ALIASES, key=len, reverse=True)
)
QUANTITY = re.compile(
    r"(?<![0-9A-Za-z_.,])(?P<sign>[+\-−－]?\s*)(?P<value>\d{1,12}(?:\.\d{1,12})?)\s*(?P<unit>"
    + UNIT_PATTERN
    + r")(?![A-Za-z/／])"
)
SUBJECT = re.compile(r"血糖|血液葡萄糖|血浆葡萄糖|血清葡萄糖|\bblood glucose\b", re.I)
OTHER_ANALYTE = re.compile(r"糖化|HbA1c|胆固醇|肌酐|乳酸|尿糖|尿液|胰岛素|平均血糖|eAG", re.I)


def glucose_subject(quote):
    if (
        not SUBJECT.search(quote)
        or OTHER_ANALYTE.search(quote)
        or re.search(r"(?:不是|并非|不指|非)\s*(?:血糖|blood glucose)", quote, re.I)
    ):
        raise CapabilityError(
            "glucose_subject_missing", "这项工具仅换算明确的血糖读数；请确认检验项目，不能套用到其他化验。"
        )


def glucose_evidence(arguments, quote, sources):
    if not isinstance(quote, str) or not 1 <= len(quote) <= 1024:
        raise CapabilityError("input_evidence_invalid", "请引用包含血糖、数值及原单位的连续原文。")
    glucose_subject(quote)
    raw = arguments["value"]
    if not isinstance(raw, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", raw):
        raise CapabilityError("invalid_arguments", "血糖数值须为非负十进制，整数和小数各最多12位。")
    for source in reversed(sources):
        text = source["text"]
        start = text.find(quote)
        while start >= 0:
            candidates = []
            for match in QUANTITY.finditer(text):
                token_start = (
                    match.start() + len(match["sign"]) - len(match["sign"].lstrip())
                )
                if not (start <= token_start and match.end() <= start + len(quote)):
                    continue
                left = re.split(r"[，。；,;\n]", text[:token_start])[-1]
                right = re.split(r"[，。；,;\n]", text[match.end() :])[0]
                if not SUBJECT.search(left) or OTHER_ANALYTE.search(left + right):
                    continue
                try:
                    glucose_subject(left)
                except CapabilityError:
                    continue
                prefix = text[:token_start].rstrip()
                suffix = text[match.end() :].lstrip()
                if (
                    match["sign"].strip() in {"-", "−", "－"}
                    or prefix.endswith(
                        (
                            "+",
                            "-",
                            "−",
                            "－",
                            "—",
                            "–",
                            "~",
                            "～",
                            "/",
                            "负",
                            "^",
                            "×",
                            "*",
                        )
                    )
                    or re.search(r"\d\s*(?:至|到|to)\s*$", prefix)
                    or re.search(r"\d\s*(?:[.,，．]|[eE][+\-]?)?\s*$", prefix)
                    or re.search(
                        r"(?:不是|并非|不为|低于|高于|小于|大于|不超过|不足|至少|至多|[<>≤≥])\s*$", prefix
                    )
                    or suffix.startswith(("/", "／", "²", "³", "^"))
                    or re.match(r"(?:[-—–~～至到]|to\b)\s*\d", suffix)
                    or re.match(r"(?:以上|以下|以内|以外)", suffix)
                ):
                    continue
                candidates.append(match)
            if len(candidates) == 1:
                match = candidates[0]
                if (
                    Decimal(match["value"]) == Decimal(raw)
                    and UNIT_ALIASES[match["unit"]] == arguments["from_unit"]
                ):
                    return {
                        "quote": quote,
                        "source_turn_id": source["id"],
                        "scope": "analyte_quantity_and_source_unit_only",
                    }
            start = text.find(quote, start + 1)
    raise CapabilityError(
        "input_evidence_mismatch", "血糖项目、数值和原单位未能与原话唯一对应；请核对，不从其他化验或多个读数中猜测。"
    )


def convert_glucose(arguments):
    raw = arguments["value"]
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", raw):
        raise ValueError("血糖数值请使用非负十进制，整数和小数各最多12位")
    before, after = arguments["from_unit"], arguments["to_unit"]
    if before not in {"mg/dL", "mmol/L"} or after not in {"mg/dL", "mmol/L"}:
        raise ValueError("血糖换算只接受mg/dL或mmol/L")
    with localcontext() as ctx:
        ctx.prec = 48
        source = Decimal(raw)
        result = (
            source
            if before == after
            else source / Decimal(18)
            if before == "mg/dL"
            else source * Decimal(18)
        )
        rounded = result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if result and not rounded:
            rounded = result.quantize(
                Decimal(1).scaleb(result.adjusted() - 3), rounding=ROUND_HALF_UP
            )
        if before == after:
            rounded = source
        rendered = format(rounded, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
    operation = "=" if before == after else "÷ 18 ≈" if before == "mg/dL" else "× 18 ≈"
    return {
        "value": rendered,
        "unit": after,
        "formula": f"{raw} {before} {operation} {rendered} {after}",
        "method": "按ADA说明的血糖常用约定系数18换算，使用Decimal十进制运算；该系数为近似约定。",
        "approximate": before != after,
        "precision_policy": "同单位保留输入值；跨单位显示最多4位小数，ROUND_HALF_UP；非零极小值改保留4位有效数字以免显示为零。显示位数不代表测量精度。",
        "limitations": [
            "仅换算血糖（葡萄糖）读数的单位，不适用于其他化验项目或食物糖分。",
            "不核验测量设备、采样条件或读数真实性；不判断高低，不推算饮食、诊断或药物剂量。",
        ],
    }
