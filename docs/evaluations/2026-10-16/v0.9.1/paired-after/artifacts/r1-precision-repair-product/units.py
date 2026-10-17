"""Exact mass-unit conversion for kg / g / mg.

Values are plain (non-scientific) decimal strings: at most 24 integer digits
and at most 36 fractional digits, non-negative and finite.  Arithmetic is done
with :class:`decimal.Decimal` inside a private ``localcontext`` so that the
caller's Decimal context (precision, rounding, traps) is never modified, yet
the result is exact regardless of the caller's precision setting.
"""

import re
from decimal import Context, Decimal, localcontext

# Exponent of each unit expressed in grams: kg = 10**3 g, g = 10**0 g,
# mg = 10**-3 g.  A conversion is therefore multiply by 10**(exp[src]-exp[tgt]).
_UNIT_EXPONENT = {"kg": 3, "g": 0, "mg": -3}

_PLAIN_DECIMAL = re.compile(r"\A(?:0|[0-9]{1,24})(?:\.[0-9]{1,36})?\Z")

# 24 integer + 36 fractional digits plus the widest unit shift (+/- 6) -> 66.
# 120 digits of head-room keeps every operation exact.
_WORKING_PRECISION = 120


def _parse(value):
    """Validate *value* and return an exact Decimal."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(
            "value must be a plain decimal string, got %r" % (type(value).__name__,)
        )
    text = value if isinstance(value, str) else str(value)
    if not _PLAIN_DECIMAL.match(text):
        raise ValueError("invalid value: %r" % (text,))
    # Decimal(str) is exact and ignores the ambient context for construction.
    return Decimal(text)


def _to_plain_string(number):
    """Render *number* without exponent and without insignificant zeros."""
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def convert(value, source, target):
    """Convert *value* from *source* unit to *target* unit.

    Returns an exact, non-scientific-notation decimal string with trailing
    fractional zeros removed.  Raises ``ValueError`` for malformed values
    (negative numbers, ``NaN``, ``Infinity``, scientific notation, too many
    digits) and for unknown units.
    """
    if source not in _UNIT_EXPONENT:
        raise ValueError("unknown source unit: %r" % (source,))
    if target not in _UNIT_EXPONENT:
        raise ValueError("unknown target unit: %r" % (target,))

    amount = _parse(value)
    shift = _UNIT_EXPONENT[source] - _UNIT_EXPONENT[target]

    with localcontext(Context(prec=_WORKING_PRECISION)):
        # Only an exact power of ten is applied; no rounding can occur.
        result = amount.scaleb(shift) if shift else amount
        return _to_plain_string(result)
