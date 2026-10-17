"""Regression tests for updating a colorbar after changing ``mappable.norm``.

See issue: "Update colorbar after changing mappable.norm".
``Colorbar.update_normal`` must adopt the new norm of the mappable and,
when that norm has no limits set yet (e.g. a freshly created ``LogNorm()``),
autoscale it from the mappable data instead of falling back to the invalid
0-1 range (which made ``LogNorm.inverse`` raise ``ZeroDivisionError`` /
``ValueError: Invalid vmin or vmax``).
"""
import numpy as np
import pytest

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize
from matplotlib.colorbar import Colorbar


def _make_figure():
    rng = np.random.RandomState(0)
    img = 10 ** rng.normal(1, 1, size=(50, 50))
    fig, ax = plt.subplots(1, 1)
    mappable = ax.imshow(img, cmap='gray')
    cbar = fig.colorbar(mappable, ax=ax)
    return fig, mappable, cbar, img


def test_update_normal_with_new_unscaled_lognorm():
    # Exact scenario from the report: assign a fresh (unscaled) LogNorm.
    fig, mappable, cbar, img = _make_figure()
    mappable.norm = LogNorm()
    assert not mappable.norm.scaled()

    cbar.update_normal(mappable)

    assert isinstance(cbar, Colorbar)
    assert cbar.norm is mappable.norm
    assert isinstance(cbar.norm, LogNorm)
    assert cbar.norm.scaled()
    assert cbar.vmin == pytest.approx(img.min())
    assert cbar.vmax == pytest.approx(img.max())
    # The colorbar must be drawable with the new norm.
    fig.canvas.draw()


def test_update_normal_uses_data_range_for_unscaled_norm():
    fig, mappable, cbar, img = _make_figure()
    mappable.norm = Normalize()
    cbar.update_normal(mappable)
    assert cbar.vmin == pytest.approx(img.min())
    assert cbar.vmax == pytest.approx(img.max())
    fig.canvas.draw()


def test_set_norm_notifies_and_scales_colorbar():
    # Setting the norm through the mappable also updates the colorbar; the
    # unscaled norm has to be autoscaled the same way.
    fig, mappable, cbar, img = _make_figure()
    mappable.set_norm(LogNorm())
    assert cbar.norm is mappable.norm
    assert cbar.vmin == pytest.approx(img.min())
    assert cbar.vmax == pytest.approx(img.max())
    fig.canvas.draw()


def test_update_normal_with_scaled_norm_still_works():
    # The pre-existing behaviour (explicit limits) must be preserved.
    fig, mappable, cbar, img = _make_figure()
    new_norm = LogNorm(img.min() * 10, img.max() * 10)
    mappable.norm = new_norm
    cbar.update_normal(mappable)
    assert cbar.norm is new_norm
    assert cbar.vmin == pytest.approx(img.min() * 10)
    assert cbar.vmax == pytest.approx(img.max() * 10)
    fig.canvas.draw()


def test_update_normal_does_not_fail_without_array():
    # A mappable without data cannot be autoscaled; this must not raise.
    fig, ax = plt.subplots()
    sm = matplotlib.cm.ScalarMappable(norm=Normalize(vmin=0, vmax=1))
    cbar = fig.colorbar(sm, ax=ax)
    sm.norm = Normalize()
    cbar.update_normal(sm)
    fig.canvas.draw()
