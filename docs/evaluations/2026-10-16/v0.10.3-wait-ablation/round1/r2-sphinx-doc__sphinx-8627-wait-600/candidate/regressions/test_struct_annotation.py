"""Regression tests: ``struct.Struct`` / ``types.TracebackType`` annotations.

Classes such as ``struct.Struct`` (implemented in ``_struct``) and
``types.TracebackType`` (whose ``__module__`` is ``builtins``) used to be
rendered as ``_struct.Struct`` / ``traceback`` by the autodoc typehint
machinery, so the generated cross-reference could not be resolved
("class reference target not found").  They must be rendered under their
public name instead.
"""

import pathlib
import struct
from types import TracebackType
from typing import List, Optional

from sphinx.util.inspect import signature, stringify_signature
from sphinx.util.typing import restify, stringify


def consume_struct(_: struct.Struct) -> None:
    pass


def make_struct() -> struct.Struct:
    return struct.Struct('HH')


def make_structs() -> List[struct.Struct]:
    return []


def make_path() -> pathlib.Path:
    return pathlib.Path()


def make_optional_struct() -> Optional[struct.Struct]:
    return None


def test_stringify_struct() -> None:
    assert stringify(struct.Struct) == 'struct.Struct'


def test_restify_struct() -> None:
    assert restify(struct.Struct) == ':class:`struct.Struct`'


def test_stringify_tracebacktype() -> None:
    assert stringify(TracebackType) == 'types.TracebackType'


def test_restify_tracebacktype() -> None:
    assert restify(TracebackType) == ':class:`types.TracebackType`'


def test_stringify_nested_struct() -> None:
    assert stringify(Optional[struct.Struct]) == 'Optional[struct.Struct]'
    assert stringify(List[struct.Struct]) == 'List[struct.Struct]'


def test_restify_nested_struct() -> None:
    assert restify(Optional[struct.Struct]) == ':obj:`Optional`\\ [:class:`struct.Struct`]'
    assert restify(List[struct.Struct]) == ':class:`List`\\ [:class:`struct.Struct`]'


def test_unaffected_annotations() -> None:
    assert stringify(int) == 'int'
    assert stringify(pathlib.Path) == 'pathlib.Path'


def test_signature_of_autodocumented_functions() -> None:
    """The autodoc signature is what the python domain turns into xrefs."""
    sig = stringify_signature(signature(make_struct, typehints=True))
    assert 'struct.Struct' in sig and '_struct' not in sig, sig
    sig = stringify_signature(signature(consume_struct, typehints=True))
    assert 'struct.Struct' in sig and '_struct' not in sig, sig
    sig = stringify_signature(signature(make_structs, typehints=True))
    assert 'struct.Struct' in sig, sig
    sig = stringify_signature(signature(make_optional_struct, typehints=True))
    assert 'struct.Struct' in sig and '_struct' not in sig, sig
    sig = stringify_signature(signature(make_path, typehints=True))
    assert 'pathlib.Path' in sig, sig
