"""Shared first-party quantity evidence policy; no clinical meaning inference."""
from decimal import Decimal
import re

ALIASES = {
    "kg": "kg",
    "千克": "kg",
    "公斤": "kg",
    "g": "g",
    "克": "g",
    "mg": "mg",
    "毫克": "mg",
    "L": "L",
    "升": "L",
    "mL": "mL",
    "毫升": "mL",
}

QUANTITY = re.compile(
    r"(?<![0-9A-Za-z_.,])(?P<sign>[+\-−－]?\s*)(?P<value>\d{1,12}(?:\.\d{1,12})?)"
    r"\s*(?P<unit>kg|mg|mL|g|L|千克|公斤|毫克|毫升|克|升)(?![A-Za-z])"
)


class CapabilityError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def units_evidence(arguments, quote, sources):
    """Bind quantity + source unit to a continuous quote in real root user input.

    Does not confirm measurement quality, target-unit intent or clinical meaning.
    Match in the full source, so trimming a minus sign/exponent cannot launder it.
    """
    if not isinstance(quote, str) or not 1 <= len(quote) <= 1024:
        raise CapabilityError("input_evidence_invalid", "请提供用户原文中的连续引文，包含输入数值和原单位。")
    if not re.fullmatch(r"\d{1,12}(?:\.\d{1,12})?", arguments["value"]):
        raise CapabilityError("invalid_arguments", "数值必须是非负十进制，整数和小数各最多12位。")
    for source in reversed(sources):
        text = source["text"]
        start = text.find(quote)
        while start >= 0:
            for match in QUANTITY.finditer(text):
                token_start = (
                    match.start() + len(match["sign"]) - len(match["sign"].lstrip())
                )
                if not (start <= token_start and match.end() <= start + len(quote)):
                    continue
                prefix = text[:token_start].rstrip()
                suffix = text[match.end() :].lstrip()
                if (
                    match["sign"].strip() in {"-", "−", "－"}
                    or prefix.endswith(("+", "-", "−", "－", "—", "–", "~", "～", "/"))
                    or re.search(r"\d\s*(?:至|到|to)\s*$", prefix)
                    or re.search(r"\d\s*(?:[.,，．]|[eE][+\-]?)?\s*$", prefix)
                    or suffix.startswith(("/", "²", "³", "^"))
                    or re.match(r"(?:[-—–~～至到]|to\b)\s*\d", suffix)
                ):
                    continue
                if ALIASES[match["unit"]] == arguments["from_unit"] and Decimal(
                    match["value"]
                ) == Decimal(arguments["value"]):
                    return {
                        "quote": quote,
                        "source_turn_id": source["id"],
                        "scope": "quantity_and_source_unit_only",
                    }
            start = text.find(quote, start + 1)
    raise CapabilityError(
        "input_evidence_mismatch", "数值和原单位无法与用户原文对应；请核对参数或向用户补问，不能自行补数。"
    )
