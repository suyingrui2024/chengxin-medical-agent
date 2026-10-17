"""Regression tests for stringifying classes of C extension modules.

``struct.Struct`` (and other classes of C extension modules such as
``io.TextIOWrapper``) report a private module name (ex. ``_struct``).
Stringifying such a class used to produce an unresolvable reference
(``_struct.Struct``); autodoc then reported::

    class reference target not found: Struct
"""

import io
import numbers
import pathlib
import struct

from sphinx.util.typing import restify, stringify


def test_stringify_struct():
    assert struct.Struct.__module__ == '_struct'
    assert stringify(struct.Struct) == 'struct.Struct'


def test_stringify_private_module_class():
    assert stringify(io.TextIOWrapper) == 'io.TextIOWrapper'


def test_restify_struct():
    assert restify(struct.Struct) == ':class:`struct.Struct`'


def test_stringify_public_module_class_unchanged():
    assert stringify(pathlib.Path) == 'pathlib.Path'
    assert stringify(numbers.Integral) == 'numbers.Integral'
    assert stringify(int) == 'int'
