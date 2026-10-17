from sympy import I, Symbol, acos, acosh, cosh, im, sqrt, sympify


def test_is_zero_of_cosh_does_not_recurse():
    # reported: RecursionError while evaluating is_zero
    g = Symbol('g')
    expr = sympify("cosh(acos(-i + acosh(-g + i)))")
    assert expr.is_zero is not True


def test_abs_of_im_of_inverse_trig_does_not_recurse():
    g = Symbol('g')
    a = im(acos(-I + acosh(-g + I)))
    assert a.is_extended_real is True
    assert abs(a) == abs(a)


def test_sqrt_of_square_of_real_expression_still_simplifies():
    x = Symbol('x', real=True)
    y = Symbol('y')
    assert sqrt(im(y)**2) == abs(im(y))
    assert sqrt(x**2) == abs(x)
