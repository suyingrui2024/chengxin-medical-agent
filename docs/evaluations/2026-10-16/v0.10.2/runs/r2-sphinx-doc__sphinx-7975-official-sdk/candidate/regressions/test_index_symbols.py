"""Regression test for index entries beginning with symbols.

Index entries whose leading character is a symbol with a code point greater
than the one used to push alphabetic entries to the back (for example ``£``
or ``←``) used to be sorted after the alphabetic entries.  As a result they
formed a *second* group named "Symbols" in the generated index, sharing the
same HTML anchor as the first one.  All symbol entries must end up in a
single "Symbols" section placed before the alphabetic sections.
"""

import pytest

from sphinx.environment.adapters.indexentries import IndexEntries
from sphinx.testing import restructuredtext
from sphinx.testing.path import path
from sphinx.testing.util import SphinxTestApp


@pytest.fixture
def app(tmp_path):
    srcdir = path(str(tmp_path)) / 'root'
    srcdir.makedirs(exist_ok=True)
    (srcdir / 'conf.py').write_text('')
    (srcdir / 'index.rst').write_text('')
    app = SphinxTestApp('dummy', srcdir=srcdir, freshenv=True)
    yield app
    app.cleanup()


def test_create_index_with_high_symbols(app):
    text = (".. index:: @entry\n"
            ".. index:: £entry\n"
            ".. index:: ←entry\n"
            ".. index:: normal\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    # every symbol entry must be collected in exactly one "Symbols" section
    symbols = [group for group in index if group[0] == 'Symbols']
    assert len(symbols) == 1
    assert [entry[0] for entry in symbols[0][1]] == ['@entry', '£entry', '←entry']

    # the single Symbols section comes before the alphabetic sections
    assert index[0][0] == 'Symbols'
    assert index[1][0] == 'N'
    assert [entry[0] for entry in index[1][1]] == ['normal']


def test_symbols_grouping_is_contiguous(app):
    text = (".. index:: @alpha\n"
            ".. index:: £beta\n"
            ".. index:: ←gamma\n"
            ".. index:: zebra\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    keys = [key for key, _entries in index]
    assert keys.count('Symbols') == 1
    assert keys == ['Symbols', 'Z']
    assert [entry[0] for entry in index[0][1]] == ['@alpha', '£beta', '←gamma']
