"""Regression tests for calculating the velocity of a Point when possible."""

from sympy.physics.vector import (Point, ReferenceFrame, dynamicsymbols,
                                  init_vprinting)
from sympy.testing.pytest import raises

init_vprinting(pretty_print=False)


def test_vel_of_located_point_is_calculated():
    # The example from the issue, with the reference point at rest in A.
    q = dynamicsymbols('q')
    qd = dynamicsymbols('q', 1)
    A = ReferenceFrame('A')
    B = A.orientnew('B', 'Axis', (q, A.x))
    P = Point('P')
    Q = Point('Q')
    r = q * A.x + 2 * q * A.y
    Q.set_pos(P, r)
    P.set_vel(A, 0)
    assert Q.vel(A) == r.dt(A) == qd * A.x + 2 * qd * A.y
    # asking again gives the same answer
    assert Q.vel(A) == qd * A.x + 2 * qd * A.y
    assert B.ang_vel_in(A) == qd * A.x


def test_vel_of_point_fixed_in_rotating_frame_is_calculated():
    q = dynamicsymbols('q')
    qd = dynamicsymbols('q', 1)
    N = ReferenceFrame('N')
    B = N.orientnew('B', 'Axis', [q, N.z])
    O = Point('O')
    P = O.locatenew('P', 10 * B.x)
    O.set_vel(N, 5 * N.x)
    # 2 point theorem, done automatically
    assert P.vel(N) == 5 * N.x + 10 * qd * B.y


def test_vel_is_calculated_through_intermediate_points():
    q, q2 = dynamicsymbols('q q2')
    qd, q2d = dynamicsymbols('q q2', 1)
    N = ReferenceFrame('N')
    O = Point('O')
    P = O.locatenew('P', q * N.x)
    Q = P.locatenew('Q', q2 * N.y)
    O.set_vel(N, 0)
    assert P.vel(N) == qd * N.x
    assert Q.vel(N) == qd * N.x + q2d * N.y
    # the velocity of the intermediate point is not needed to get Q's one
    assert Q.pos_from(O) == q * N.x + q2 * N.y


def test_vel_in_frame_of_position_vector_is_calculated():
    # The position vector may be expressed in a frame whose angular velocity
    # is known, the velocity of the reference point must be given in the
    # requested frame as well.
    q = dynamicsymbols('q')
    qd = dynamicsymbols('q', 1)
    N = ReferenceFrame('N')
    B = N.orientnew('B', 'Axis', [q, N.z])
    O = Point('O')
    P = O.locatenew('P', 10 * B.x)
    O.set_vel(N, 5 * N.x)
    assert P.vel(N) == 5 * N.x + 10 * qd * B.y
    assert P.vel(N).diff(qd, N, var_in_dcm=False) == 10 * B.y


def test_vel_still_raises_when_not_calculable():
    N = ReferenceFrame('N')
    A = ReferenceFrame('A')
    p = Point('p')
    q = dynamicsymbols('q')
    # no information at all: nothing can be calculated
    raises(ValueError, lambda: p.vel(N))
    # isolated point with a velocity in an unrelated frame
    p.set_vel(A, q * A.x)
    raises(ValueError, lambda: p.vel(N))
    # position is known but no velocity of any related point is known
    O = Point('O')
    P = O.locatenew('P', q * N.x)
    raises(ValueError, lambda: P.vel(N))
    # mutually related points without velocities do not recurse forever
    raises(ValueError, lambda: O.vel(N))


def test_acc_of_located_point_is_calculated():
    q = dynamicsymbols('q')
    qd = dynamicsymbols('q', 1)
    qdd = dynamicsymbols('q', 2)
    N = ReferenceFrame('N')
    O = Point('O')
    P = O.locatenew('P', q * N.x)
    O.set_vel(N, 0)
    assert O.acc(N) == 0
    assert P.acc(N) == qdd * N.x
    # acceleration that is set explicitly is still returned
    P.set_acc(N, 3 * N.y)
    assert P.acc(N) == 3 * N.y
