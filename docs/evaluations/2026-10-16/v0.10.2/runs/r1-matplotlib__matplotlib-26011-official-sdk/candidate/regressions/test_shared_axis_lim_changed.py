"""Regression tests: lim_changed callbacks must fire on shared axes."""
import matplotlib.pyplot as plt


def test_shared_x_axis_emits_xlim_changed():
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True)
    seen = []
    ax2.callbacks.connect(
        "xlim_changed", lambda ax: seen.append(tuple(ax.get_xlim())))

    ax1.set_xlim(1, 5)

    assert seen == [(1.0, 5.0)]
    assert ax2.get_xlim() == (1.0, 5.0)
    plt.close(fig)


def test_shared_y_axis_emits_ylim_changed():
    fig, (ax1, ax2) = plt.subplots(1, 2, sharey=True)
    seen = []
    ax2.callbacks.connect(
        "ylim_changed", lambda ax: seen.append(tuple(ax.get_ylim())))

    ax1.set_ylim(2, 7)

    assert seen == [(2.0, 7.0)]
    assert ax2.get_ylim() == (2.0, 7.0)
    plt.close(fig)


def test_shared_axis_callbacks_fire_once_per_change():
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, sharex=True)
    counts = {"ax1": 0, "ax2": 0, "ax3": 0}
    for key, ax in (("ax1", ax1), ("ax2", ax2), ("ax3", ax3)):
        ax.callbacks.connect(
            "xlim_changed", lambda _ax, key=key: counts.__setitem__(
                key, counts[key] + 1))

    ax1.set_xlim(0, 10)

    assert counts == {"ax1": 1, "ax2": 1, "ax3": 1}
    plt.close(fig)


def test_emit_false_suppresses_shared_callbacks():
    fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True)
    seen = []
    ax1.callbacks.connect("xlim_changed", lambda ax: seen.append("ax1"))
    ax2.callbacks.connect("xlim_changed", lambda ax: seen.append("ax2"))

    ax1.set_xlim(3, 8, emit=False)

    assert seen == []
    assert ax1.get_xlim() == (3.0, 8.0)
    assert ax2.get_xlim() == (3.0, 8.0)
    plt.close(fig)
