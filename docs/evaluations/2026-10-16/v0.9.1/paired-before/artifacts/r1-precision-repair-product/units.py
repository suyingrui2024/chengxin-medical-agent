"""Exact decimal unit conversion between kg / g / mg.

Public API
----------
convert(value, source, target) -> str

``value`` must be a plain (non-scientific) decimal string, non-negative and
finite, with at most 24 integer digits and at most 36 fractional digits.
``source`` / ``target`` must be one of ``"kg"``, ``"g"``, ``"mg"``.

The result is an exact, non-scientific decimal string with insignificant
trailing fractional zeros removed.  Conversion uses only exact powers of ten,
so the caller's :mod:`decimal` context (including a precision as small as 8)
can never change the answer, and the caller's context is never mutated.
"""

import re
from decimal import Decimal, localcontext

# Exact magnitudes expressed in milligrams: kg = 10**6 mg, g = 10**3 mg, mg = 1 mg.
_EXPONENT_IN_MG = {"kg": 6, "g": 3, "mg": 0}

# Plain decimal: digits, optional single fractional part.  No sign, no
# exponent, no whitespace, no NaN/Infinity (those simply fail to match).
_PLAIN_DECIMAL = re.compile(r"\A(\d+)(?:\.(\d+))?\Z")

_MAX_INTEGER_DIGITS = 24
_MAX_FRACTION_DIGITS = 36

# Far more than the 60 significant digits an accepted input can carry, so the
# scaling below is always exact while the caller's context stays at prec=8.
_WORKING_PRECISION = 200


def _validate_unit(unit, name):
    if unit not in _EXPONENT_IN_MG:
        raise ValueError(
            "invalid %s unit %r: expected one of %s"
            % (name, unit, sorted(_EXPONENT_IN_MG))
        )


def _parse_value(value):
    if not isinstance(value, str):
        raise ValueError("value must be a decimal string, got %r" % (type(value).__name__,))

    match = _PLAIN_DECIMAL.match(value)
    if match is None:
        raise ValueError(
            "value %r is not a plain non-negative finite decimal string" % (value,)
        )

    integer_part, fraction_part = match.group(1), match.group(2) or ""
    if len(integer_part) > _MAX_INTEGER_DIGITS:
        raise ValueError(
            "value has %d integer digits, maximum is %d"
            % (len(integer_part), _MAX_INTEGER_DIGITS)
        )
    if len(fraction_part) > _MAX_FRACTION_DIGITS:
        raise ValueError(
            "value has %d fractional digits, maximum is %d"
            % (len(fraction_part), _MAX_FRACTION_DIGITS)
        )
    return Decimal(value)


def _format_exact(result):
    """Render a Decimal without scientific notation, stripping insignificant zeros."""
    text = format(result, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text


def convert(value, source, target):
    _validate_unit(source, "source")
    _validate_unit(target, "target")
    amount = _parse_value(value)

    shift = _EXPONENT_IN_MG[source] - _EXPONENT_IN_MG[target]
    with localcontext() as ctx:
        ctx.prec = _WORKING_PRECISION
        # Exact power of ten: no rounding can occur at this precision.
        result = amount * (Decimal(1).scaleb(shift))
    return _format_exact(result)
