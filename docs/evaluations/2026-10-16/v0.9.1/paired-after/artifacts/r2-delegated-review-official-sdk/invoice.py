"""Exact decimal invoice total.

Each line is a mapping with a ``price`` string and an ``quantity`` integer.
Prices are finite, non-negative, ordinary decimal strings with at most 16
integer digits and 6 fractional digits; quantities are integers in [0, 1000]
(``bool`` is not accepted as an integer).

Amounts are multiplied exactly in decimal and summed without any per-line
rounding; only the final sum is rounded to two places using ROUND_HALF_UP.
The computation is exact even when the caller's decimal context has a small
precision (e.g. 8) and the global context is never modified.
"""

import decimal
import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

# finite non-negative ordinary decimal string, at most 16 integer digits and
# 6 fractional digits (sign, exponent and whitespace are rejected)
_PRICE_RE = re.compile(r"^(?:\d{1,16}(?:\.\d{1,6})?|\.\d{1,6})$")
_CENT = Decimal("0.01")


def _parse_price(value):
    if not isinstance(value, str):
        raise ValueError("invalid price: %r" % (value,))
    if _PRICE_RE.match(value) is None:
        raise ValueError("invalid price: %r" % (value,))
    price = Decimal(value)
    if not price.is_finite():
        raise ValueError("non-finite price: %r" % (value,))
    if price < 0:
        raise ValueError("negative price: %r" % (value,))
    return price


def _parse_quantity(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid quantity: %r" % (value,))
    if value < 0 or value > 1000:
        raise ValueError("quantity out of range: %r" % (value,))
    return value


def total(lines):
    """Return the exact invoice total as a fixed two-decimal string."""
    rows = list(lines)
    # Enough precision to keep every product and the whole sum exact, no
    # matter how small the caller's decimal precision is.
    precision = 50 + len(str(len(rows)))
    with localcontext(decimal.Context(prec=precision, rounding=ROUND_HALF_UP)):
        amount = Decimal(0)
        for row in rows:
            try:
                raw_price = row["price"]
                raw_quantity = row["quantity"]
            except (TypeError, KeyError):
                raise ValueError("invalid line: %r" % (row,))
            price = _parse_price(raw_price)
            quantity = _parse_quantity(raw_quantity)
            amount += price * quantity
        rounded = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
        return format(rounded, "f")
