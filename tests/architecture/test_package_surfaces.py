"""A package must declare its surface, and the surface must be the one in use.

``conclusion/__init__.py`` re-exported 118 names for no reason other than that
the module it replaced had imported them; `from medagent.conclusion import
CaseState` answered, so the conclusion package appeared to own `CaseState`.
Turning on F401 found 467 such names. They are gone, and each package now says
what it exports.

Two things are checked here, and they pull in opposite directions on purpose:
the declared surface must be *complete* (every name resolves) and *justified*
(somebody outside the module imports it). A name added to `__all__` "for later"
fails the second check, which is the same mistake as re-exporting an import.
"""
from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = (ROOT / "src").resolve()

#: Modules that publish a surface: the three packages whose `__init__.py` used
#: to be the original monolithic module, plus the constants module that
#: re-exports the shared fact tables on purpose.
SURFACES = [
    "medagent.consultation",
    "medagent.consultation.agent",
    "medagent.conclusion",
    "medagent.conclusion.constants",
    "medagent.execution",
    "medagent.patient_records",
    "medagent.patient_records.store",
    "medagent.server",
    "medagent.subagents",
    "medagent.subagents.coordinator",
]


def module_path(module: str) -> pathlib.Path:
    return SRC / (module.replace(".", "/") + ".py")


def dotted(path: pathlib.Path) -> str:
    parts = list(path.resolve().relative_to(SRC).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_from(path: pathlib.Path, node: ast.ImportFrom) -> str:
    """Absolute module a relative ``from ..x import y`` refers to."""
    if node.level == 0:
        return node.module or ""
    base = dotted(path).split(".")
    anchor = base if path.name == "__init__.py" else base[:-1]
    if node.level > 1:
        anchor = anchor[: len(anchor) - (node.level - 1)]
    return ".".join(anchor + ([node.module] if node.module else []))


def consumers() -> dict[str, set[str]]:
    """module -> names other files read from it.

    Both syntaxes count. ``from medagent.conclusion import x`` is the obvious
    one; ``from medagent import conclusion as c`` followed by ``c.x`` is the
    one tests use, and counting only the first reported live names as dead.
    """
    found: dict[str, set[str]] = {}
    for base in ("src", "tests", "scripts"):
        for path in (ROOT / base).rglob("*.py"):
            if "docs/" in path.as_posix():
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            aliases: dict[str, str] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    target = imported_from(path, node)
                    if target.startswith("medagent"):
                        found.setdefault(target, set()).update(a.name for a in node.names)
                    for alias in node.names:
                        bound = target + "." + alias.name
                        if bound.startswith("medagent"):
                            aliases[alias.asname or alias.name] = bound
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("medagent"):
                            aliases[alias.asname or alias.name.split(".")[-1]] = alias.name
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    target = aliases.get(node.value.id)
                    if target:
                        found.setdefault(target, set()).add(node.attr)
    return found


@pytest.mark.parametrize("module", SURFACES)
def test_surface_resolves(module):
    exported = importlib.import_module(module).__all__
    missing = [name for name in exported if not hasattr(importlib.import_module(module), name)]
    assert missing == [], f"{module}.__all__ 里有取不到的名字：{missing}"


@pytest.mark.parametrize("module", SURFACES)
def test_surface_is_sorted_and_unique(module):
    exported = importlib.import_module(module).__all__
    assert exported == sorted(exported), f"{module}.__all__ 未排序"
    assert len(exported) == len(set(exported)), f"{module}.__all__ 有重复"


def defined_in_package(module: str) -> set[str]:
    """Names the package defines somewhere under its own directory."""
    root = SRC / module.replace(".", "/")
    if not root.is_dir():
        root = root.parent
    found: set[str] = set()
    for path in root.rglob("*.py"):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign):
                found |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
    return found


@pytest.mark.parametrize("module", SURFACES)
def test_every_export_is_used_or_owned(module):
    """An export must be either wanted somewhere, or the package's own work.

    Both halves matter. `conclusion` used to re-export `CaseState`, which is
    neither: a reader would take it for the conclusion package's type. But
    `execution` exports `ExecutionSession` with no caller today, and that is
    fine -- the class lives here, so this is where it belongs. The rule is
    about accidental re-exports, not about forbidding a package from naming its
    own headline class.
    """
    exported = set(importlib.import_module(module).__all__)
    used = consumers().get(module, set())
    owned = defined_in_package(module)
    unjustified = sorted(exported - used - owned)
    assert unjustified == [], f"{module} 导出了既没人用、也不属于本包的名字：{unjustified}"
