"""Regression test: autodoc must be able to resolve ``struct.Struct`` annotations.

Reported as ``class reference target not found: Struct``: the ``__module__`` of the
``Struct`` class does not point to the public ``struct`` module, so stringified and
reST-ified annotations used to produce an unqualified / private name.
"""

import struct
import types
from typing import Dict, Optional, Tuple

from sphinx.util import inspect
from sphinx.util.typing import restify, stringify


def consume_struct(_: struct.Struct) -> None:
    pass


def make_struct() -> struct.Struct:
    return struct.Struct('HH')


def test_stringify_struct() -> None:
    assert stringify(struct.Struct) == 'struct.Struct'


def test_restify_struct() -> None:
    assert restify(struct.Struct) == ':class:`struct.Struct`'


def test_stringify_struct_containers() -> None:
    assert stringify(Optional[struct.Struct]) == 'Optional[struct.Struct]'
    assert stringify(Dict[str, struct.Struct]) == 'Dict[str, struct.Struct]'


def test_restify_struct_containers() -> None:
    assert restify(Optional[struct.Struct]) == ':obj:`Optional`\\ [:class:`struct.Struct`]'
    assert restify(Tuple[struct.Struct, ...]) == ':class:`Tuple`\\ [:class:`struct.Struct`, ...]'


def test_stringify_tracebacktype() -> None:
    assert stringify(types.TracebackType) == 'types.TracebackType'


def test_restify_tracebacktype() -> None:
    assert restify(types.TracebackType) == ':class:`types.TracebackType`'


def test_signature_of_struct_annotations() -> None:
    assert inspect.stringify_signature(inspect.signature(consume_struct)) == \
        '(_: struct.Struct) -> None'
    assert inspect.stringify_signature(inspect.signature(make_struct)) == \
        '() -> struct.Struct'
