"""Local, provenance-preserving drafts for pasted laboratory reports.

This module deliberately parses only text and CSV. It neither calls a model nor
writes clinical facts: each extracted row remains a patient-confirmation draft.
"""

from __future__ import annotations

import csv
from hashlib import sha256
from io import StringIO
import re
from typing import Any


MAX_REPORT_CHARS = 32_000
MAX_REPORT_ITEMS = 80
_DATE = re.compile(r"\b20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}")
_TEXT_ROW = re.compile(
    r"^\s*(?P<name>[A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff()（）/%+\- ]{0,80}?)\s+"
    r"(?P<value>[<>≤≥]?\s*\d+(?:\.\d+)?)\s*"
    r"(?P<unit>[A-Za-zμµ/%][A-Za-z0-9μµ/%^·*/.-]{0,20})?\s*"
    r"(?P<reference>(?:[<>≤≥]\s*\d+(?:\.\d+)?)|(?:\d+(?:\.\d+)?\s*(?:-|–|—|~|至)\s*\d+(?:\.\d+)?))\s*$"
)


def _clean(value: Any, maximum: int = 180) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text[:maximum]


def _item(raw: str, name: Any, value: Any, unit: Any, reference: Any, index: int):
    excerpt = _clean(raw, 300)
    return {
        "source_item_id": f"row-{index}-{sha256(excerpt.encode()).hexdigest()[:12]}",
        "name": _clean(name),
        "reported_value": _clean(value),
        "unit": _clean(unit, 40),
        "reference_range": _clean(reference, 100),
        "source_excerpt": excerpt,
        "status": "needs_confirmation",
    }


def _find_column(headers: list[str], aliases: set[str]) -> str | None:
    normalized = {header: re.sub(r"[\s_－-]", "", header).lower() for header in headers}
    return next((header for header, value in normalized.items() if value in aliases), None)


def _parse_csv(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(StringIO(text)))
    if not rows or not rows[0]:
        return []
    headers = [header for header in rows[0] if header]
    name = _find_column(headers, {"项目", "检测项目", "名称", "name", "item", "test"})
    value = _find_column(headers, {"结果", "数值", "检测结果", "value", "result"})
    unit = _find_column(headers, {"单位", "unit"})
    reference = _find_column(headers, {"参考范围", "参考值", "正常范围", "range", "reference"})
    if not name or not value:
        return []
    items = []
    for index, row in enumerate(rows, 1):
        if len(items) == MAX_REPORT_ITEMS:
            break
        if not _clean(row.get(name)) or not _clean(row.get(value)):
            continue
        raw = " | ".join(f"{header}: {_clean(row.get(header))}" for header in headers)
        items.append(_item(raw, row.get(name), row.get(value), row.get(unit), row.get(reference), index))
    return items


def _parse_text(text: str) -> list[dict[str, str]]:
    items = []
    for index, line in enumerate(text.splitlines(), 1):
        if len(items) == MAX_REPORT_ITEMS:
            break
        match = _TEXT_ROW.match(line)
        if not match:
            continue
        items.append(
            _item(
                line,
                match.group("name"),
                match.group("value"),
                match.group("unit"),
                match.group("reference"),
                index,
            )
        )
    return items


def parse_report_draft(text: Any, format_hint: str = "auto") -> dict[str, Any]:
    """Create a local review draft without interpreting clinical significance."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("报告内容不能为空")
    if len(text) > MAX_REPORT_CHARS:
        raise ValueError("报告内容过长，请分段粘贴")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
        raise ValueError("报告内容包含不支持的控制字符")
    if format_hint not in {"auto", "text", "csv"}:
        raise ValueError("报告格式只支持 text、csv 或 auto")
    items = _parse_csv(text) if format_hint in {"auto", "csv"} else []
    source_format = "csv" if items else "text"
    if not items and format_hint != "csv":
        items = _parse_text(text)
    missing_context = []
    if not _DATE.search(text):
        missing_context.append("报告日期")
    if not items:
        missing_context.append("可识别的项目、数值与参考范围")
    return {
        "schema": "report-draft/v1",
        "source_format": source_format,
        "items": items,
        "missing_context": missing_context,
        "limitations": [
            "仅按原文提取，未判断异常原因、诊断或治疗。",
            "每一项需由用户核对后才可作为患者提供的报告内容。",
        ],
    }
