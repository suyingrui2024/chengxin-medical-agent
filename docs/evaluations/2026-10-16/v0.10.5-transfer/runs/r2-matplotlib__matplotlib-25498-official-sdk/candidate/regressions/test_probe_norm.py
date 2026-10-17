import numpy as np
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm


def test_probe_direct_norm_assign():
    img = 10**np.random.normal(1, 1, size=(50, 50))
    fig, ax = plt.subplots(1, 1)
    plot = ax.imshow(img, cmap='gray')
    cb = fig.colorbar(plot, ax=ax)
    old_vmin, old_vmax = cb.vmin, cb.vmax
    plot.norm = LogNorm()
    print("after set, cb.norm:", type(cb.norm).__name__)
    print("mappable vmin/vmax:", plot.norm.vmin, plot.norm.vmax)
    print("cb vmin/vmax:", cb.vmin, cb.vmax)
    print("yscale:", cb.ax.yaxis.get_scale())
    cb.update_normal(plot)
    print("after update_normal cb.norm:", type(cb.norm).__name__)
    print("mappable vmin/vmax:", plot.norm.vmin, plot.norm.vmax)
    print("cb vmin/vmax:", cb.vmin, cb.vmax)
    print("yscale:", cb.ax.yaxis.get_scale())
    print("ticks:", cb.get_ticks())
