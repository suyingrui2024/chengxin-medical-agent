"""Regression tests for callbacks on shared axes.

When the limits of an Axes are changed, ``xlim_changed`` / ``ylim_changed``
must also be emitted for every Axes sharing that axis, even though the
sibling limits are propagated internally without re-emitting recursively.
"""

from unittest import mock

import matplotlib.pyplot as plt


def test_xlim_changed_shared():
    fig, (ax1, ax2) = plt.subplots(1, 2, sharex=True)
    cb = mock.Mock()
    ax2.callbacks.connect('xlim_changed', cb)

    ax1.set_xlim(0, 5)

    assert ax1.get_xlim() == ax2.get_xlim() == (0, 5)
    cb.assert_called_once_with(ax2)
    assert cb.call_args.args[0] is ax2
    plt.close(fig)


def test_ylim_changed_shared():
    fig, (ax1, ax2) = plt.subplots(1, 2, sharey=True)
    cb = mock.Mock()
    ax2.callbacks.connect('ylim_changed', cb)

    ax1.set_ylim(-1, 1)

    assert ax1.get_ylim() == ax2.get_ylim() == (-1, 1)
    cb.assert_called_once_with(ax2)
    plt.close(fig)


def test_lim_changed_shared_multiple_siblings():
    fig, axs = plt.subplots(3, 1, sharex=True)
    cbs = [mock.Mock() for _ in axs[1:]]
    for ax, cb in zip(axs[1:], cbs):
        ax.callbacks.connect('xlim_changed', cb)

    axs[0].set_xlim(1, 2)

    for ax, cb in zip(axs[1:], cbs):
        assert ax.get_xlim() == (1, 2)
        cb.assert_called_once_with(ax)
    plt.close(fig)


def test_lim_changed_emit_false_not_shared():
    # A direct emit=False must not notify observers.
    fig, ax = plt.subplots()
    cb = mock.Mock()
    ax.callbacks.connect('xlim_changed', cb)

    ax.set_xlim(0, 5, emit=False)

    assert ax.get_xlim() == (0, 5)
    cb.assert_not_called()
    plt.close(fig)
