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

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "lint" / "naming.py"


def _load():
    spec = importlib.util.spec_from_file_location("naming", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load()


def failure_for(root: pathlib.Path, rel: str, body: str = "") -> str | None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
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
    assert failure_for(tmp_path, "tests/consultation/test_agent.py") is None
    assert failure_for(tmp_path, "tests/conftest.py") is None


def test_test_module_at_the_tests_root_is_rejected(tmp_path):
    """R4: the suite is grouped by package; a loose file starts the drift again."""
    message = failure_for(tmp_path, "tests/test_loose.py")
    assert message and "tests/ is not a place for test modules" in message, message


def test_test_module_in_an_area_is_accepted(tmp_path):
    assert failure_for(tmp_path, "tests/consultation/test_turn.py") is None
    assert failure_for(tmp_path, "tests/architecture/test_naming.py") is None


def test_helper_module_under_tests_needs_no_prefix_outside_support(tmp_path):
    """R3 now reaches every level, not just the root."""
    message = failure_for(tmp_path, "tests/consultation/builders.py")
    assert message and "test_*.py" in message, message
    assert failure_for(tmp_path, "tests/support/builders.py") is None


def test_importing_another_test_module_is_rejected(tmp_path):
    """R5: a fixture imported from a test file has no home of its own."""
    message = failure_for(
        tmp_path,
        "tests/records/test_things.py",
        body="from tests.consultation.test_agent import make_state\n",
    )
    assert message and "imports another test module" in message, message


def test_importing_the_support_tree_stays_allowed(tmp_path):
    assert (
        failure_for(
            tmp_path,
            "tests/records/test_things.py",
            body="from tests.support.builders import closed_state\n",
        )
        is None
    )


def test_import_shapes_other_than_from_x_import_y_are_caught_too(tmp_path):
    """Submodule paths and plain imports both count -- the first pass of the
    regex missed five of them."""
    for body in (
        "import tests.consultation.test_agent\n",
        "from tests.consultation import test_agent as offline\n",
    ):
        message = failure_for(tmp_path, "tests/records/test_things.py", body=body)
        assert message and "imports another test module" in message, (body, message)


def test_support_helpers_keep_their_own_names(tmp_path):
    assert failure_for(tmp_path, "tests/support/builders.py") is None
    assert failure_for(tmp_path, "tests/support/scripted.py") is None


def test_core_is_allowed_because_it_is_a_consistent_convention(tmp_path):
    assert failure_for(tmp_path, "src/pkg/core.py") is None
    assert failure_for(tmp_path, "src/pkg/agent/core.py") is None


def test_close_copy_is_not_a_dated_name(tmp_path):
    assert failure_for(tmp_path, "tests/conclusion/test_close_copy.py") is None


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
        "tests/conclusion/test_worry_and_goal.py",
        # Node's default test glob, and a plain CommonJS entry point.
        "tests/ui/workspace.spec.mjs",
        "desktop/main.cjs",
    ],
)
def test_informative_names_are_accepted(tmp_path, rel):
    assert failure_for(tmp_path, rel) is None


def test_the_real_tree_passes():
    assert rules.main([]) == 0
