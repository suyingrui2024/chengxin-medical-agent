"""
    test_typing_invalid_builtin_classes
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Regression tests for type annotations of classes whose ``__module__``
    does not name the module they are documented in (ex. ``struct.Struct``
    lives in ``_struct``, ``types.TracebackType`` reports ``builtins``).

    Such annotations used to be rendered as ``Struct`` / ``TracebackType``,
    which made the generated cross-reference unresolvable
    ("class reference target not found: Struct").
"""

from struct import Struct
from types import TracebackType
from typing import List, Optional

import pytest

from sphinx import addnodes
from sphinx.deprecation import RemovedInSphinx50Warning
from sphinx.domains.python import _parse_annotation
from sphinx.util.typing import INVALID_BUILTIN_CLASSES, restify, stringify


def test_restify_invalid_builtin_classes():
    assert restify(Struct) == ':class:`struct.Struct`'
    assert restify(TracebackType) == ':class:`types.TracebackType`'


def test_stringify_invalid_builtin_classes():
    assert stringify(Struct) == 'struct.Struct'
    assert stringify(TracebackType) == 'types.TracebackType'


def test_nested_invalid_builtin_classes():
    assert restify(List[Struct]) == ':class:`List`\\ [:class:`struct.Struct`]'
    assert stringify(List[Struct]) == 'List[struct.Struct]'
    assert 'struct.Struct' in stringify(Optional[Struct])


def test_invalid_builtin_classes_mapping():
    assert INVALID_BUILTIN_CLASSES[Struct] == 'struct.Struct'
    assert INVALID_BUILTIN_CLASSES[TracebackType] == 'types.TracebackType'


def test_unhashable_annotation_still_supported():
    # looking up the class in the mapping must not break for objects that
    # cannot be used as dict keys (ex. a list literal used as annotation)
    assert stringify([1, 2]) == '[1, 2]'


def test_annotation_reference_targets_the_public_module():
    with pytest.warns(RemovedInSphinx50Warning):
        result = _parse_annotation(stringify(Struct), None)

    refs = [node for node in result if isinstance(node, addnodes.pending_xref)]
    assert len(refs) == 1
    assert refs[0]['reftarget'] == 'struct.Struct'
    assert refs[0]['reftype'] == 'class'
