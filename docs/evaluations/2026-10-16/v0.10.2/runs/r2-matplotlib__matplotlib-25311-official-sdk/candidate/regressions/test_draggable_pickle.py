"""Regression tests for pickling figures with draggable legends/annotations.

Setting a legend or annotation draggable used to store a direct reference to
the (backend specific, often non-picklable) figure canvas on the draggable
helper object.  ``pickle.dumps(fig)`` therefore failed with errors such as
``TypeError: cannot pickle 'FigureCanvasQTAgg' object``.
"""
import pickle
import threading

import matplotlib.pyplot as plt


def _figure_with_unpicklable_canvas(*, legend=False, annotation=False):
    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [0, 1, 2], label="speed")
    helper = None
    if legend:
        leg = ax.legend()
        helper = leg.set_draggable(True)
    if annotation:
        ann = ax.annotate("note", (0.5, 0.5))
        helper = ann.draggable(True)
    # Figure.__getstate__ drops the canvas itself; only a stale reference kept
    # on the draggable helper would drag the canvas into the pickle.
    fig.canvas._unpicklable = threading.Lock()
    return fig, helper


def test_pickle_draggable_legend():
    fig, helper = _figure_with_unpicklable_canvas(legend=True)
    assert "canvas" not in vars(helper), (
        "draggable must not keep a direct reference to the canvas")
    loaded = pickle.loads(pickle.dumps(fig))
    loaded_leg = loaded.axes[0].get_legend()
    assert loaded_leg.get_draggable()
    # The helper must resolve the canvas through the (new) figure.
    assert loaded_leg._draggable.canvas is loaded.canvas


def test_pickle_draggable_annotation():
    fig, helper = _figure_with_unpicklable_canvas(annotation=True)
    assert "canvas" not in vars(helper)
    loaded = pickle.loads(pickle.dumps(fig))
    loaded_ann = loaded.axes[0].texts[0]
    assert loaded_ann._draggable is not None
    assert loaded_ann._draggable.canvas is loaded.canvas
