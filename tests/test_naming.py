"""The file-naming guard must actually fire.

Same reasoning as ``test_import_rules.py``: a structural check that silently
passes is worse than no check, because it advertises a guarantee the codebase
does not have. Each rule gets a tree that violates it, and the two names this
repo deliberately keeps (``core.py``, ``tests/support/*``) get a tree that must
stay quiet -- an over-eager guard gets deleted, and then nothing is guarded.
"""
from __future__ import annotations

import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lint" / "naming.py"


def _load():
    spec = importlib.util.spec_from_file_location("naming", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load()


def failure_for(root: pathlib.Path, rel: str) -> str | None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return rules.check(path, root)


def test_leftover_word_is_rejected(tmp_path):
    message = failure_for(tmp_path, "src/pkg/utils.py")
    assert message and "leftovers" in message, message


def test_batch_suffix_is_rejected(tmp_path):
    message = failure_for(tmp_path, "src/pkg/formulas_extra.py")
    assert message and "'extra' suffix" in message, message


def test_version_suffix_is_rejected(tmp_path):
    assert failure_for(tmp_path, "src/pkg/engine_v2.py")
    assert failure_for(tmp_path, "src/pkg/engine_2.py")
    assert failure_for(tmp_path, "src/pkg/engine_old.py")


def test_uncollected_test_module_is_rejected(tmp_path):
    message = failure_for(tmp_path, "tests/question_ordering_contract.py")
    assert message and "test_*.py" in message, message


def test_test_modules_and_conftest_are_accepted(tmp_path):
    assert failure_for(tmp_path, "tests/test_agent.py") is None
    assert failure_for(tmp_path, "tests/conftest.py") is None


def test_support_helpers_keep_their_own_names(tmp_path):
    assert failure_for(tmp_path, "tests/support/builders.py") is None
    assert failure_for(tmp_path, "tests/support/scripted.py") is None


def test_core_is_allowed_because_it_is_a_consistent_convention(tmp_path):
    assert failure_for(tmp_path, "src/pkg/core.py") is None
    assert failure_for(tmp_path, "src/pkg/agent/core.py") is None


def test_close_copy_is_not_a_batch_suffix(tmp_path):
    assert failure_for(tmp_path, "tests/test_close_copy.py") is None


def test_the_real_tree_passes():
    assert rules.main([]) == 0
