"""The import-direction guard must actually fire.

A structural check that silently passes is worse than no check: it advertises a
guarantee the codebase does not have.  Each test builds a throwaway package tree
and asserts the matching rule trips, so a refactor of the script cannot quietly
turn a rule into a no-op.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "lint" / "import_rules.py"


def _load():
    spec = importlib.util.spec_from_file_location("import_rules", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load()


def build(root: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


def test_capability_may_not_import_a_feature(tmp_path):
    build(
        tmp_path,
        {
            "kg/__init__.py": "",
            "kg/engine.py": "from ..consultation import core\n",
            "consultation/__init__.py": "",
            "consultation/core.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert any(f.startswith("R1") for f in failures), failures


def test_feature_packages_may_not_import_each_other(tmp_path):
    build(
        tmp_path,
        {
            "consultation/__init__.py": "",
            "consultation/core.py": "from ..conclusion import sheet\n",
            "conclusion/__init__.py": "",
            "conclusion/sheet.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert any(f.startswith("R2") for f in failures), failures


def test_feature_may_import_a_capability(tmp_path):
    build(
        tmp_path,
        {
            "consultation/__init__.py": "",
            "consultation/core.py": "from ..extensions import formulas\n",
            "extensions/__init__.py": "",
            "extensions/formulas.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures


def test_only_entry_points_may_import_the_app_layer(tmp_path):
    build(
        tmp_path,
        {
            "runtime.py": "from .server import core\n",
            "server/__init__.py": "",
            "server/core.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert any(f.startswith("R3") for f in failures), failures


def test_entry_point_may_import_the_app_layer(tmp_path):
    build(
        tmp_path,
        {
            "cli.py": "from .server import core\n",
            "server/__init__.py": "",
            "server/core.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures


def test_intra_package_wiring_is_not_an_app_layer_violation(tmp_path):
    build(
        tmp_path,
        {
            "server/__init__.py": "",
            "server/types.py": "",
            "server/agent.py": "from .types import Payload\n",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures


def test_a_late_bound_cross_package_import_only_warns(tmp_path):
    """A function-level import is how cycles are broken on purpose; do not fail it."""
    build(
        tmp_path,
        {
            "consultation/__init__.py": "",
            "consultation/core.py": (
                "def build():\n"
                "    from ..conclusion import sheet\n"
                "    return sheet\n"
            ),
            "conclusion/__init__.py": "",
            "conclusion/sheet.py": "",
        },
    )
    failures, notes, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures
    assert any("late-bound" in n for n in notes), notes


def test_a_baselined_edge_is_frozen_not_banned(tmp_path):
    """The known agent -> consultation coupling passes; a new one does not."""
    build(
        tmp_path,
        {
            "agent/__init__.py": "",
            "agent/_preamble.py": "from ..consultation import core\n",
            "agent/run.py": "from ..consultation import core\n",
            "consultation/__init__.py": "",
            "consultation/core.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert len(failures) == 1, failures
    assert "agent.run" in failures[0], failures


def test_a_new_cycle_is_rejected(tmp_path):
    build(
        tmp_path,
        {
            "alpha.py": "from . import beta\n",
            "beta.py": "from . import alpha\n",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert any(f.startswith("R4") for f in failures), failures


def test_a_baselined_cycle_is_tolerated(tmp_path):
    build(
        tmp_path,
        {
            "domain.py": "from . import consultation_subject\n",
            "consultation_subject.py": "from . import domain\n",
        },
    )
    failures, notes, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures
    # The fixture is tiny, so the other baselined cycles legitimately report as
    # gone.  Only this pair must stay quiet.
    assert not [n for n in notes if "consultation_subject, domain" in n], notes


def test_a_shrunken_baselined_cycle_is_reported_for_tightening(tmp_path):
    """One edge of the baselined domain pair is gone: say so, do not fail."""
    build(
        tmp_path,
        {
            "domain.py": "from . import consultation_subject\n",
            "consultation_subject.py": "",
        },
    )
    failures, notes, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures
    assert any("gone" in n for n in notes), notes


def test_flat_module_ceiling_is_enforced(tmp_path):
    build(tmp_path, {"one.py": "", "two.py": "", "three.py": ""})
    failures, _, stats = rules.run(tmp_path, ceiling=2)
    assert stats["flat"] == 3
    assert any(f.startswith("R5") for f in failures), failures


def test_package_init_files_do_not_count_as_flat_modules(tmp_path):
    build(tmp_path, {"__init__.py": "", "kg/__init__.py": ""})
    _, _, stats = rules.run(tmp_path, ceiling=0)
    assert stats["flat"] == 0


def test_relative_imports_from_nested_modules_resolve_to_the_package(tmp_path):
    """``from ..x`` inside a package must not be read as a top-level module."""
    build(
        tmp_path,
        {
            "kg/__init__.py": "",
            "kg/deep/__init__.py": "",
            "kg/deep/engine.py": "from .. import store\n",
            "kg/store.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert not failures, failures


def test_type_checking_imports_still_count(tmp_path):
    """A guarded import is still a dependency; ignoring it would hide coupling."""
    build(
        tmp_path,
        {
            "kg/__init__.py": "",
            "kg/engine.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from ..consultation import core\n"
            ),
            "consultation/__init__.py": "",
            "consultation/core.py": "",
        },
    )
    failures, _, _ = rules.run(tmp_path, ceiling=99)
    assert any(f.startswith("R1") for f in failures), failures


def test_the_real_tree_passes():
    failures, _, stats = rules.run(rules.PACKAGE_ROOT, rules.TOP_LEVEL_CEILING)
    assert not failures, failures
    assert stats["modules"] > 100


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
