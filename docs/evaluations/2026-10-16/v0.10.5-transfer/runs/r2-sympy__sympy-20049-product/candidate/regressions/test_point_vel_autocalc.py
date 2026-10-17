"""Regression tests for automatic calculation of Point.vel().

Point.vel(frame) used to raise ValueError whenever the velocity had not been
set explicitly, even if the point's position relative to a point with a known
velocity was defined.  The velocity can be derived in that case from the
relative position vector.  These tests keep the original error behaviour for
points that cannot be related to any point of known velocity.
"""

from sympy.physics.vector import (Point, ReferenceFrame, dynamicsymbols)
from sympy.testing.pytest import raises


def test_issue_example_with_anchor():
    # The example from the report, with the reference point fixed in A.
    A = ReferenceFrame('A')
    q = dynamicsymbols('q')
    P = Point('P')
    Q = Point('Q')
    r = q * A.x + 2 * q * A.y
    Q.set_pos(P, r)
    P.set_vel(A, 0)
    assert Q.vel(A) == r.dt(A)
    assert Q.vel(A) == q.diff() * A.x + 2 * q.diff() * A.y

    # ... and the orientation case it should be consistent with.
    B = A.orientnew('B', 'Axis', (q, A.x))
    assert B.ang_vel_in(A) == q.diff() * A.x


def test_multi_hop_position_tree():
    q, r = dynamicsymbols('q r')
    N = ReferenceFrame('N')
    O = Point('O')
    P = Point('P')
    Q = Point('Q')
    O.set_vel(N, 0)
    Q.set_pos(O, r * N.x)
    P.set_pos(Q, q * N.y)
    assert Q.vel(N) == r.diff() * N.x
    assert P.vel(N) == r.diff() * N.x + q.diff() * N.y
    # The order in which the velocities are requested must not matter.
    P2 = Point('P2')
    Q2 = Point('Q2')
    P2.set_pos(Q2, q * N.y)
    Q2.set_pos(O, r * N.x)
    assert P2.vel(N) == r.diff() * N.x + q.diff() * N.y


def test_rotating_frame_relative_position():
    q = dynamicsymbols('q')
    N = ReferenceFrame('N')
    B = N.orientnew('B', 'Axis', (q, N.z))
    O = Point('O')
    P = Point('P')
    O.set_vel(N, 5 * N.x)
    P.set_pos(O, B.x)
    assert P.vel(N) == 5 * N.x + (B.x).dt(N)
    assert P.vel(N) == 5 * N.x + q.diff() * B.y


def test_explicit_velocity_wins():
    q = dynamicsymbols('q')
    N = ReferenceFrame('N')
    O = Point('O')
    P = Point('P')
    O.set_vel(N, 0)
    P.set_pos(O, q * N.x)
    P.set_vel(N, 3 * N.z)
    assert P.vel(N) == 3 * N.z


def test_no_velocity_information_raises():
    q = dynamicsymbols('q')
    N = ReferenceFrame('N')
    O = Point('O')
    P = Point('P')
    P.set_pos(O, q * N.x)
    raises(ValueError, lambda: P.vel(N))
    raises(ValueError, lambda: O.vel(N))
    # an unrelated point without any position information still raises
    Z = Point('Z')
    raises(ValueError, lambda: Z.vel(N))
    # a frame in which nothing is known also raises
    A = ReferenceFrame('A')
    O.set_vel(N, 0)
    raises(ValueError, lambda: P.vel(A))


def test_inconsistent_velocities_raise():
    q = dynamicsymbols('q')
    N = ReferenceFrame('N')
    O = Point('O')
    P = Point('P')
    Q = Point('Q')
    O.set_vel(N, 0)
    Q.set_pos(O, q * N.x)
    P.set_pos(Q, q * N.x)
    # O and P are both anchors but they disagree (0 != q').
    P.set_vel(N, q.diff() * N.x)
    raises(ValueError, lambda: Q.vel(N))


def test_consistent_duplicate_anchors():
    q = dynamicsymbols('q')
    N = ReferenceFrame('N')
    O = Point('O')
    P = Point('P')
    Q = Point('Q')
    O.set_vel(N, 0)
    Q.set_pos(O, q * N.x)
    P.set_pos(Q, 0)
    P.set_vel(N, q.diff() * N.x)
    assert Q.vel(N) == q.diff() * N.x
    assert P.vel(N) == q.diff() * N.x
