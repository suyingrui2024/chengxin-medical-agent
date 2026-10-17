"""Exact invoice totals.

``total(lines)`` accepts an iterable of mappings with a ``price`` (a finite,
non-negative, plain decimal string) and an integer ``quantity``.  Each amount
is computed with exact decimal multiplication, the amounts are summed, and
*only the final sum* is rounded to two decimal places with ``ROUND_HALF_UP``.
"""

import re
from decimal import ROUND_HALF_UP, Decimal, localcontext

_CENT = Decimal("0.01")

# Plain (non-scientific) non-negative decimal with at most 16 integer digits
# and at most 6 fractional digits.  A leading integer part is optional so that
# forms like ".5" are still accepted, but at least one digit is required.
_PRICE_RE = re.compile(r"^(?=.*\d)\d{0,16}(?:\.\d{1,6})?\Z")


def _parse_price(value):
    """Validate and return *value* as a finite, non-negative Decimal."""
    if not isinstance(value, str):
        raise ValueError(f"price must be a decimal string, got {type(value).__name__}")
    if _PRICE_RE.match(value) is None:
        raise ValueError(f"invalid price: {value!r}")
    price = Decimal(value)
    if not price.is_finite() or price < 0:
        raise ValueError(f"price must be finite and non-negative: {value!r}")
    return price


def _parse_quantity(value):
    """Validate and return *value* as an integer in ``[0, 1000]``.

    ``bool`` is explicitly rejected even though it is a subclass of ``int``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"quantity must be an integer, got {value!r}")
    if value < 0 or value > 1000:
        raise ValueError(f"quantity out of range [0, 1000]: {value!r}")
    return value


def total(lines) -> str:
    """Return the exact invoice total as a fixed two-decimal string."""
    try:
        iterator = iter(lines)
    except TypeError as exc:
        raise ValueError("lines must be an iterable of invoice lines") from exc

    parsed = []
    for row in iterator:
        try:
            price = _parse_price(row["price"])
            quantity = _parse_quantity(row["quantity"])
        except (KeyError, TypeError, IndexError) as exc:
            raise ValueError(f"invalid invoice line: {row!r}") from exc
        parsed.append((price, quantity))

    # Size a local precision large enough to hold every product and the exact
    # sum: the widest price in significant digits, plus up to four integer
    # digits introduced by the quantity, plus the digits of the line count.
    width = 1
    for price, _quantity in parsed:
        width = max(width, len(price.as_tuple().digits))
    precision = width + 6 + len(str(max(1, len(parsed))))

    # A local context keeps exactness even when the caller's context has a tiny
    # precision (e.g. 8); the global context is never touched.
    with localcontext() as ctx:
        ctx.prec = precision
        exact_total = Decimal(0)
        for price, quantity in parsed:
            exact_total += price * quantity
        rounded = exact_total.quantize(_CENT, rounding=ROUND_HALF_UP)

    return format(rounded, "f")
