"""
    test_index_symbols_grouping
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Regression tests for index entry grouping.

    Index entries whose keys start with a symbol that sorts after ASCII
    letters (e.g. ``£`` or ``←``) used to end up in a *second* "Symbols"
    section, after the letter sections.  The HTML output then contained two
    ``Symbols`` headings and two jumpbox links pointing at the same anchor.

    :copyright: Copyright 2007-2020 by the Sphinx team, see AUTHORS.
    :license: BSD, see LICENSE for details.
"""

from io import StringIO

from sphinx.application import Sphinx
from sphinx.environment.adapters.indexentries import IndexEntries


class _FakeBuilder:
    def get_relative_uri(self, from_, to):
        return 'genindex.html'


class _FakeDomain:
    def __init__(self, entries):
        self.entries = entries


class _FakeEnv:
    def __init__(self, entries):
        self._domain = _FakeDomain(entries)

    def get_domain(self, name):
        assert name == 'index'
        return self._domain


def _create_index(entries):
    env = _FakeEnv({'index': entries})
    return IndexEntries(env).create_index(_FakeBuilder())


def test_symbols_are_grouped_into_single_section():
    entries = [
        ('single', 'normal', 'index-0', '', None),
        ('single', '@', 'index-1', '', None),
        ('single', '£', 'index-2', '', None),
        ('single', '←', 'index-3', '', None),
        ('single', 'Zebra', 'index-4', '', None),
    ]
    index = _create_index(entries)

    keys = [key for key, _entries in index]
    assert keys.count('Symbols') == 1
    assert keys == ['Symbols', 'N', 'Z']

    symbols = dict(index)['Symbols']
    assert [name for name, _entry in symbols] == ['@', '£', '←']


def test_genindex_html_contains_single_symbols_section(tmp_path):
    srcdir = tmp_path / 'src'
    srcdir.mkdir()
    (srcdir / 'conf.py').write_text(
        "master_doc = 'index'\n"
        "project = 'regression'\n"
        "html_theme = 'basic'\n",
        encoding='utf-8')
    (srcdir / 'index.rst').write_text(
        "Index\n"
        "=====\n"
        "\n"
        ".. index:: @\n"
        ".. index:: £\n"
        ".. index:: ←\n"
        ".. index:: normal\n",
        encoding='utf-8')

    outdir = tmp_path / 'out'
    doctreedir = tmp_path / 'doctrees'
    app = Sphinx(str(srcdir), str(srcdir), str(outdir), str(doctreedir), 'html',
                 status=StringIO(), warning=StringIO(), freshenv=True)
    app.build()

    html = (outdir / 'genindex.html').read_text(encoding='utf-8')
    assert html.count('id="Symbols"') == 1
    assert html.count('href="#Symbols"') == 1

    # every symbol entry is listed inside the single "Symbols" section
    symbols_section = html.split('id="Symbols"', 1)[1].split('<h2', 1)[0]
    for name in ('@', '£', '←'):
        assert name in symbols_section
    assert 'normal' not in symbols_section
