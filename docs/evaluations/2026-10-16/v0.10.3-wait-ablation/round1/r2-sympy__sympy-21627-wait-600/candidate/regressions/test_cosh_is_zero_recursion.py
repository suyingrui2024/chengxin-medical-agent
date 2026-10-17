"""Regression test: no RecursionError for assumptions of cosh expressions.

See bug: ``cosh(acos(-i + acosh(-g + i))).is_zero`` raised
``RecursionError`` because ``Abs.eval`` kept calling ``sqrt(arg*conj(arg))``
for a real argument whose conjugate was rewritten (by ``signsimp``) into an
equivalent but structurally different form, so ``Abs`` never settled and
``Pow``/``Abs`` recursed forever.
"""

from sympy import Abs, I, acos, acosh, cosh, im, pi, sympify
from sympy.abc import x


def test_cosh_is_zero_no_recursion():
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    # must not raise RecursionError; cosh is zero only on the imaginary axis
    assert expr.is_zero is not True
    assert expr.is_zero in (None, False)
    # related assumption queries took the same recursive path
    assert expr.is_positive is not True
    assert expr.is_nonnegative is not True


def test_abs_of_imaginary_part_conjugate():
    # Abs(im(...)) used to recurse forever inside Abs.eval
    z = im(acos(-I + acosh(-sympify("g") + I)))
    assert Abs(z) == Abs(z)
    assert Abs(z) is not None


def test_abs_real_argument_unchanged():
    # plain real arguments keep the previous (unevaluated) behaviour
    assert Abs(x) == Abs(x)
    assert Abs(-3) == 3
    assert Abs(3) == 3


def test_cosh_known_zero_and_nonzero():
    assert cosh(0).is_zero is True
    assert cosh(I*pi/2).is_zero is True
    assert cosh(1).is_zero is False
