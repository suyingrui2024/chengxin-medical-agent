"""Bounded checks of Chinese assertion evidence, not a clinical language parser."""

import re
from typing import Optional


UNKNOWN_PHRASES = (
    "不知道",
    "不清楚",
    "不确定",
    "说不清",
    "忘了",
    "不记得",
    "记不得",
    "没印象",
    "无法确认",
    "不详",
    "不敢肯定",
    "记不清",
    "记不准",
    "不能确认",
    "不能确定",
    "无法确定",
    "不太清楚",
    "不太确定",
    "不太记得",
    "说不准",
    "没把握",
    "没有把握",
)
UNCERTAIN_QUALIFIERS = (
    "好像",
    "似乎",
    "也许",
    "可能",
    "印象中",
    "应该",
    "未必",
    "不一定",
)
UNCERTAIN_TERMS = UNKNOWN_PHRASES + UNCERTAIN_QUALIFIERS
_CLAUSE = re.compile(r"[^，,。；;！!？?\n]+")
_QUALIFIER_ONLY = re.compile(
    r"^(?:(?:但是|不过|只是|具体|这点|这个|其实|现在|本人|家属|暂时|真的|已经|有点|不太|但|我|也|仍|还)\s*)*"
    + "(?:"
    + "|".join(re.escape(term) for term in UNCERTAIN_TERMS)
    + ")"
    + r"(?:了|的|呢|吧|啊|呀|哦|而已)*$"
)
_ABSENCE = re.compile(
    r"没有|没(?:用|吃|服|做|出现|发生)|未(?:用|吃|服|做|曾|出现|发生)|从未|否认|"
    r"不(?:存在|伴有|伴)|无(?!法|论|关|所谓|妨|须|需|效|意|从|可|聊|限|条件)"
)
_QUESTION = re.compile(r"有没有|有无|是否|是不是|会不会|能不能")
_NON_ABSENCE = re.compile(
    r"没有(?:必要|关系|影响|效果|帮助|意义|理由)|"
    r"(?:并不是|不是|并非|不能说|不代表|不等于|不敢说)(?:没有|没|无|未)|"
    r"(?:不能|无法|不)否认"
)


def has_uncertainty(text: str) -> bool:
    return any(term in text for term in UNCERTAIN_TERMS)


def has_absence_cue(text: str) -> bool:
    return bool(_ABSENCE.search(text)) or text.strip(" 。！!，,") == "没"


def absence_evidence_error(raw: str, evidence: str) -> Optional[str]:
    """Reject uncertain denials, including shortened/ambiguous source quotes.

    Scope is the quoted clause plus an immediately adjacent qualifier-only
    clause. Other named history in adjacent clauses does not invalidate a
    clear denial. Every occurrence is inspected because quotes carry no offset.
    This does not prove clinical field assignment or understand all negations.
    """
    if has_uncertainty(evidence):
        return "不确定的原话不能记为明确否认；请保留完整限定词并使用 unknown/null，或澄清。"
    if _QUESTION.search(evidence):
        return "疑问表达不能作为明确否认；请引用实际回答，或继续澄清。"
    if _NON_ABSENCE.search(evidence):
        return "引文中的否定针对必要性、效果或另一层否定，不能据此记录为没有该病史；请澄清范围。"
    if not has_absence_cue(evidence):
        return "阴性事实缺少明确否定证据；单个“不”或“无所谓”不能表示没有。"
    clauses = list(_CLAUSE.finditer(raw))
    for occurrence in re.finditer(re.escape(evidence), raw):
        indices = [
            index
            for index, clause in enumerate(clauses)
            if clause.start() < occurrence.end() and clause.end() > occurrence.start()
        ]
        for index in indices:
            if has_uncertainty(clauses[index].group()):
                return "引文所在分句含不确定限定，不能截取其中的否定作为明确没有；请引用完整范围。"
            if _QUESTION.search(clauses[index].group()) or _NON_ABSENCE.search(
                clauses[index].group()
            ):
                return "引文所在分句含疑问或否定范围限制，不能仅截取否定词确认没有；请澄清范围。"
        for index in {indices[0] - 1, indices[-1] + 1} if indices else set():
            if 0 <= index < len(clauses) and _QUALIFIER_ONLY.fullmatch(
                clauses[index].group().strip()
            ):
                return "原话紧邻不确定限定，不能省略该限定后记为明确否认；请引用完整范围或澄清。"
    return None
