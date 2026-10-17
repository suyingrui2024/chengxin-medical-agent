"""Exact mass-unit conversion for plain decimal strings.

The public :func:`convert` entry point accepts a non-negative, finite plain
decimal string (or, as a convenience, an ``int``/``Decimal``) together with the
source and target unit (``kg``/``g``/``mg``) and returns the exact result as a
plain, non-scientific decimal string with insignificant trailing fractional
zeros removed.

The implementation deliberately performs *no* floating point arithmetic and
does *not* rely on (or mutate) the caller's :mod:`decimal` context.  Every unit
factor is a power of ten, so a conversion is just an integer exponent shift and
is therefore always exact, regardless of ``decimal.getcontext().prec``.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

__all__ = ["convert"]

# Number of decimal places (power of ten) each unit represents when expressed
# in grams: 1 kg = 10**3 g, 1 g = 10**0 g, 1 mg = 10**-3 g.
_UNIT_POWERS = {"kg": 3, "g": 0, "mg": -3}

# A non-negative "plain" decimal literal: no sign, no exponent, no NaN/Inf.
_PLAIN_DECIMAL_RE = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")


def _decompose(value):
    """Return ``(coefficient, exponent)`` with value == coefficient * 10**exponent.

    Validation is intentionally independent from any arithmetic context, so it
    works even when the caller has configured a tiny precision.
    """
    if isinstance(value, bool):
        raise ValueError("value must be a non-negative plain decimal string")

    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, str):
        if not _PLAIN_DECIMAL_RE.match(value):
            raise ValueError(
                "value must be a non-negative plain decimal string, got %r" % (value,)
            )
        decimal_value = Decimal(value)
    elif isinstance(value, float):
        # Floats are outside the documented contract; reject non-finite ones
        # and otherwise use the shortest exact decimal representation.
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        decimal_value = Decimal(repr(value))
    else:
        raise ValueError("value must be a non-negative plain decimal string")

    if not decimal_value.is_finite():
        raise ValueError("value must be finite")
    if decimal_value.is_signed() and decimal_value != 0:
        raise ValueError("value must be non-negative")

    _sign, digits, exponent = decimal_value.as_tuple()
    coefficient = int("".join(str(digit) for digit in digits))
    return coefficient, exponent


def _format_plain(coefficient, exponent):
    """Render ``coefficient * 10**exponent`` as a plain decimal string."""
    if coefficient == 0:
        return "0"

    digits = str(coefficient)

    if exponent >= 0:
        # Integer result: trailing zeros here are significant.
        return digits + "0" * exponent

    point = len(digits) + exponent
    if point > 0:
        text = digits[:point] + "." + digits[point:]
    else:
        text = "0." + "0" * (-point) + digits

    # Drop insignificant trailing fractional zeros (and a dangling point).
    text = text.rstrip("0")
    if text.endswith("."):
        text = text[:-1]
    return text


def convert(value, source, target):
    """Convert ``value`` from ``source`` to ``target`` exactly.

    Parameters
    ----------
    value:
        Non-negative, finite plain decimal string (``int``/``Decimal`` are also
        accepted).  ``NaN``, ``Infinity`` and negative values raise
        :class:`ValueError`.
    source, target:
        One of ``"kg"``, ``"g"`` or ``"mg"``.  Anything else raises
        :class:`ValueError`.

    Returns
    -------
    str
        Exact, non-scientific decimal string with insignificant trailing
        fractional zeros removed.
    """
    if not isinstance(source, str) or source not in _UNIT_POWERS:
        raise ValueError("invalid source unit: %r" % (source,))
    if not isinstance(target, str) or target not in _UNIT_POWERS:
        raise ValueError("invalid target unit: %r" % (target,))

    coefficient, exponent = _decompose(value)
    exponent += _UNIT_POWERS[source] - _UNIT_POWERS[target]
    return _format_plain(coefficient, exponent)
