"""Exact decimal invoice total.

Contract
--------
``total(lines) -> str``

* ``price``  : finite, non-negative plain decimal string, <= 16 integer digits
               and <= 6 fractional digits (``int`` / ``Decimal`` / finite
               ``float`` are also accepted, see ``_price_micros``).
* ``quantity``: int in ``[0, 1000]``; ``bool`` is NOT an integer.
* Each line is multiplied exactly, the products are summed exactly, and only
  the grand total is rounded, with ``ROUND_HALF_UP``, to 2 decimal places.
  Per-line rounding is never performed.
* The result is a fixed 2-decimal string; the empty list yields ``"0.00"``.

Implementation note: arithmetic is done on integers scaled by 10**6, so the
result is independent of ``decimal.getcontext().prec`` and the caller's global
precision is never modified.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

_SCALE = 1_000_000  # price is stored in millionths
_CENT = 10_000      # 1 cent == 10**4 millionths

_PRICE_RE = re.compile(r"\A([0-9]{1,16})(?:\.([0-9]{1,6}))?\Z")


def _price_micros(price: object) -> int:
    """Return ``price`` in millionths, or raise ``ValueError`` if illegal."""
    if isinstance(price, bool):  # bool is a subclass of int -> reject first
        raise ValueError(f"illegal price: {price!r}")
    if isinstance(price, Decimal):
        if not price.is_finite():
            raise ValueError(f"illegal price: {price!r}")
        text = format(price, "f")
    elif isinstance(price, float):
        if not math.isfinite(price):
            raise ValueError(f"illegal price: {price!r}")
        text = repr(price)
    elif isinstance(price, int):
        text = str(price)
    elif isinstance(price, str):
        text = price
    else:
        raise ValueError(f"illegal price: {price!r}")

    match = _PRICE_RE.match(text)
    if match is None:
        raise ValueError(f"illegal price: {price!r}")

    integer_part, fraction = match.group(1), match.group(2) or ""
    return int(integer_part) * _SCALE + int(fraction.ljust(6, "0") or "0")


def _quantity(value: object) -> int:
    """Return ``value`` as an int in ``[0, 1000]``, else raise ``ValueError``."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"illegal quantity: {value!r}")
    if not 0 <= value <= 1000:
        raise ValueError(f"illegal quantity: {value!r}")
    return value


def total(lines) -> str:
    """Exact sum of ``price * quantity`` over ``lines``, rounded once."""
    micros = 0
    for row in lines:
        micros += _price_micros(row["price"]) * _quantity(row["quantity"])

    cents, remainder = divmod(micros, _CENT)
    if remainder * 2 >= _CENT:  # ROUND_HALF_UP on the grand total only
        cents += 1
    return f"{cents // 100}.{cents % 100:02d}"
