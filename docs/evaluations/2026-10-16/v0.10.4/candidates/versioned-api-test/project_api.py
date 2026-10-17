def signature(value, *, show_annotations=False):
    return str(value) + (':int' if show_annotations else '')
