"""The function-size guard must actually fire.

A ceiling nobody can trip is decoration.  These tests drive the two halves
separately: ``check`` against hand-built measurements, so the baseline
arithmetic is tested without writing 400-line fixtures, and ``measure`` against
a throwaway package tree, so the counting itself is pinned.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "function_size.py"


def _load():
    spec = importlib.util.spec_from_file_location("function_size", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def build(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


# --- the ceilings -----------------------------------------------------------


def test_a_new_long_function_is_rejected():
    failures, _ = guard.check({"medagent.a::big": (61, 3)}, {})
    assert failures and "exceeds" in failures[0], failures


def test_a_short_function_is_accepted():
    failures, warnings = guard.check({"medagent.a::small": (20, 4)}, {})
    assert failures == [] and warnings == []


def test_a_branchy_function_is_rejected_even_when_short():
    """Thirty-nine lines and twenty-three branches is still unreadable."""
    failures, _ = guard.check({"medagent.a::dense": (38, 23)}, {})
    assert failures and "38 lines / 23 branches" in failures[0], failures


# --- what the baseline does -------------------------------------------------


def test_a_baselined_function_is_allowed_at_its_recorded_size():
    baseline = {"medagent.a::big": (388, 78)}
    failures, warnings = guard.check({"medagent.a::big": (388, 78)}, baseline)
    assert (failures, warnings) == ([], [])


def test_a_baselined_function_may_not_grow():
    baseline = {"medagent.a::big": (388, 78)}
    failures, _ = guard.check({"medagent.a::big": (400, 78)}, baseline)
    assert failures and "grew past its baselined" in failures[0], failures


def test_a_baselined_function_that_shrank_is_reported_for_removal():
    """Otherwise the list only grows and stops describing anything."""
    baseline = {"medagent.a::big": (388, 78)}
    failures, warnings = guard.check({"medagent.a::big": (40, 8)}, baseline)
    assert failures == []
    assert any("strike it off" in w for w in warnings), warnings


def test_a_renamed_baselined_function_is_reported():
    """A rename would otherwise hand the new name a free pass."""
    baseline = {"medagent.a::old_name": (388, 78)}
    failures, warnings = guard.check({"medagent.a::new_name": (10, 2)}, baseline)
    assert failures == []
    assert any("no longer exists" in w for w in warnings), warnings


def test_a_baselined_function_that_shrank_still_over_is_reported_for_lowering():
    """Otherwise the old ceiling becomes headroom to grow back into."""
    baseline = {"medagent.a::big": (388, 78)}
    failures, warnings = guard.check({"medagent.a::big": (80, 30)}, baseline)
    assert failures == []
    assert any("lower the entry" in w for w in warnings), warnings


# --- measuring --------------------------------------------------------------


def test_measure_keys_a_function_by_module_and_class(tmp_path):
    root = build(
        tmp_path,
        {
            "medagent/__init__.py": "",
            "medagent/thing.py": (
                "class Thing:\n"
                "    def go(self):\n"
                "        return 1\n"
                "\n"
                "def free():\n"
                "    return 2\n"
            ),
        },
    )
    sizes = guard.measure(root / "medagent")
    assert set(sizes) == {"medagent.thing::Thing.go", "medagent.thing::free"}


def test_nested_branches_are_not_charged_to_the_outer_function(tmp_path):
    """The reader meets a closure when they open it, not before."""
    root = build(
        tmp_path,
        {
            "medagent/__init__.py": "",
            "medagent/outer.py": (
                "def outer():\n"
                "    def inner(value):\n"
                "        if value:\n"
                "            return 1\n"
                "        if value == 2:\n"
                "            return 2\n"
                "        return 0\n"
                "    return inner(1)\n"
            ),
        },
    )
    sizes = guard.measure(root / "medagent")
    assert sizes["medagent.outer::outer"] == (8, 0), sizes
    assert sizes["medagent.outer::outer.inner"][1] == 2, sizes


def test_two_definitions_with_the_same_name_fail(tmp_path):
    """The second wins at import time; the first is dead code nobody sees."""
    root = build(
        tmp_path,
        {
            "medagent/__init__.py": "",
            "medagent/dup.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def helper():\n"
                "    return 2\n"
            ),
        },
    )
    with pytest.raises(SystemExit) as raised:
        guard.measure(root / "medagent")
    assert "medagent.dup::helper" in str(raised.value)


def test_the_real_tree_has_no_duplicate_definitions():
    """The check above is only useful if the tree it guards is clean."""
    assert guard.measure()
