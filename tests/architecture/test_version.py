"""The version guard must actually fire.

A check that compares four declarations and never fires is a comment.  These
tests build a throwaway tree where the declarations disagree in each possible
way and assert the matching failure is reported.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "version.py"


def _load():
    spec = importlib.util.spec_from_file_location("version", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()

VERSION = "1.2.3"


def build(
    root: pathlib.Path,
    *,
    source: str = VERSION,
    package_json: str = VERSION,
    pyproject: str = VERSION,
    init: str | None = None,
    extra_source: str = "",
) -> pathlib.Path:
    (root / "src" / "medagent").mkdir(parents=True, exist_ok=True)
    body = (
        init
        if init is not None
        else f'"""The package."""\n__version__ = "{source}"\n'
    )
    (root / "src" / "medagent" / "__init__.py").write_text(body, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "medagent", "version": package_json}), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "medagent"\nversion = "' + pyproject + '"\n', encoding="utf-8"
    )
    if extra_source:
        (root / "src" / "medagent" / "stamped.py").write_text(
            extra_source, encoding="utf-8"
        )
    return root


def test_consistent_declarations_pass(tmp_path):
    assert guard.check(build(tmp_path)) == []


def test_package_json_out_of_step_is_reported(tmp_path):
    failures = guard.check(build(tmp_path, package_json="9.9.9"))
    assert any("package.json" in f for f in failures), failures


def test_pyproject_out_of_step_is_reported(tmp_path):
    failures = guard.check(build(tmp_path, pyproject="9.9.9"))
    assert any("pyproject.toml" in f for f in failures), failures


def test_a_package_without_a_version_is_reported(tmp_path):
    failures = guard.check(build(tmp_path, init='"""The package."""\n'))
    assert failures, "a package with no __version__ cannot be compared"


def test_a_hardcoded_policy_version_is_reported(tmp_path):
    """The audit record must not be able to claim a policy the code did not run."""
    failures = guard.check(
        build(
            tmp_path,
            extra_source='POLICY = "medical-tools-v1.2.3"\n',
        )
    )
    assert any("hardcoded" in f for f in failures), failures


def test_a_derived_policy_version_is_allowed(tmp_path):
    failures = guard.check(
        build(
            tmp_path,
            extra_source='POLICY = f"medical-tools-v{__version__}"\n',
        )
    )
    assert failures == [], failures


def test_the_real_tree_agrees():
    assert guard.check(guard.ROOT) == []
