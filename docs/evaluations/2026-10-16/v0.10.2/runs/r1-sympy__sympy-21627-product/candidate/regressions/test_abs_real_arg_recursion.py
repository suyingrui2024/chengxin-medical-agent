"""Regression test for the RecursionError raised while checking
``cosh(acos(-i + acosh(-g + i))).is_zero``.

``Abs.eval`` used to rewrite ``Abs(im(z))`` -- a real expression of
unknown sign -- as ``sqrt(im(z)*conjugate(im(z)))``.  ``signsimp`` made
the conjugate look structurally different, so the product collapsed to
``im(z)**2``; ``sqrt(im(z)**2)`` then evaluated back to ``Abs(im(z))``
(due to ``Pow._eval_power`` turning ``sqrt(real**2)`` into ``abs(real)``)
and the evaluation recursed without bound.
"""
from sympy import Abs, I, Symbol, acos, acosh, cosh, im, sympify
from sympy.simplify.simplify import signsimp


def test_is_zero_of_cosh_expression_does_not_recurse():
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    # decided to be unknown, but must not raise RecursionError
    assert expr.is_zero is None


def test_abs_of_unknown_sign_real_expr_stays_unevaluated():
    g = Symbol('g')
    x = Symbol('x')
    arg = im(acos(-I + acosh(-g + I)))
    assert arg.is_extended_real is True
    # the conjugate is the argument itself, only reordered by signsimp
    assert signsimp(arg.conjugate(), evaluate=False) != arg
    # so Abs must not be rewritten as a square root of a square
    result = Abs(arg)
    assert isinstance(result, Abs)
    assert result.args == (arg,)
    # a plain real symbol of unknown sign behaves the same way
    assert isinstance(Abs(im(x)), Abs)
