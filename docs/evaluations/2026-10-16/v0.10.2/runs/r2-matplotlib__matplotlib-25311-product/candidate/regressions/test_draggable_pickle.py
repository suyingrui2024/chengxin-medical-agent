"""Regression tests: figures with draggable legends/annotations must pickle."""
import pickle

import numpy as np
import pytest

import matplotlib.pyplot as plt
from matplotlib.backend_bases import MouseEvent, PickEvent


def _make_figure():
    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [40, 43, 45], label="speed")
    leg = ax.legend()
    leg.set_draggable(True)
    ann = ax.annotate("note", (0.5, 0.5))
    ann.draggable()
    fig.canvas.draw()
    return fig, leg, ann


def test_pickle_draggable_legend_and_annotation():
    fig, _, _ = _make_figure()
    restored = pickle.loads(pickle.dumps(fig))
    assert restored.axes[0].get_legend().get_draggable()


def test_pickled_draggable_legend_still_drags():
    fig, leg, _ = _make_figure()
    restored = pickle.loads(pickle.dumps(fig))
    leg = restored.axes[0].get_legend()
    dr = leg._draggable
    assert dr.canvas is restored.canvas

    press = MouseEvent("button_press_event", restored.canvas, 100, 100,
                       button=1)
    restored.canvas.callbacks.process(
        "pick_event", PickEvent("pick_event", restored.canvas, press, leg))
    assert dr.got_artist

    before = leg._loc
    restored.canvas.callbacks.process(
        "motion_notify_event",
        MouseEvent("motion_notify_event", restored.canvas, 150, 130))
    restored.canvas.callbacks.process(
        "button_release_event",
        MouseEvent("button_release_event", restored.canvas, 150, 130,
                   button=1))
    assert not dr.got_artist
    assert leg._loc != before


def test_disconnect_after_unpickle():
    fig, _, _ = _make_figure()
    restored = pickle.loads(pickle.dumps(fig))
    leg = restored.axes[0].get_legend()
    dr = leg._draggable
    registry = restored._canvas_callbacks
    n = sum(len(v) for v in registry.callbacks.values())
    dr.disconnect()
    assert sum(len(v) for v in registry.callbacks.values()) == n - 3
