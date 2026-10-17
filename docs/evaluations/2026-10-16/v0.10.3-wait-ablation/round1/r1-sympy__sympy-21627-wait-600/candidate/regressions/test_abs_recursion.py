"""Regression tests for the RecursionError triggered by asking for
``is_zero`` of ``cosh(acos(-i + acosh(-g + i)))``.

The recursion happened while evaluating ``Abs(im(...))``: the conjugate of
the argument was rewritten by ``signsimp(..., evaluate=False)`` into a
structurally different (but equal) expression, so ``Abs.eval`` built
``sqrt(arg*conj) == sqrt(arg**2)`` which asks again for ``Abs(arg)`` via
``Pow._eval_power``.
"""

from sympy import Abs, im, sqrt, sympify
from sympy.abc import g, i


def test_is_zero_of_cosh_expression_is_not_recursive():
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    assert expr.is_zero is None


def test_abs_of_real_imaginary_part_terminates():
    arg = sympify("acos(-i + acosh(-g + i))")
    # im(...) is extended real, so Abs(im(...)) cannot be rewritten as a
    # square root of a product any further
    assert Abs(im(arg)) == Abs(im(arg))
    assert sqrt(im(arg)**2) == Abs(im(arg))
