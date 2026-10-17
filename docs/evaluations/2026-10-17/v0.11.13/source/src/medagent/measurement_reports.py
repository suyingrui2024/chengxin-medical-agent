"""Bounded separation of one explicit Celsius report from accompanying narrative.

This selects a registered input, never invents a value or interprets the supplement.
Ambiguous or conflicting readings remain subject to the existing strict parser.
"""
from dataclasses import dataclass
import re

from .assertion_evidence import has_uncertainty
from .interpreter import Interpreter


_CLAUSE = re.compile(r"[^，。；,;\n]+")
_NUMBER = r"-?\d+(?:\.\d+)?\s*(?:℃|°\s*[Cc]|摄氏度?)?"
_LABELED = re.compile(r"(?:腋温|腋下体温|体温)\s*(?:是|为|测得|读数为)?\s*(" + _NUMBER + r")")
_VALUE = re.compile(_NUMBER)
_QUALITY = re.compile(r"不准|不准确|不可靠|没看清|看不清|没看准|没测好|测不准")
_ANCHOR = re.compile(r"读数|数值|体温|温度|腋温|测量|体温计|探头|设备")
_HYPOTHETICAL = re.compile(r"如果|假如|假设|例如|比如")


def _qualifier(clause):
    if not (has_uncertainty(clause) or _QUALITY.search(clause)):
        return False
    if _ANCHOR.search(clause):
        return True
    rest = clause
    from .assertion_evidence import UNCERTAIN_TERMS

    for term in sorted(UNCERTAIN_TERMS, key=len, reverse=True):
        rest = rest.replace(term, "")
    rest = re.sub(r"但是|不过|只是|这个|这次|这点|具体|其实|现在|我|也|还|有点|不太|但", "", rest)
    rest = _QUALITY.sub("", rest)
    return not rest.strip(" 的了啊呀呢，。；;！!？?\t\r\n")


@dataclass(frozen=True)
class TemperatureReport:
    reading_text: str
    evidence: str
    uncertain: bool
    hypothetical: bool
    has_supplement: bool


def temperature_report(text):
    clauses = list(_CLAUSE.finditer(text))
    labeled = list(_LABELED.finditer(text))
    strict_value = Interpreter().temperature(text)
    selected = None
    # Multiple explicit temperature expressions are not a license to pick one.
    if len(labeled) == 1:
        candidate = labeled[0]
        has_unit = bool(re.search(r"℃|°\s*[Cc]|摄氏", candidate.group()))
        if strict_value is not None or has_unit:
            selected = candidate
    elif not labeled and strict_value is not None:
        values = list(_VALUE.finditer(text))
        selected = values[0] if len(values) == 1 else None
    scope_match = selected or (labeled[0] if len(labeled) == 1 else None)
    index = next(
        (
            i
            for i, c in enumerate(clauses)
            if scope_match and c.start() <= scope_match.start() < c.end()
        ),
        None,
    )
    if index is None:
        index = next(
            (i for i, c in enumerate(clauses) if _ANCHOR.search(c.group())),
            0 if clauses else None,
        )
    clause = clauses[index].group() if index is not None else text
    qualifiers = [
        c
        for i, c in enumerate(clauses)
        if index is not None and abs(i - index) == 1 and _qualifier(c.group())
    ]
    uncertain = (
        has_uncertainty(clause) or bool(_QUALITY.search(clause)) or bool(qualifiers)
    )
    if not scope_match and not _ANCHOR.search(clause):
        uncertain = _qualifier(clause)
    # Unknown information in a different field does not qualify this reading.
    if selected:
        own_prefix = text[clauses[index].start() : selected.start()]
        own_suffix = text[selected.end() : clauses[index].end()]
        uncertain = (
            has_uncertainty(own_prefix)
            or bool(_QUALITY.search(own_prefix))
            or bool(qualifiers)
        )
        if _qualifier(own_suffix):
            uncertain = True
    reading = selected.group() if selected else text
    # Do not choose one temperature if another Celsius expression is unlabelled.
    if selected and len(re.findall(r"-?\d+(?:\.\d+)?\s*(?:℃|°\s*[Cc]|摄氏度?)", text)) > 1:
        reading = text
    if selected and (
        len(list(_VALUE.finditer(clause))) > 1 or re.search(r"华氏|℉|°\s*[Ff]", clause)
    ):
        reading = text
    rest = text
    if selected:
        rest = text[: selected.start()] + " " + text[selected.end() :]
    for qualifier in qualifiers:
        rest = rest.replace(qualifier.group(), "")
    rest = re.sub(
        r"(?:我|他|她)?(?:刚刚|刚才|现在|这次)?(?:又)?(?:测了(?:一次)?|测得|量了(?:一次)?|读数|测的是|测到|测出|是|为)",
        "",
        rest,
    )
    rest = re.sub(r"想重新测量|想重新测温|再测一次腋温|想再测腋温|重新测腋温|重新测体温|再量一次体温|刚刚又测了一次腋温", "", rest)
    rest = "，".join(
        c.group() for c in _CLAUSE.finditer(rest) if not _qualifier(c.group().strip())
    )
    return TemperatureReport(
        reading_text=reading,
        evidence=clause if selected and not uncertain else text,
        uncertain=uncertain,
        hypothetical=bool(_HYPOTHETICAL.search(clause)),
        has_supplement=bool(rest.strip(" ，。；,;！!\t\r\n")),
    )
