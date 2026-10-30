from __future__ import annotations
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from importlib import resources
from typing import Any, Dict, List, Optional
from ..baseline_projection import build_allergy_hint
from ..direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from ..domain import Assertion, CaseState, Phase, RiskLevel
from ..followups import build_follow_up_plan
from ..handoff import LABELS as FACT_LABELS
from ..reasoning import RuleBasedProposer, validate_workspace
from ..text_match import contains_unnegated, find_unnegated_certain
from ..worry import extract_worry_object, is_worry_sentence

from .constants import _CHIEF_CHUNK_STOPWORDS, _GOAL_DRUG_TREATMENT_WORDS, _GOAL_PART_FIXED_HANDLING, _GOAL_PART_PREFIXES, _GOAL_VISIT_PART_KEYWORDS

def split_goal_parts(goal_line: str) -> List[str]:
    """Split a goal line into parts on ，、；, stripped, deduped,
    order preserved. Empty parts and trailing terminal punctuation
    (the recorded sentence's own 。) are dropped."""
    if not goal_line:
        return []
    parts: List[str] = []
    for raw in re.split("[，、；]", goal_line):
        part = raw.strip().strip("。！？")
        if part and part not in parts:
            parts.append(part)
    return parts


def _part_keywords(part: str) -> List[str]:
    """Content keywords of one goal part, for the addressed check.

    The full part is always a keyword; stripping question scaffolding
    and one leading verb character adds the shorter content form
    (e.g. "吃退烧药" -> "退烧药"), and a "不X" form adds X
    (e.g. "正不正常" -> "正常"). The addressed check is a substring
    check of these keywords in the visible close text *excluding*
    the goal_response's own quote of the goal — otherwise every part
    would trivially "appear" via its own quotation. Note the
    deliberate non-shortening: "缓解一点" is kept whole, not cut to
    "缓解", so a batch-A condition clause ("症状持续不缓解") does
    not falsely mark a relief-method part as answered (F-13)."""
    core = part.strip()
    # Strip scaffolding repeatedly (longest first each pass): stacked
    # frames are real ("还是先吃…" -> "先吃…" -> content; "是不是
    # 需要做…" -> "需要做…" -> content). Bounded so a degenerate
    # part cannot loop.
    for _ in range(3):
        stripped = False
        for prefix in _GOAL_PART_PREFIXES:
            if core.startswith(prefix) and len(core) > len(prefix):
                core = core[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    keywords: List[str] = []
    if part.strip() and part.strip() not in keywords:
        keywords.append(part.strip())
    if core and core not in keywords:
        keywords.append(core)
    # One leading verb character off (吃/用/做/留 …): "吃退烧药"
    # also matches on "退烧药" alone.
    if len(core) >= 3 and core[0] in "吃用喝做贴滴停开留":
        shorter = core[1:]
        if shorter not in keywords:
            keywords.append(shorter)
    # "不X" -> X ("正不正常" -> "正常").
    index = core.find("不")
    if index >= 0 and len(core) - index - 1 >= 2:
        without_bu = core[index + 1:]
        if without_bu not in keywords:
            keywords.append(without_bu)
    return [keyword for keyword in keywords if len(keyword) >= 2]


def _keyword_addressed(keyword: str, visible: str) -> bool:
    """A keyword occurrence counts as handling only when it is not
    a negated mention. O-17: the batch-A condition clause
    "症状持续不缓解" contains 缓解, but a negated mention is not a
    relief-method response — only an occurrence not immediately
    preceded by 不 counts."""
    if not keyword:
        return False
    start = 0
    while True:
        index = visible.find(keyword, start)
        if index < 0:
            return False
        if index == 0 or visible[index - 1] != "不":
            return True
        start = index + 1


def _visit_part_residual(part: str) -> str:
    """Content hidden inside a visit part (Y-16 / D-14).

    A part like "去医院挑开" or "去医院开药" is filtered as a
    visit part, which used to swallow the non-visit content after
    the visit phrase. The residual exists only for the narrow
    destination-plus-content shape: after stripping question
    scaffolding and urgency fillers, the part must *start* with
    去医院, and the text after it (with any further visit wording
    stripped) is the residual. Pure visit parts ("去医院",
    "去医院检查") leave an empty residual and stay covered, and
    a visit keyword embedded in a noun phrase ("想整理就医记录":
    就医记录 is the record itself, not a second question) never
    yields a residual — that pinned shape stays covered by the
    disposition line alone."""
    core = part.strip()
    for _ in range(3):
        stripped = False
        for prefix in _GOAL_PART_PREFIXES:
            if core.startswith(prefix) and len(core) > len(prefix):
                core = core[len(prefix):]
                stripped = True
                break
        if not stripped:
            break
    for filler in ("马上", "立刻", "尽快"):
        if core.startswith(filler):
            core = core[len(filler):]
    if not core.startswith("去医院"):
        return ""
    suffix = core[len("去医院"):]
    # J-09 filter (at the return below): a residual that is only
    # visit wording or a meaningless verb fragment is not content.
    for keyword in _GOAL_VISIT_PART_KEYWORDS + ("急诊", "看病"):
        suffix = suffix.replace(keyword, "")
    suffix = suffix.strip(" ，、；。！？")
    for prefix in ("还是", "或者", "顺便"):
        if suffix.startswith(prefix) and len(suffix) > len(prefix):
            suffix = suffix[len(prefix):]
    suffix = suffix.strip(" ，、；。！？")
    # J-09: a residual with no substantive request is not a part.
    # "去医院看看" leaves 看看 (a reduplicated verb with no
    # object) and "去医院检查一下" leaves 一下 (a bare verbal
    # complement); "去医院看一下" leaves 看一下 and "去医院
    # 看一下病" leaves 看一下病 — all pure visit wording, and
    # listing any of them as its own part produced the redundant
    # fixed-sentence close ("关于‘看看’/‘一下’"). Strip the
    # complement fragments, then drop a bare reduplicated verb
    # (看看/瞧瞧/试试/问问 …) and the split visit form 看病.
    # Length alone cannot do this — both observed defects are
    # exactly 2 characters. Substantive residuals survive:
    # 挑开/开药 contain no such fragment, and "挑开一下" keeps
    # 挑开 after the complement is stripped.
    for fragment in ("一下", "一遍", "一趟", "一看"):
        suffix = suffix.replace(fragment, "")
    suffix = suffix.strip(" ，、；。！？")
    # Split visit forms left by the keyword pass: "看一看病" loses
    # its trailing 看病 to that pass and leaves 看一; "看一看医生"
    # leaves the same fragment. Both are visit wording, not content.
    if suffix in ("", "看", "看病", "看一", "一看"):
        return ""
    if len(suffix) == 2 and suffix[0] == suffix[1]:
        return ""
    return suffix if len(suffix) >= 2 else ""


def unaddressed_goal_parts(goal_line: str, visible_text: str) -> List[str]:
    """Parts of a goal line with no handling in the visible text.

    Pure visit-keyword parts are treated as covered by the batch-A
    visit sentence and never listed; any other part whose keywords
    appear nowhere in visible_text is unaddressed. A visit part
    with residual non-visit content (Y-16 "挑开", D-14 "开药")
    contributes the residual as its own part. Callers must pass
    the close text *without* the goal_response quote (see
    _part_keywords) or the check is vacuous."""
    if not goal_line:
        return []
    visible = visible_text or ""
    unaddressed: List[str] = []
    for part in split_goal_parts(goal_line):
        if any(keyword in part for keyword in _GOAL_VISIT_PART_KEYWORDS):
            residual = _visit_part_residual(part)
            if not residual:
                continue
            keywords = _part_keywords(residual)
            if not keywords:
                continue
            if not any(_keyword_addressed(keyword, visible) for keyword in keywords):
                unaddressed.append(residual)
            continue
        keywords = _part_keywords(part)
        if not keywords:
            continue
        if not any(_keyword_addressed(keyword, visible) for keyword in keywords):
            unaddressed.append(part)
    return unaddressed


def unaddressed_urgent_goal_parts(goal_line: str, visible_text: str) -> List[str]:
    """Urgent-close walk (O-19): the shared walk plus examination
    parts. The urgent head answers the destination question
    ("要不要马上去急诊：要") but never the examination question,
    so a part naming 检查 with no 检查 handling in the visible
    text is walked with the fixed sentence ("需当面评估后决定"
    is the offline-evaluation framing the O-19 opinion allows).
    Pure destination parts stay covered by the direct sentence
    and are never listed — listing them would re-introduce the
    N-11 contradiction in per-part form."""
    parts = unaddressed_goal_parts(goal_line, visible_text)
    visible = visible_text or ""
    if _keyword_addressed("检查", visible):
        return parts
    for part in split_goal_parts(goal_line or ""):
        if "检查" in part and part not in parts:
            parts.append(part)
    return parts


def goal_part_handling(
    part: str, sheet: Dict[str, Any], chief_text: str = ""
) -> str:
    """Handling sentence body for one unaddressed goal part.

    Drug / treatment parts always take the fixed sentence. Otherwise
    the first self-care "do" item sharing a part keyword wins, with
    chief-overlapping items preferred (K-17: a do item matching the
    chief complaint's own wording is the most specific answer the
    sheet already contains). When no sheet item matches, the fixed
    sentence is returned — never an invented self-care line. In
    particular, self_care.json contains no ulcer-specific entry at
    all (verified: 溃疡/口腔/辣/酸 all absent), so ulcer goals fall
    through to the fixed sentence and stay a reported residual."""
    if any(word in part for word in _GOAL_DRUG_TREATMENT_WORDS):
        return _GOAL_PART_FIXED_HANDLING
    keywords = _part_keywords(part)
    care = sheet.get("self_care") if isinstance(sheet, dict) else None
    do_items = (
        [item for item in (care or {}).get("do", []) if isinstance(item, str)]
        if isinstance(care, dict)
        else []
    )
    candidates = [
        item for item in do_items if any(keyword in item for keyword in keywords)
    ]
    if not candidates:
        return _GOAL_PART_FIXED_HANDLING
    chunks = [
        chunk
        for chunk in re.split("[，、。；：\\s]", chief_text or "")
        if len(chunk) >= 2 and chunk not in _CHIEF_CHUNK_STOPWORDS
    ]
    for item in candidates:
        if any(chunk in item for chunk in chunks):
            return item
    return candidates[0]
