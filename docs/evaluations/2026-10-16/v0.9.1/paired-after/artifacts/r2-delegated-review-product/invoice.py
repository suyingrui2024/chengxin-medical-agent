"""Invoice total: exact decimal arithmetic, single rounding at the end.

Rules implemented:
  * ``price`` is a *finite, non-negative, plain decimal string*: at most 16
    integer digits and at most 6 fractional digits (no sign, no exponent,
    no ``nan``/``inf``).
  * ``quantity`` is a real integer (``bool`` is rejected) in ``0..1000``.
  * Amounts are multiplied exactly in decimal, summed exactly, and only the
    *total* is rounded once, using ``ROUND_HALF_UP`` to 2 decimal places.
    Per-line rounding is never performed.
  * The result is a fixed 2-decimal string; an empty input yields ``"0.00"``.
  * Works correctly even when the caller's ``decimal`` context precision is
    small (e.g. 8) and never mutates the global context.
"""

from decimal import Decimal, ROUND_HALF_UP, localcontext
import re

#: at most 16 integer digits and at most 6 fractional digits
_PRICE_RE = re.compile(r"\A(?:0|[0-9]{1,16})(?:\.[0-9]{1,6})?\Z")

#: prices are stored as integers scaled by 10**6 -> exact integer arithmetic
_SCALE = 6
_SCALE_FACTOR = 10 ** _SCALE
_CENT = Decimal("0.01")


def _parse_price(value):
    """Return the price as an exact integer scaled by 10**6."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("price must be a decimal string")
    if not _PRICE_RE.match(value):
        raise ValueError("price must be a finite non-negative plain decimal string")
    int_part, _, frac_part = value.partition(".")
    frac_part = (frac_part + "0" * _SCALE)[:_SCALE]
    return int(int_part) * _SCALE_FACTOR + int(frac_part)


def _parse_quantity(value):
    """Return the quantity as an ``int`` in ``0..1000`` (``bool`` rejected)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("quantity must be an integer")
    if not 0 <= value <= 1000:
        raise ValueError("quantity must be between 0 and 1000")
    return value


def total(lines):
    """Sum ``price * quantity`` over ``lines`` exactly and format the total."""
    scaled_total = 0
    for index, row in enumerate(lines or ()):
        try:
            raw_price = row["price"]
            raw_quantity = row["quantity"]
        except (TypeError, KeyError, IndexError):
            raise ValueError("line %d: missing price or quantity" % index) from None
        price = _parse_price(raw_price)
        quantity = _parse_quantity(raw_quantity)
        scaled_total += price * quantity

    # Build the exact decimal from the scaled integer (no context rounding),
    # then quantize once with ROUND_HALF_UP under a local context.
    sign = "-" if scaled_total < 0 else ""
    magnitude = abs(scaled_total)
    exact = Decimal(
        "%s%d.%0*d" % (sign, magnitude // _SCALE_FACTOR, _SCALE, magnitude % _SCALE_FACTOR)
    )
    with localcontext() as ctx:
        ctx.prec = max(len(str(magnitude)) + 4, 28)
        return format(exact.quantize(_CENT, rounding=ROUND_HALF_UP), "f")
