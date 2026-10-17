"""Regression tests for RecursionError while evaluating Abs of a real argument.

See: "maximum recursion depth error when checking is_zero of cosh expression".
"""
from sympy import I, acos, acosh, cosh, im, sympify, symbols
from sympy.core.function import expand_mul


def test_issue_cosh_acos_acosh_is_zero():
    g = symbols('g')
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    # must not raise RecursionError
    assert expr.is_zero in (True, False, None)


def test_abs_of_real_im_expression_does_not_recurse():
    g = symbols('g')
    arg = im(acos(-I + acosh(-g + I)))
    assert arg.is_extended_real is True
    # previously recursed forever inside Abs.eval -> Pow._eval_power -> Abs
    assert str(sympify("Abs(im(acos(-i + acosh(-g + i))))")) is not None
    from sympy import Abs
    res = Abs(arg)
    assert res.is_extended_nonnegative is True


def test_expand_mul_of_real_arg_is_square():
    # the intermediate product that used to close the recursion cycle
    g = symbols('g')
    from sympy import signsimp, conjugate
    arg = im(acos(-I + acosh(-g + I)))
    conj = signsimp(conjugate(arg), evaluate=False)
    assert expand_mul(arg*conj) == arg**2
