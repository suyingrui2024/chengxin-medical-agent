import pickle
from io import BytesIO

import matplotlib.pyplot as plt


def _roundtrip(fig):
    buf = BytesIO()
    pickle.dump(fig, buf, pickle.HIGHEST_PROTOCOL)
    buf.seek(0)
    return pickle.load(buf)


def test_pickle_draggable_legend():
    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [0, 1, 2], label="speed")
    leg = ax.legend()
    leg.set_draggable(True)
    fig2 = _roundtrip(fig)
    assert fig2.axes[0].get_legend().get_draggable()


def test_pickle_draggable_annotation():
    fig, ax = plt.subplots()
    ann = ax.annotate("foo", (0.5, 0.5))
    ann.draggable(True)
    fig2 = _roundtrip(fig)
    assert fig2.axes[0].texts[0]._draggable is not None
