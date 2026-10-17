"""
    Regression tests for struct.Struct type annotations.
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    ``struct.Struct`` lives in the private C module ``_struct``, so its
    ``__module__`` is ``'_struct'``.  Sphinx used to render it as
    ``_struct.Struct``, which the Python domain cannot resolve
    (``class reference target not found: Struct``).
"""

import struct
import sys
from io import StringIO

from sphinx.testing.path import path
from sphinx.testing.util import SphinxTestApp
from sphinx.util.typing import restify, stringify


def test_stringify_struct() -> None:
    assert stringify(struct.Struct) == 'struct.Struct'


def test_restify_struct() -> None:
    assert restify(struct.Struct) == ':class:`struct.Struct`'


def test_autodoc_struct_annotation(tmp_path) -> None:
    srcdir = path(str(tmp_path))
    srcdir.makedirs(exist_ok=True)
    (srcdir / 'conf.py').write_text(
        "extensions = ['sphinx.ext.autodoc']\n"
        "nitpicky = True\n"
    )
    (srcdir / 'target.py').write_text(
        "import struct\n"
        "import pathlib\n"
        "\n"
        "def consume_struct(_: struct.Struct) -> None:\n"
        "    pass\n"
        "\n"
        "def make_struct() -> struct.Struct:\n"
        "    return struct.Struct('HH')\n"
        "\n"
        "def make_path() -> pathlib.Path:\n"
        "    return pathlib.Path()\n"
    )
    (srcdir / 'index.rst').write_text(
        "Hello World\n"
        "===========\n"
        "\n"
        ".. py:class:: struct.Struct\n"
        "\n"
        ".. automodule:: target\n"
        "   :members:\n"
    )

    sys.path.insert(0, str(srcdir))
    warning = StringIO()
    app = SphinxTestApp(srcdir=srcdir, warning=warning)
    try:
        app.build()
        warnings = warning.getvalue()
        content = (app.outdir / 'index.html').read_text()
    finally:
        app.cleanup()
        if str(srcdir) in sys.path:
            sys.path.remove(str(srcdir))

    # the module must really be documented (guards against a vacuous test)
    assert 'failed to import' not in warnings
    assert 'make_struct' in content
    # the annotation must be attributed to the public struct module
    assert 'struct.Struct' in content
    assert '_struct.Struct' not in warnings
    assert 'reference target not found' not in warnings
