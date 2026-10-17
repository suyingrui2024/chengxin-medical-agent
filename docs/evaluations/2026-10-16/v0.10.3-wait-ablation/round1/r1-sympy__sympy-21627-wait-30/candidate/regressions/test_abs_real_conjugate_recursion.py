"""Regression tests for the RecursionError raised while checking
``is_zero`` of ``cosh(acos(-i + acosh(-g + i)))``.

The infinite recursion was caused by ``Abs.eval`` rewriting the absolute
value of a real expression as ``sqrt(arg*conjugate(arg))``: for a real
argument this gives ``sqrt(arg**2)``, which evaluates back to ``Abs(arg)``.
"""

from sympy import Abs, acos, acosh, cosh, im, symbols, sympify


def test_is_zero_of_cosh_expression_does_not_recurse():
    # reported case: expr.is_zero raised RecursionError
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    result = expr.is_zero
    assert result in (True, False, None)


def test_abs_of_imaginary_part_is_not_rewritten_as_sqrt():
    i, g = symbols('i g')
    z = acos(-i + acosh(-g + i))
    a = Abs(im(z))
    # must stay unevaluated instead of recursing through sqrt(im(z)**2)
    assert a.func is Abs
    assert a.args == (im(z),)


def test_abs_of_real_expression_with_unknown_sign_stays_unevaluated():
    x = symbols('x', real=True)
    assert Abs(x) == Abs(x, evaluate=False)
    assert Abs(x).args == (x,)
    # known sign is still simplified
    assert Abs(x**2) == x**2
    assert Abs(-x**2) == x**2
