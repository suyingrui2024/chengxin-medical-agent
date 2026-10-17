"""Regression tests: updating a colorbar after the mappable's norm changed.

Reassigning ``mappable.norm`` to a norm whose limits are not set yet (e.g.
``LogNorm()``) used to make the attached colorbar fail, because the colorbar
tried to invert the new norm on the default 0-1 range.
"""
import numpy as np
import pytest

import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm


def _image():
    rng = np.random.RandomState(0)
    return 10 ** rng.normal(1, 1, size=(50, 50))


def test_update_normal_after_replacing_norm():
    img = _image()
    fig, ax = plt.subplots()
    try:
        plot = ax.imshow(img, cmap='gray')
        cb = fig.colorbar(plot, ax=ax)
        plot.norm = LogNorm()
        # The explicit call from the issue report must work as well.
        cb.update_normal(plot)
        assert cb.norm is plot.norm
        assert cb.vmin == pytest.approx(img.min())
        assert cb.vmax == pytest.approx(img.max())
        assert cb.ax.get_yscale() == 'log'
        fig.canvas.draw()
    finally:
        plt.close(fig)


def test_replacing_norm_autoscales_from_data():
    img = _image()
    fig, ax = plt.subplots()
    try:
        plot = ax.imshow(img, cmap='gray')
        fig.colorbar(plot, ax=ax)
        plot.norm = LogNorm()
        assert plot.norm.vmin == pytest.approx(img.min())
        assert plot.norm.vmax == pytest.approx(img.max())
    finally:
        plt.close(fig)


def test_update_normal_keeps_explicit_norm_limits():
    img = _image()
    fig, ax = plt.subplots()
    try:
        plot = ax.imshow(img, cmap='gray')
        cb = fig.colorbar(plot, ax=ax)
        plot.norm = LogNorm(vmin=1, vmax=100)
        cb.update_normal(plot)
        assert cb.vmin == 1
        assert cb.vmax == 100
    finally:
        plt.close(fig)
