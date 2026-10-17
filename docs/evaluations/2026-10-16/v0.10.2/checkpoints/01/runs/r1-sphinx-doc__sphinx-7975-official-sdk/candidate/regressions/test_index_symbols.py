"""Regression tests for grouping of symbol index entries.

See: "Two sections called Symbols in index".  Symbol entries whose leading
character lies after the ASCII letters (such as ``£`` and ``←``) used to be
sorted *after* the alphabetic entries, which produced a second ``Symbols``
group with the same HTML anchor as the first one.
"""

import pytest

from sphinx.environment.adapters.indexentries import IndexEntries
from sphinx.testing import restructuredtext
from sphinx.testing.path import path

pytest_plugins = 'sphinx.testing.fixtures'


@pytest.fixture(scope='session')
def rootdir():
    return path(__file__).parent.parent / 'tests' / 'roots'


@pytest.mark.sphinx('dummy', freshenv=True)
def test_symbols_are_grouped_into_single_section(app):
    text = (".. index:: @decorator\n"
            ".. index:: £pound\n"
            ".. index:: ←arrow\n"
            ".. index:: docutils\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    keys = [key for key, _ in index]
    # all symbol entries must share one single "Symbols" section
    assert keys.count('Symbols') == 1
    # ... and that section has to come before the alphabetic ones
    assert keys[0] == 'Symbols'
    assert keys[1] == 'D'

    symbols = dict(index)['Symbols']
    assert [name for name, _ in symbols] == ['@decorator', '£pound', '←arrow']


@pytest.mark.sphinx('dummy', freshenv=True)
def test_symbols_grouping_matches_low_ascii_symbols(app):
    text = (".. index:: 9-symbol\n"
            ".. index:: &-symbol\n"
            ".. index:: £-symbol\n"
            ".. index:: docutils\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    keys = [key for key, _ in index]
    assert keys == ['Symbols', 'D']
    symbols = dict(index)['Symbols']
    assert [name for name, _ in symbols] == ['&-symbol', '9-symbol', '£-symbol']
