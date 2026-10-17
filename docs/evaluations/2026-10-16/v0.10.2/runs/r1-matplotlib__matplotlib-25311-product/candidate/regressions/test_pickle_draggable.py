"""Regression tests: figures with draggable legends/annotations can be pickled.

See the bug report "Unable to pickle figure with draggable legend": pickling a
figure whose legend (or annotation) was made draggable raised
``TypeError: cannot pickle 'FigureCanvasQTAgg' object`` because the draggable
helper kept a hard reference to the canvas.
"""

import pickle

import pytest

from matplotlib.legend import DraggableLegend
from matplotlib.offsetbox import DraggableAnnotation
import matplotlib.pyplot as plt


def _roundtrip(fig):
    return pickle.loads(pickle.dumps(fig))


def test_pickle_draggable_legend():
    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [40, 43, 45], label="speed")
    leg = ax.legend()
    draggable = leg.set_draggable(True)
    assert isinstance(draggable, DraggableLegend)
    assert leg.get_draggable()

    fig2 = _roundtrip(fig)
    leg2 = fig2.axes[0].get_legend()
    assert leg2 is not None
    assert leg2.get_draggable()


def test_pickle_draggable_annotation():
    fig, ax = plt.subplots()
    ann = ax.annotate("foo", (0.5, 0.5))
    draggable = ann.draggable(True)
    assert isinstance(draggable, DraggableAnnotation)

    fig2 = _roundtrip(fig)
    ann2 = fig2.axes[0].texts[0]
    # NB: Annotation.draggable() toggles, so check the attribute.
    assert ann2._draggable is not None
    assert ann2._draggable.canvas is fig2.canvas


def test_pickle_draggable_offsetbox():
    from matplotlib.offsetbox import AnchoredOffsetbox, DraggableOffsetBox, \
        TextArea

    fig, ax = plt.subplots()
    box = AnchoredOffsetbox(
        "upper right", child=TextArea("hi"), pad=0.1, borderpad=0.1)
    ax.add_artist(box)
    draggable = DraggableOffsetBox(box, box, use_blit=False)
    box.set_picker(True)

    fig2 = _roundtrip(fig)
    box2 = fig2.axes[0].artists[0]
    assert isinstance(box2, AnchoredOffsetbox)
    assert draggable.canvas is fig.canvas


def test_unpickled_draggable_uses_new_canvas():
    """The draggable helper must follow the figure's canvas, not hold one."""
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], label="speed")
    leg = ax.legend()
    leg.set_draggable(True)
    assert leg._draggable.canvas is fig.canvas

    fig2 = _roundtrip(fig)
    leg2 = fig2.axes[0].get_legend()
    assert leg2._draggable.canvas is fig2.canvas
    assert fig2.canvas is not fig.canvas

    # Disconnecting (which relies on the canvas) still works after unpickling.
    leg2.set_draggable(False)
    assert not leg2.get_draggable()


@pytest.mark.parametrize("use_blit", [True, False])
def test_draggable_legend_interactive_roundtrip(use_blit):
    """A pick event on the unpickled figure still drags the legend."""
    from matplotlib.backend_bases import MouseEvent, PickEvent

    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [40, 43, 45], label="speed")
    leg = ax.legend(loc="upper left")
    leg.set_draggable(True, use_blit=use_blit)

    fig2 = _roundtrip(fig)
    canvas2 = fig2.canvas
    leg2 = fig2.axes[0].get_legend()

    x, y = canvas2.get_width_height()[0] // 2, \
        canvas2.get_width_height()[1] // 2
    mouse = MouseEvent("button_press_event", canvas2, x, y, button=1)
    PickEvent("pick_event", canvas2, mouse, leg2)._process()
    assert leg2._draggable.got_artist
