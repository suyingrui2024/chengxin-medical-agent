"""Aggregate CSV-style rows into per-group statistics.

``aggregate(rows)`` accepts a list of dictionaries that are expected to have
``group`` and ``value`` string fields.  Rows with an empty (after stripping)
group name, a missing field, or a value that is not a plain finite decimal are
ignored.
"""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

__all__ = ["aggregate"]

# A plain base-10 decimal: optional sign, digits with an optional fractional
# part (or a bare ".5"/"5." form).  Scientific notation such as "1e3" and the
# special values "NaN"/"Infinity" do not match.
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

_CENT = Decimal("0.01")


def _parse_value(raw):
    """Return a finite ``Decimal`` for *raw*, or ``None`` if it is not valid."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not _DECIMAL_RE.match(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return value


def _plain(value):
    """Render *value* as a plain decimal string without redundant zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0", "+0", "-", "+"):
        return "0"
    return text


def aggregate(rows):
    """Group *rows* by stripped group name and return ordered statistics.

    The result is sorted by group name and each entry has exactly the keys
    ``group``, ``count``, ``min``, ``max`` and ``mean``.
    """
    groups = {}

    with localcontext() as ctx:
        ctx.prec = 100

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

            entry = groups.get(group)
            if entry is None:
                groups[group] = {
                    "min": value,
                    "max": value,
                    "total": value,
                    "count": 1,
                }
            else:
                if value < entry["min"]:
                    entry["min"] = value
                if value > entry["max"]:
                    entry["max"] = value
                entry["total"] += value
                entry["count"] += 1

        result = []
        for group in sorted(groups):
            entry = groups[group]
            mean = (entry["total"] / Decimal(entry["count"])).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            if mean == 0:
                mean = abs(mean)
            result.append(
                {
                    "group": group,
                    "count": entry["count"],
                    "min": _plain(entry["min"]),
                    "max": _plain(entry["max"]),
                    "mean": format(mean, "f"),
                }
            )

    return result
