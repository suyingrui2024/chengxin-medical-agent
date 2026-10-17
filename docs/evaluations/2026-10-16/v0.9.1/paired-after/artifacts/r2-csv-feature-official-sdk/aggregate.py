"""Aggregate rows of ``group``/``value`` strings.

``aggregate(rows)`` groups rows by their whitespace-stripped ``group`` and
returns one summary per group, sorted by group name.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

# Plain decimal notation only: optional sign, ASCII digits, optional single
# fractional part.  Scientific notation, NaN, Infinity, hex and the like are
# intentionally rejected.
_DECIMAL_RE = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")

_CENT = Decimal("0.01")


def _format_plain(value):
    """Render a Decimal as a plain decimal string with no trailing zeros."""
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _parse_value(raw):
    """Return a Decimal for a valid plain-decimal string, else ``None``."""
    if not isinstance(raw, str):
        return None
    if not _DECIMAL_RE.match(raw):
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None


def aggregate(rows):
    """Aggregate ``rows`` by group.

    Rows with a missing/empty group or a missing/invalid value are ignored.
    The result is a list sorted by group name; each item has exactly the keys
    ``group``, ``count``, ``min``, ``max`` and ``mean``.
    """
    groups = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        name = row.get("group")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue

        value = _parse_value(row.get("value"))
        if value is None:
            continue

        groups.setdefault(name, []).append(value)

    result = []
    for name in sorted(groups):
        values = groups[name]
        with localcontext() as ctx:
            ctx.prec = 60
            total = sum(values, Decimal(0))
            mean = (total / Decimal(len(values))).quantize(
                _CENT, rounding=ROUND_HALF_UP
            )
            mean_text = "0.00" if mean == 0 else format(mean, "f")

        result.append(
            {
                "group": name,
                "count": len(values),
                "min": _format_plain(min(values)),
                "max": _format_plain(max(values)),
                "mean": mean_text,
            }
        )

    return result
