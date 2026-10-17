"""Exact invoice total calculation.

Money amounts are ordinary non-negative decimal strings.  Every line amount is
formed with *exact* decimal arithmetic (never binary floats), all line amounts
are summed exactly, and rounding to two decimal places happens exactly once on
the final total using ROUND_HALF_UP.
"""

import re
from decimal import MAX_PREC, ROUND_HALF_UP, Decimal, localcontext

# Ordinary decimal string: digits, optionally followed by a fractional part.
# This deliberately rejects signs, exponents, whitespace and the special
# values NaN / Infinity.
_PRICE_RE = re.compile(r"\d+(?:\.\d+)?")

_CENT = Decimal("0.01")
_ZERO = Decimal(0)


def _parse_price(value):
    """Validate and exactly convert one price value."""
    if not isinstance(value, str) or _PRICE_RE.fullmatch(value) is None:
        raise ValueError("invalid price: {!r}".format(value))
    return Decimal(value)


def _parse_quantity(value):
    """Validate one quantity: a real int in [0, 1000]; bool is not an int."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid quantity: {!r}".format(value))
    if value < 0 or value > 1000:
        raise ValueError("quantity out of range: {!r}".format(value))
    return value


def total(lines):
    """Return the exact total of ``lines`` as a fixed two-decimal string.

    Each ``row`` must provide a ``price`` decimal string and an integer
    ``quantity``.  An empty input yields ``"0.00"``.  Invalid, non-finite or
    negative prices and invalid quantities raise :class:`ValueError`.

    A large local decimal precision is used so that the result is exact no
    matter what precision the caller's global decimal context currently has;
    the global context is never modified.
    """
    with localcontext() as ctx:
        ctx.prec = MAX_PREC
        amount = _ZERO
        for row in lines:
            price = _parse_price(row["price"])
            quantity = _parse_quantity(row["quantity"])
            amount += price * quantity
        # Round only the grand total, exactly once, half-up.
        result = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    return format(result, ".2f")
