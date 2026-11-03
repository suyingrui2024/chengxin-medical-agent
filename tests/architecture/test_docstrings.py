"""The docstring guard must actually fire.

A guard that lets a new undocumented function through is a comment with a
process around it.  These tests build a throwaway tree and assert each rule
trips on its own.
"""
from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "docstrings.py"


def _load():
    spec = importlib.util.spec_from_file_location("docstrings", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def long_body(lines: int = 40) -> str:
    """A function body long enough to need a docstring."""
    return "\n".join(f"    x{index} = {index}" for index in range(lines))


def build(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def check(tmp_path, files, baseline=None, module_baseline=None):
    root = build(tmp_path, files)
    sizes, modules = guard.measure(root)
    return guard.check(
        sizes,
        modules,
        baseline or {},
        module_baseline or set(),
    )


def test_a_long_function_without_a_docstring_is_rejected(tmp_path):
    failures, _ = check(
        tmp_path,
        {
            "medagent/thing.py": (
                '"""A module."""\n\n\ndef big():\n' + long_body() + "\n"
            )
        },
    )
    assert failures and "no docstring" in failures[0], failures


def test_a_short_function_needs_no_docstring(tmp_path):
    failures, _ = check(
        tmp_path,
        {"medagent/thing.py": '"""A module."""\n\n\ndef small():\n    return 1\n'},
    )
    assert failures == [], failures


def test_a_module_without_a_docstring_is_rejected(tmp_path):
    failures, _ = check(
        tmp_path,
        {"medagent/thing.py": "def f():\n    return 1\n"},
    )
    assert any("module docstring" in f for f in failures), failures


def test_a_baselined_function_is_allowed(tmp_path):
    failures, _ = check(
        tmp_path,
        {"medagent/thing.py": '"""A module."""\n\n\ndef big():\n' + long_body() + "\n"},
        baseline={"medagent/thing.py::big": 40},
    )
    assert failures == [], failures


def test_a_baselined_function_that_got_documented_is_reported(tmp_path):
    """Otherwise the list only grows and stops describing anything."""
    failures, warnings = check(
        tmp_path,
        {
            "medagent/thing.py": (
                '"""A module."""\n\n\ndef big():\n    """Documented."""\n'
                + long_body()
                + "\n"
            )
        },
        baseline={"medagent/thing.py::big": 40},
    )
    assert failures == []
    assert any("strike it off" in w for w in warnings), warnings


def test_a_baselined_module_that_got_documented_is_reported(tmp_path):
    failures, warnings = check(
        tmp_path,
        {"medagent/thing.py": '"""A module."""\n'},
        module_baseline={"medagent/thing.py"},
    )
    assert failures == []
    assert any("strike it off" in w for w in warnings), warnings


def test_a_renamed_function_is_a_new_debt(tmp_path):
    """The old entry must not cover the new name."""
    failures, _ = check(
        tmp_path,
        {"medagent/thing.py": '"""A module."""\n\n\ndef renamed():\n' + long_body() + "\n"},
        baseline={"medagent/thing.py::big": 40},
    )
    assert failures and "renamed" in failures[0], failures


def test_a_baselined_function_that_disappeared_is_reported(tmp_path):
    failures, warnings = check(
        tmp_path,
        {"medagent/thing.py": '"""A module."""\n'},
        baseline={"medagent/thing.py::big": 40},
    )
    assert failures == []
    assert any("no longer exists" in w for w in warnings), warnings


def test_the_real_tree_is_consistent():
    """The committed baseline covers every gap in the real tree."""
    assert guard.check(*guard.measure(), guard.BASELINE, guard.MODULE_BASELINE) == (
        [],
        [],
    )
