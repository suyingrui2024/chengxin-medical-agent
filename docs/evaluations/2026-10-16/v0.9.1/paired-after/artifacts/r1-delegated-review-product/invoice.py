"""Invoice line totals with exact decimal arithmetic.

``total(lines)`` sums ``price * quantity`` over invoice lines.  Prices are
finite, non-negative plain-decimal strings (max 16 integer digits, 6 decimal
places); quantities are integers in ``0..1000`` (``bool`` is rejected).
Amounts are multiplied exactly in decimal and summed exactly; only the final
sum is rounded to two places with ``ROUND_HALF_UP``.  The caller's decimal
context precision is never modified and does not affect the result.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

PLACES = 6  # exact decimal places kept for every parsed price
SCALE = 10 ** PLACES
CENT = Decimal("0.01")

_PRICE_RE = re.compile(r"\A[0-9]{1,16}(?:\.[0-9]{1,6})?\Z")


def _parse_price(value):
    """Return the price as an exact integer number of 1e-6 units."""
    if not isinstance(value, str) or not _PRICE_RE.match(value):
        raise ValueError(f"invalid price: {value!r}")
    whole, _, frac = value.partition(".")
    return int(whole) * SCALE + int(frac.ljust(PLACES, "0") or "0")


def _parse_quantity(value):
    """Return a validated integer quantity in 0..1000."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid quantity: {value!r}")
    if not 0 <= value <= 1000:
        raise ValueError(f"quantity out of range: {value!r}")
    return value


def total(lines):
    """Return the exact line-sum rounded half-up to a 2-place string."""
    total_units = 0  # exact sum in 1e-6 units, no rounding per line
    for row in lines:
        if not isinstance(row, dict):
            raise ValueError(f"invalid line: {row!r}")
        try:
            price = row["price"]
            quantity = row["quantity"]
        except KeyError as exc:
            raise ValueError(f"missing field: {exc}") from None
        total_units += _parse_price(price) * _parse_quantity(quantity)

    with localcontext() as ctx:
        # Enough precision that scaleb/quantize never silently rounds.
        ctx.prec = max(28, len(str(total_units)) + PLACES + 2)
        amount = Decimal(total_units).scaleb(-PLACES)
        rounded = amount.quantize(CENT, rounding=ROUND_HALF_UP)
        return format(rounded, "f")
