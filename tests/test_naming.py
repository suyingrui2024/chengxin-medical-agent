"""The file-naming guard must actually fire.

Same reasoning as ``test_import_rules.py``: a structural check that silently
passes is worse than no check, because it advertises a guarantee the codebase
does not have. Each rule gets a tree that violates it, in every language the
rule claims to cover, and the names this repo deliberately keeps (``core.py``,
``tests/support/*``, an ordered ``02-artifacts.css``) get a tree that must stay
quiet -- an over-eager guard gets deleted, and then nothing is guarded.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

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


@pytest.mark.parametrize(
    "rel",
    [
        "src/pkg/utils.py",
        "src/pkg/helpers.py",
        "src/web/js/date-utils.js",
        "src/web/css/layout-common.css",
        "desktop/window-helpers.cjs",
    ],
)
def test_leftover_word_is_rejected(tmp_path, rel):
    message = failure_for(tmp_path, rel)
    assert message and "leftovers" in message, message


@pytest.mark.parametrize(
    "rel",
    [
        "src/pkg/formulas_extra.py",
        "src/pkg/engine_v2.py",
        "src/pkg/engine_2.py",
        "src/pkg/engine_old.py",
        "src/web/js/panel-extra.js",
        "src/web/css/styles-08-settings-and-late-refinements.css",
        "src/web/css/tokens-final.css",
    ],
)
def test_dated_name_is_rejected(tmp_path, rel):
    """R2 covers every language: the stylesheets grew these names while the
    guard was Python-only."""
    message = failure_for(tmp_path, rel)
    assert message and "records when the file was written" in message, message


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


def test_close_copy_is_not_a_dated_name(tmp_path):
    assert failure_for(tmp_path, "tests/test_close_copy.py") is None


@pytest.mark.parametrize(
    "rel",
    [
        # An ordered stylesheet slice: the number is cascade position, and the
        # word after it is the subject. Neither is a date.
        "src/medagent/web/css/02-artifacts.css",
        "src/medagent/web/css/styles.css",
        "src/medagent/web/js/structured-ui-cards.js",
        "src/medagent/web/js/execution-ui-observation.js",
        "src/medagent/web/index.html",
        # ``and`` in a test name joins two subjects the test really covers.
        "tests/test_worry_and_goal.py",
        # Node's default test glob, and a plain CommonJS entry point.
        "tests/ui/workspace.spec.mjs",
        "desktop/main.cjs",
    ],
)
def test_informative_names_are_accepted(tmp_path, rel):
    assert failure_for(tmp_path, rel) is None


def test_the_real_tree_passes():
    assert rules.main([]) == 0
