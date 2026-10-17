import pickle

def test_debug():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([0,1,2],[0,1,2],label='speed')
    leg = ax.legend()
    leg.set_draggable(True)
    d = leg._draggable
    print("CANVAS_TYPE", type(d.canvas).__mro__[:4])
    for name, obj in [("canvas", d.canvas)]:
        try:
            pickle.dumps(obj)
            print(name, "picklable True")
        except Exception as e:
            print(name, "picklable False", type(e).__name__, e)
    print("canvas dict keys", sorted(d.canvas.__dict__.keys()))
    print("figure has _canvas_callbacks", hasattr(fig, "_canvas_callbacks"))
