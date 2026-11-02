"""Documentation must not point at modules that do not exist.

The kernel regrouping moved 59 modules. Twelve documents kept naming the old
paths, and nothing noticed until someone followed one and landed on a missing
file. Fixing the documents by hand only helps until the next refactor, so the
rule is now enforced: a document may name a moved module **only if** the move is
recorded in `docs/模块路径变更对照.md`.

That is the same discipline the code already uses for architecture rules — a
baseline you update deliberately, rather than drift nobody sees.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAPPING = ROOT / "docs" / "模块路径变更对照.md"

#: Any ``src/medagent/...py`` mentioned in prose.
REFERENCE = re.compile(r"src/medagent/[\w/]+\.py")
#: A row of the mapping table: | `old` | `new` |
ROW = re.compile(r"^\|\s*`(src/medagent/[\w/]+\.py)`\s*\|\s*`([^`]+)`\s*\|$", re.M)

#: Generated evaluation output, vendored trees and project metadata are not docs.
SKIP_PARTS = ("docs/evaluations", "node_modules", ".git")

#: ``xxx`` is the conventional placeholder for "some module here" — the mapping
#: table's intro and the notes pointing at it use it. It is not a claim that a
#: file by that name exists.
PLACEHOLDER = "xxx"


def recorded_moves() -> dict[str, str]:
    return dict(ROW.findall(MAPPING.read_text(encoding="utf-8")))


def documents() -> list[pathlib.Path]:
    return [
        p
        for p in ROOT.rglob("*.md")
        if not any(part in p.as_posix() for part in SKIP_PARTS)
        # The mapping table is the source of truth: its left column is, by
        # definition, a list of paths that no longer exist.
        and p != MAPPING
    ]


def offenders_in(text: str, recorded: dict[str, str]) -> list[str]:
    """Module paths the text names that neither exist nor are recorded as moved."""
    return sorted(
        {
            match.group(0)
            for match in REFERENCE.finditer(text)
            if PLACEHOLDER not in match.group(0)
            and not (ROOT / match.group(0)).exists()
            and match.group(0) not in recorded
        }
    )


def test_the_mapping_table_is_present_and_parseable():
    """A silently empty table would make the check below vacuous."""
    moves = recorded_moves()
    assert len(moves) >= 20, f"only parsed {len(moves)} rows from {MAPPING.name}"


def test_every_recorded_target_still_exists():
    """A stale mapping is worse than none: it sends the reader somewhere wrong."""
    missing = [
        (old, new) for old, new in recorded_moves().items() if not (ROOT / new).exists()
    ]
    assert not missing, f"mapping points at paths that do not exist: {missing}"


def test_no_document_names_a_module_that_does_not_exist():
    recorded = recorded_moves()
    broken = {
        document.relative_to(ROOT).as_posix(): offenders_in(
            document.read_text(encoding="utf-8"), recorded
        )
        for document in documents()
    }
    broken = {name: paths for name, paths in broken.items() if paths}
    assert not broken, (
        "these documents name modules that do not exist and are not in "
        f"{MAPPING.name}: {broken}. Either fix the path or add a row to the mapping "
        "table."
    )


def test_the_check_would_catch_a_bad_reference():
    """Meta: the guard above is only worth having if it can fail."""
    recorded = recorded_moves()
    assert offenders_in("见 `src/medagent/definitely_not_here.py`", recorded) == [
        "src/medagent/definitely_not_here.py"
    ]
    # A recorded move is allowed to be named.
    moved = next(iter(recorded))
    assert offenders_in(f"见 `{moved}`", recorded) == []
    # A live path is allowed.
    assert offenders_in("见 `src/medagent/core/domain.py`", recorded) == []
    # The placeholder is not a claim about a file.
    assert offenders_in("见 `src/medagent/xxx.py`", recorded) == []
