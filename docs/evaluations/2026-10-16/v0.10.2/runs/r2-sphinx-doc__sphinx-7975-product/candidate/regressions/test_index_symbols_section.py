"""
    Regression test for duplicated "Symbols" sections in the index.

    Index entries whose first character is a symbol that sorts after the
    letters in ASCII (for example '£' or '←') used to end up in a second
    "Symbols" section placed after all letter sections, leading to two
    sections with the same name and the same anchor in the generated HTML
    index.

    All symbol entries must be collected in a single "Symbols" section that
    is placed before the letter sections.
"""

from pathlib import Path

import pytest

# import the testing helpers first so that the sphinx packages are fully
# initialised before importing the index entries adapter
from sphinx.testing import restructuredtext  # noqa: E402
from sphinx.testing.path import path  # noqa: E402
from sphinx.environment.adapters.indexentries import IndexEntries  # noqa: E402

pytest_plugins = ('sphinx.testing.fixtures',)


@pytest.fixture(scope='session')
def rootdir():
    return path(Path(__file__).parents[1] / 'tests' / 'roots')


@pytest.mark.sphinx('dummy', freshenv=True)
def test_symbols_form_a_single_section(app):
    text = (".. index:: docutils\n"
            ".. index:: £-symbol\n"
            ".. index:: @-symbol\n"
            ".. index:: ←-symbol\n"
            ".. index:: Python\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    keys = [key for key, _entries in index]

    # only one "Symbols" section, placed before the letter sections
    assert keys.count('Symbols') == 1
    assert keys == ['Symbols', 'D', 'P']

    # all symbol entries belong to that single section
    assert [key for key, _entry in index[0][1]] == ['@-symbol', '£-symbol', '←-symbol']


@pytest.mark.sphinx('dummy', freshenv=True)
def test_symbols_section_is_unique_without_letters(app):
    text = (".. index:: £-symbol\n"
            ".. index:: ←-symbol\n")
    restructuredtext.parse(app, text)
    index = IndexEntries(app.env).create_index(app.builder)

    assert [key for key, _entries in index] == ['Symbols']
    assert [key for key, _entry in index[0][1]] == ['£-symbol', '←-symbol']
