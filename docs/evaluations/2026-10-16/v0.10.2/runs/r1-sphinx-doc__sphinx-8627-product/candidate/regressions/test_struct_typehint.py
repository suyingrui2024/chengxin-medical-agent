"""Regression tests for the ``struct.Struct`` type annotation (issue #8410).

``struct.Struct`` is implemented in the private ``_struct`` module, so its
``__module__`` attribute is ``'_struct'`` while the documented and
cross-referenceable name is ``struct.Struct``.  Sphinx used to stringify the
annotation using ``__module__`` and emitted ``_struct.Struct``, which could not
be resolved (``class reference target not found: Struct`` with ``-n``).
"""

import importlib.util
import struct
from pathlib import Path
from types import TracebackType

from sphinx.util.inspect import signature, stringify_signature
from sphinx.util.typing import get_type_hints, stringify

TARGET = '''\
import pathlib
import struct


def consume_struct(_: struct.Struct) -> None:
    pass


def make_struct() -> struct.Struct:
    return struct.Struct('HH')


def make_path() -> pathlib.Path:
    return pathlib.Path()
'''


def consume_struct(_: struct.Struct) -> None:
    pass


def make_struct() -> struct.Struct:
    return struct.Struct('HH')


def test_stringify_struct() -> None:
    assert stringify(struct.Struct) == 'struct.Struct'


def test_stringify_traceback_type() -> None:
    assert stringify(TracebackType) == 'types.TracebackType'


def test_stringify_signature_with_struct_annotation() -> None:
    sig = signature(consume_struct)
    assert 'struct.Struct' in stringify_signature(sig)
    assert '_struct.Struct' not in stringify_signature(sig)

    sig = signature(make_struct)
    assert stringify_signature(sig).endswith('-> struct.Struct')
    assert '_struct' not in stringify_signature(sig)


def test_annotation_pipeline_of_a_module(tmp_path) -> None:
    """Resolved annotations of a real module are stringified with public names.

    This is the pipeline used by autodoc to render signatures: the annotations
    are evaluated by ``typing.get_type_hints()`` (wrapped by Sphinx) and then
    stringified by ``sphinx.util.typing.stringify()``.
    """
    target = tmp_path / 'target.py'
    target.write_text(TARGET)

    spec = importlib.util.spec_from_file_location('regression_target', str(target))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    hints = get_type_hints(module.consume_struct)
    assert stringify(hints['_']) == 'struct.Struct'

    # pathlib.Path keeps working (its __module__ is already correct)
    hints = get_type_hints(module.make_path)
    assert stringify(hints['return']) == 'pathlib.Path'

    assert stringify_signature(signature(module.consume_struct)) == \
        '(_: struct.Struct) -> None'
    assert stringify_signature(signature(module.make_struct)) == '() -> struct.Struct'
