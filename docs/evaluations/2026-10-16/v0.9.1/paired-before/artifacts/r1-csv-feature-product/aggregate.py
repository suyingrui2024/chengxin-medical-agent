"""Group-level aggregation of CSV-derived rows.

Public API:
    aggregate(rows) -> list[dict]

``rows`` is an iterable of dict-like items that may carry string
``group`` / ``value`` fields.  Rows are ignored when the group name is
missing, non-string or blank after stripping, when a field is missing, or
when the value is not a plain decimal number (exponents, NaN, Infinity,
hex and other notations are rejected).
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

__all__ = ["aggregate"]

# Plain fixed-point decimal only: optional sign, digits, optional fraction.
_VALUE_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

_MEAN_QUANTUM = Decimal("0.01")
# Enough precision to divide exactly enough before the final rounding.
_DIVISION_PRECISION = 60


def _parse_value(raw):
    """Return a finite Decimal for a plain decimal string, else None."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not _VALUE_RE.match(text):
        return None
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite():
        return None
    return value


def _plain(value):
    """Exact plain decimal string with redundant trailing zeros removed."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.startswith("+"):
        text = text[1:]
    if text in ("", "-", "-0"):
        text = "0"
    return text


def _mean_text(total, count):
    with localcontext() as ctx:
        ctx.prec = _DIVISION_PRECISION
        mean = (total / Decimal(count)).quantize(
            _MEAN_QUANTUM, rounding=ROUND_HALF_UP
        )
    if mean == 0:  # normalise -0.00 and 0 to a single representation
        return "0.00"
    return format(mean, "f")


def aggregate(rows):
    """Aggregate ``rows`` by stripped group name.

    Returns a list sorted by group name, each item having exactly the keys
    ``group``, ``count``, ``min``, ``max`` and ``mean``.  ``min``/``max`` are
    exact trimmed decimals; ``mean`` is the Decimal mean rounded half up to
    two places, zero rendered uniformly as ``"0.00"``.
    """
    buckets = {}
    if rows is None:
        return []
    for row in rows:
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
        buckets.setdefault(group, []).append(value)

    result = []
    for group in sorted(buckets):
        values = buckets[group]
        total = sum(values, Decimal(0))
        result.append(
            {
                "group": group,
                "count": len(values),
                "min": _plain(min(values)),
                "max": _plain(max(values)),
                "mean": _mean_text(total, len(values)),
            }
        )
    return result
