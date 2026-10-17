"""Exact mass-unit conversion for plain decimal strings.

Supported units: kg, g, mg (all powers of ten relative to the gram), so every
conversion is an exact shift of the decimal exponent. No binary floating point
and no context-dependent Decimal division is used, which makes results
independent of the caller's decimal context (including prec=8).
"""

import re

_UNIT_EXP = {"kg": 3, "g": 0, "mg": -3}

# Plain decimal: 1-24 integer digits with an optional fractional part of up to
# 36 digits, or a bare ".5"-style fraction. No sign, no exponent.
_PLAIN = re.compile(r"^(?:(\d{1,24})(?:\.(\d{0,36}))?|\.(\d{1,36}))$")


def _parse(value):
    """Parse a non-negative finite plain decimal string into (significand, exp)."""
    if not isinstance(value, str):
        raise ValueError("value must be a decimal string, got %s" % (type(value).__name__,))
    match = _PLAIN.match(value)
    if match is None:
        raise ValueError("invalid decimal value: %r" % (value,))
    int_part, frac_part, bare_frac = match.group(1), match.group(2), match.group(3)
    if bare_frac is not None:
        int_part, frac_part = "", bare_frac
    frac_part = frac_part or ""
    digits = (int_part + frac_part) or "0"
    return int(digits), -len(frac_part)


def _format(significand, exponent):
    """Render significand * 10**exponent as an exact plain-decimal string."""
    if significand == 0:
        return "0"
    digits = str(significand)
    point = len(digits) + exponent
    if point >= len(digits):
        return digits + "0" * (point - len(digits))
    if point > 0:
        int_part, frac_part = digits[:point], digits[point:]
    else:
        int_part, frac_part = "0", "0" * (-point) + digits
    frac_part = frac_part.rstrip("0")
    return int_part + "." + frac_part if frac_part else int_part


def convert(value, source, target):
    """Convert ``value`` (plain decimal string) from ``source`` to ``target``.

    Returns an exact, non-scientific-notation string with insignificant
    fractional zeros removed. Raises ValueError for unknown units, negative
    values, NaN/Infinity, and any non-plain-decimal input.
    """
    try:
        src_exp = _UNIT_EXP[source]
    except (KeyError, TypeError):
        raise ValueError("unknown source unit: %r" % (source,))
    try:
        tgt_exp = _UNIT_EXP[target]
    except (KeyError, TypeError):
        raise ValueError("unknown target unit: %r" % (target,))
    significand, exponent = _parse(value)
    return _format(significand, exponent + src_exp - tgt_exp)
