"""Aggregate ``{"group": ..., "value": ...}`` rows into per-group statistics.

Only plain decimal strings are accepted as values (no exponent notation,
no NaN/Infinity).  Valid inputs are documented as at most 16 integer
digits and 4 decimal places, optionally negative.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_TWO_PLACES = Decimal("0.01")


def _parse_value(value):
    """Return a Decimal for a valid decimal string, else None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _DECIMAL_RE.match(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return number


def _plain(number):
    """Exact decimal string without superfluous trailing fractional zeros."""
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-", "-0"):
        text = "0"
    return text


def _mean_text(total, count):
    mean = (total / Decimal(count)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if mean == 0:
        return "0.00"
    return format(mean, "f")


def aggregate(rows):
    """Group rows by stripped group name and summarise their values.

    Rows with a missing/blank group field, missing value field, or a value
    that is not a plain finite decimal string are ignored.  The result is
    sorted lexicographically by group name.
    """
    buckets = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        group = row.get("group")
        if not isinstance(group, str):
            continue
        group = group.strip()
        if not group:
            continue
        if "value" not in row:
            continue
        number = _parse_value(row.get("value"))
        if number is None:
            continue
        bucket = buckets.get(group)
        if bucket is None:
            buckets[group] = [1, number, number, number]
        else:
            bucket[0] += 1
            if number < bucket[1]:
                bucket[1] = number
            if number > bucket[2]:
                bucket[2] = number
            bucket[3] += number

    result = []
    for group in sorted(buckets):
        count, minimum, maximum, total = buckets[group]
        result.append({
            "group": group,
            "count": count,
            "min": _plain(minimum),
            "max": _plain(maximum),
            "mean": _mean_text(total, count),
        })
    return result
