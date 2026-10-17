"""Aggregate rows of {"group": str, "value": str} into per-group statistics.

Documented input range: decimal values with at most 16 integer digits and at
most 4 fractional digits; values may be negative.  Rows with a missing/blank
field, a blank group name, or a value that is not a finite plain decimal are
ignored.
"""

import re
from decimal import Decimal, ROUND_HALF_UP

# Plain decimal only: optional sign, digits with optional fraction, or a bare
# fraction.  Exponent notation, NaN, Infinity and empty strings are rejected.
_VALUE_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

_TWO_PLACES = Decimal("0.01")


def _parse_value(raw):
    """Return a finite Decimal for a valid decimal string, else None."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not _VALUE_RE.match(text):
        return None
    value = Decimal(text)
    if not value.is_finite():
        return None
    return value


def _format_exact(value):
    """Exact plain decimal string with trailing fractional zeros removed."""
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_mean(value):
    """Round to two decimals with ROUND_HALF_UP; zero is always '0.00'."""
    rounded = value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return "0.00"
    return format(rounded, "f")


def aggregate(rows):
    """Group valid rows by stripped group name and compute count/min/max/mean.

    Returns a list sorted lexicographically by group name; each item has exactly
    the keys ``group``, ``count``, ``min``, ``max`` and ``mean``.  min/max are
    exact plain decimal strings without redundant trailing zeros; mean is a
    ROUND_HALF_UP two-decimal string.  Empty/fully invalid input gives ``[]``.
    """
    totals = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw_group = row.get("group")
        if not isinstance(raw_group, str):
            continue
        group = raw_group.strip()
        if not group:
            continue
        value = _parse_value(row.get("value"))
        if value is None:
            continue
        bucket = totals.get(group)
        if bucket is None:
            totals[group] = [1, value, value, value]
        else:
            bucket[0] += 1
            if value < bucket[1]:
                bucket[1] = value
            if value > bucket[2]:
                bucket[2] = value
            bucket[3] += value

    result = []
    for group in sorted(totals):
        count, minimum, maximum, total = totals[group]
        result.append({
            "group": group,
            "count": count,
            "min": _format_exact(minimum),
            "max": _format_exact(maximum),
            "mean": _format_mean(total / Decimal(count)),
        })
    return result
