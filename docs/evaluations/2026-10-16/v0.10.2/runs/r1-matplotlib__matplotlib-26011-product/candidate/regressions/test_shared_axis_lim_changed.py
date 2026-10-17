"""Regression tests: ``*lim_changed`` must fire on shared axes.

When limits are changed on one Axes, sibling Axes sharing that axis have
their limits updated as well; their registered ``xlim_changed`` /
``ylim_changed`` callbacks must be notified of that change.
"""
import matplotlib
matplotlib.use("Agg")

from matplotlib.figure import Figure


def _new_figure():
    return Figure()


def test_xlim_changed_emitted_on_shared_axis():
    fig = _new_figure()
    ax1 = fig.add_subplot(121)
    ax2 = fig.add_subplot(122, sharex=ax1)

    seen = []
    ax1.callbacks.connect("xlim_changed", lambda ax: seen.append(("ax1", ax)))
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append(("ax2", ax)))

    ax1.set_xlim(1, 2)

    assert ax1.get_xlim() == (1, 2)
    assert ax2.get_xlim() == (1, 2)
    # Both axes (the changed one and its sibling) must be notified.
    assert seen == [("ax1", ax1), ("ax2", ax2)]


def test_ylim_changed_emitted_on_shared_axis():
    fig = _new_figure()
    ax1 = fig.add_subplot(121)
    ax2 = fig.add_subplot(122, sharey=ax1)

    seen = []
    ax1.callbacks.connect("ylim_changed", lambda ax: seen.append(("ax1", ax)))
    ax2.callbacks.connect("ylim_changed", lambda ax: seen.append(("ax2", ax)))

    ax1.set_ylim(3, 4)

    assert ax1.get_ylim() == (3, 4)
    assert ax2.get_ylim() == (3, 4)
    assert seen == [("ax1", ax1), ("ax2", ax2)]


def test_lim_changed_emitted_for_all_siblings_exactly_once():
    fig = _new_figure()
    ax1 = fig.add_subplot(131)
    ax2 = fig.add_subplot(132, sharex=ax1)
    ax3 = fig.add_subplot(133, sharex=ax1)
    ax4 = fig.add_subplot(133)  # not sharing x

    counts = {ax: 0 for ax in (ax1, ax2, ax3, ax4)}
    for ax in counts:
        ax.callbacks.connect(
            "xlim_changed", lambda changed, ax=ax, counts=counts:
            counts.__setitem__(ax, counts[ax] + 1))

    ax2.set_xlim(10, 20)

    assert ax1.get_xlim() == (10, 20)
    assert ax2.get_xlim() == (10, 20)
    assert ax3.get_xlim() == (10, 20)
    # Every shared axes gets exactly one notification...
    assert counts[ax1] == 1
    assert counts[ax2] == 1
    assert counts[ax3] == 1
    # ...and unrelated axes are left alone (autoscaling may have run before,
    # so just check that setting the sibling limits did not notify it again).
    assert counts[ax4] == 0


def test_lim_changed_not_emitted_with_emit_false():
    fig = _new_figure()
    ax1 = fig.add_subplot(121)
    ax2 = fig.add_subplot(122, sharex=ax1)

    seen = []
    ax1.callbacks.connect("xlim_changed", lambda ax: seen.append("ax1"))
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append("ax2"))

    ax1.set_xlim(5, 6, emit=False)

    assert ax1.get_xlim() == (5, 6)
    # emit=False means "do not notify", so neither axis is notified and the
    # limits are not propagated to the sibling (pre-existing behavior).
    assert seen == []
    assert ax2.get_xlim() == (0, 1)


def test_lim_changed_emitted_on_shared_axis_after_sharex_call():
    fig = _new_figure()
    ax1 = fig.add_subplot(121)
    ax2 = fig.add_subplot(122)
    ax2.sharex(ax1)

    received = []
    ax2.callbacks.connect("xlim_changed", received.append)

    ax1.set_xlim(-1, 1)

    assert received == [ax2]
    assert ax2.get_xlim() == (-1, 1)
