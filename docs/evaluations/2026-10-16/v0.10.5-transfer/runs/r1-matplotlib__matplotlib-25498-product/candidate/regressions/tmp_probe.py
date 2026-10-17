import numpy as np, matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, Normalize

def scenario(setter, label):
    rng = np.random.RandomState(0)
    img = 10**rng.normal(1, 1, size=(10, 10))
    fig, ax = plt.subplots()
    plot = ax.imshow(img, cmap='gray')
    cb = fig.colorbar(plot, ax=ax)
    old = (cb.vmin, cb.vmax)
    setter(plot)
    try:
        cb.update_normal(plot)
        print(f"{label}: OK vmin={cb.vmin:.4g} vmax={cb.vmax:.4g} norm={type(cb.norm).__name__} nvmin={cb.norm.vmin} nvmax={cb.norm.vmax} ticks={np.round(cb.ax.yaxis.get_majorticklocs(),3)[:4]}")
    except Exception as e:
        print(f"{label}: FAIL {type(e).__name__}: {e}")

scenario(lambda m: setattr(m, 'norm', LogNorm(m.norm.vmin, m.norm.vmax)), "scaled LogNorm via attr")
scenario(lambda m: m.set_norm(LogNorm(m.norm.vmin, m.norm.vmax)), "scaled LogNorm via set_norm")
scenario(lambda m: setattr(m, 'norm', LogNorm()), "unscaled LogNorm via attr")
scenario(lambda m: m.set_norm(LogNorm()), "unscaled LogNorm via set_norm")
