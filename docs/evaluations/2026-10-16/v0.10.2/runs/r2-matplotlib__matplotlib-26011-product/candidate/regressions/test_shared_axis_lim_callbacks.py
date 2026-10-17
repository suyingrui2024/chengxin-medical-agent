"""Regression tests: lim_changed callbacks must fire on shared axes.

When the limits of an axis are changed, every Axes sharing that axis also has
its limits changed; observers registered for ``xlim_changed`` / ``ylim_changed``
on those shared Axes must therefore be notified as well (exactly once).
"""
import matplotlib
import matplotlib.pyplot as plt
import pytest

matplotlib.use("agg")


@pytest.mark.parametrize("axis_name", ["x", "y"])
def test_shared_axis_lim_changed_is_emitted(axis_name):
    fig, axs = plt.subplots(2, 2, sharex=(axis_name == "x"),
                            sharey=(axis_name == "y"))
    ax1, ax2 = axs[0, 0], axs[1, 1]
    signal = f"{axis_name}lim_changed"
    seen = []
    ax1.callbacks.connect(signal, lambda ax, seen=seen: seen.append(ax))
    ax2.callbacks.connect(signal, lambda ax, seen=seen: seen.append(ax))

    getattr(ax1, f"set_{axis_name}lim")(3, 8)

    # The sibling order is not guaranteed (siblings are stored in a set), but
    # each shared Axes must be notified exactly once, and the modified one
    # first.
    assert seen[0] is ax1
    assert sorted(map(id, seen)) == sorted(map(id, [ax1, ax2]))
    assert getattr(ax1, f"get_{axis_name}lim")() == \
        getattr(ax2, f"get_{axis_name}lim")() == (3.0, 8.0)


def test_shared_axis_lim_changed_emitted_once_per_axes():
    fig, axs = plt.subplots(3, sharex=True)
    seen = []
    for ax in axs:
        ax.callbacks.connect("xlim_changed",
                             lambda ax, seen=seen: seen.append(ax))
    axs[0].set_xlim(1, 2)
    assert sorted(map(id, seen)) == sorted(map(id, list(axs)))
    axs[2].set_xlim(4, 5)
    assert sorted(map(id, seen)) == sorted(map(id, list(axs) * 2))
    assert seen[0] is axs[0] and seen[3] is axs[2]


def test_shared_axis_callback_sees_updated_limits():
    fig, (ax1, ax2) = plt.subplots(2, sharex=True)
    observed = []
    ax2.callbacks.connect("xlim_changed",
                          lambda ax: observed.append(ax.get_xlim()))
    ax1.set_xlim(3, 8)
    assert observed == [(3.0, 8.0)]


def test_unshared_axes_callbacks_are_not_emitted():
    fig, (ax1, ax2) = plt.subplots(2, sharex=False)
    seen = []
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append(ax))
    ax1.set_xlim(3, 8)
    assert seen == []


def test_emit_false_does_not_emit_on_shared_axes():
    fig, (ax1, ax2) = plt.subplots(2, sharex=True)
    seen = []
    ax1.callbacks.connect("xlim_changed", lambda ax: seen.append(ax))
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append(ax))
    ax1.set_xlim(3, 8, emit=False)
    assert seen == []
    assert ax1.get_xlim() == (3.0, 8.0)


def test_set_inverted_notifies_shared_axes():
    fig, (ax1, ax2) = plt.subplots(2, sharex=True)
    seen = []
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append(ax))
    ax1.set_xlim(0, 10)
    seen.clear()
    ax1.invert_xaxis()
    assert seen == [ax2]
