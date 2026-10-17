"""Aggregate CSV-derived rows into per-group statistics."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

# Working precision for exact sums and the (possibly non-terminating) mean
# division.  Valid inputs are bounded, so this is far more than enough.
_WORK_PRECISION = 200
_QUANTUM = Decimal("0.01")


def _parse_value(value):
    """Return a finite ``Decimal`` for a decimal string, otherwise ``None``.

    Missing fields, non-strings, malformed decimals and NaN/Infinity all
    yield ``None`` so the caller can skip them.
    """
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def _format_number(number):
    """Exact plain-decimal string with redundant trailing zeros removed."""
    if number == 0:
        return "0"
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _format_mean(total, count):
    """ROUND_HALF_UP the exact mean to two decimals; zero prints as 0.00."""
    with localcontext() as ctx:
        ctx.prec = _WORK_PRECISION
        mean = (total / Decimal(count)).quantize(_QUANTUM, rounding=ROUND_HALF_UP)
    if mean == 0:
        return "0.00"
    return format(mean, "f")


def aggregate(rows):
    """Group rows by stripped ``group`` and return sorted statistics.

    Each row is a mapping with ``group`` and ``value`` string fields.  Rows
    with an empty group, a missing field or an invalid/non-finite value are
    ignored.  Returns a list sorted lexicographically by group name; every
    item has exactly ``group``, ``count``, ``min``, ``max`` and ``mean``.
    """
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            continue

        group = row.get("group")
        if not isinstance(group, str):
            continue
        group = group.strip()
        if not group:
            continue

        value = _parse_value(row.get("value"))
        if value is None:
            continue

        grouped.setdefault(group, []).append(value)

    results = []
    for group in sorted(grouped):
        values = grouped[group]
        with localcontext() as ctx:
            ctx.prec = _WORK_PRECISION
            total = sum(values, Decimal(0))
        results.append(
            {
                "group": group,
                "count": len(values),
                "min": _format_number(min(values)),
                "max": _format_number(max(values)),
                "mean": _format_mean(total, len(values)),
            }
        )
    return results
