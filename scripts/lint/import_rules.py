#!/usr/bin/env python3
"""Import-direction guard.

Why this exists
---------------
``module_size.py`` only bounds *how big* a file may get.  It says nothing about
*who is allowed to import whom*, so a module can stay small and still wreck the
architecture by reaching sideways into an unrelated package.  Size and coupling
are different debts; this check owns the second one.

The layering
------------
The 2026-10 modularisation left a three-tier shape.  It is not enforced by
directory nesting, so it is declared here explicitly:

* **capability** (``extensions``, ``kg``) — formula/scale/glucose engines and the
  knowledge graph.  Widely reused, so they must stay ignorant of every feature.
* **feature** (``consultation``, ``conclusion``, ``agent``, ``execution``,
  ``patient_records``, ``subagents``) — the clinical workflows.  They may use
  capabilities and the flat kernel, never each other.
* **app** (``server``) — HTTP surface, sits on top of everything.  Only the
  process entry points may import it.

Imports that point *up* this stack are inversions: a capability that knows about
a feature can no longer be reused or tested in isolation, and two features that
import each other cannot be changed independently.

Rules
-----
R1  capability packages must not import feature or app packages.
R2  feature packages must not import each other (capabilities and the flat
    kernel are fair game).
R3  only entry points (``cli``, ``desktop_service``) may import ``server``.
R4  no import cycles.  Cycles are detected on *module-level* edges only: a
    function-level (late-bound) import is the canonical cycle-break and cannot
    trigger a circular-import error, so it is not counted as a cycle.  Every
    cycle the graph now contains is a genuine violation and fails the check;
    there is no tolerated baseline left.
R5  the flat top-level module count must not grow.  It is the shared kernel and
    the natural place for new debt to accumulate.
R6  the shared kernel must not import feature packages.  The kernel is the
    vocabulary every layer depends on, so a kernel module that reaches into a
    clinical workflow stops being reusable by the other workflows — and the
    kernel is precisely where new coupling accumulates.  Entry points are
    exempt: they are the app surface and drive the workflows on purpose.

Run locally::

    python scripts/lint/import_rules.py

Pass ``--root <dir>`` to point the check at a fixture tree; the test-suite uses
that to prove each rule actually fires.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / "medagent"

CAPABILITY_PACKAGES = frozenset({"extensions", "kg"})
FEATURE_PACKAGES = frozenset(
    {"consultation", "conclusion", "agent", "execution", "patient_records", "subagents"}
)
APP_PACKAGES = frozenset({"server"})
ENTRY_MODULES = frozenset({"cli", "desktop_service", "__main__"})

# Existing module-level cross-package edges, as (importer, imported).  These are
# hard coupling, not late-bound imports: the target loads whenever the importer
# loads, so neither side can be changed or tested alone.
#
# Empty on purpose.  Every edge that used to be here has been paid off:
#
#   * five ``subagents`` modules imported three fact tables that lived in
#     ``conclusion``; the tables moved to the dependency-free ``facts``
#     package, so ``subagents -> conclusion`` is gone;
#   * ``agent -> consultation`` was not a workflow coupling at all: the
#     adaptive engine *subclasses* the deterministic one, so the two packages
#     were one feature split in two.  ``agent`` is now
#     ``consultation.agent``, a subpackage, and the edge is intra-package.
#
# Any feature -> feature edge the graph now contains is a new violation and
# fails the check.
EDGE_BASELINE: frozenset[tuple[str, str]] = frozenset()

# Kernel modules that import a feature package at module level.  The kernel is
# the shared vocabulary every layer depends on, so a kernel module reaching
# into a clinical workflow is a primitive that is no longer reusable by any
# other workflow — and the kernel is exactly where new coupling accumulates,
# because it is where new code naturally lands.
#
# R6 is therefore stated as "must not grow", not "must be zero".  Every edge
# that used to be here has been paid off:
#
#   * ``hypothesis_ranking`` / ``question_ordering`` / ``out_of_pathway``
#     reached into ``conclusion`` for one predicate
#     (``chief_matches_pathway``, now in the kernel's ``reasoning``) and one
#     table (``PRIOR_RANK``, now in ``facts``).
#   * ``recognition_jobs`` drove the ``patient_records`` store, so it moved
#     into that package where it belongs.
#   * ``runtime`` and ``workspace`` are composition roots that assemble the
#     feature packages, so they moved into ``server`` — the app layer that
#     already depends on every feature.
#
# The baseline is empty on purpose: the kernel now has no feature dependency
# at all, so any kernel -> feature edge is a new violation and must fail.
# Entry points (``cli``, ``desktop_service``, ``__main__``) are exempt: they
# are the app surface and are *supposed* to drive the workflows.
KERNEL_EDGE_BASELINE: frozenset[tuple[str, str]] = frozenset()

# Existing cycles, as member sets.  Each entry should disappear as the cycle is
# broken.  Historically this held five member sets; all have been broken by
# extracting shared low-level modules (formulas_core, execution/paths,
# conclusion/assets) and late-binding the last kernel<->capability edge
# (knowledge -> extensions.builtin).  It is kept empty on purpose: any cycle the
# graph now contains is a *new* violation and must fail, not be tolerated.
CYCLE_BASELINE: tuple[frozenset[str], ...] = ()

# Flat modules directly under src/medagent/.  Frozen: new code belongs in a
# package, not in the shared kernel.  Lower this number as modules move out.
TOP_LEVEL_CEILING = 62


def module_name(path: pathlib.Path, root: pathlib.Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    name = ".".join(rel.parts)
    if name == "__init__":
        return ""
    if name.endswith(".__init__"):
        return name[: -len(".__init__")]
    return name


def imported_names(tree: ast.AST, name: str) -> dict[str, bool]:
    """Map absolute dotted targets to *is this a module-level import?*.

    The distinction matters.  A module-level import is a hard dependency: the
    target is loaded whenever this module loads, so the coupling is structural.
    An import inside a function is a deliberate late binding — usually written
    precisely to break a cycle — and is treated as a warning, not a failure.
    Conflating the two makes the rule unusable: every sanctioned cycle-break
    would look like an architecture violation.
    """
    deps: dict[str, bool] = {}

    def add(target: str, module_level: bool) -> None:
        # A hard dependency anywhere wins: if the same edge also appears at
        # module level, it is a hard dependency.
        deps[target] = deps.get(target, False) or module_level

    def absolute(module: str) -> str | None:
        if module.startswith("medagent."):
            return module[len("medagent.") :]
        return None

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Lambda(self, node: ast.Lambda) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module_level = self.depth == 0
            if node.level:
                base = name.split(".")[:-1] if name else []
                up = node.level - 1
                prefix = base[: len(base) - up] if up else base
                if node.module is None:
                    # ``from . import sibling`` — the target lives in the alias
                    # list, not in ``module``.  Missing this hides most
                    # intra-package wiring, and with it most cycles.
                    for alias in node.names:
                        add(".".join([*prefix, alias.name]), module_level)
                else:
                    add(".".join([*prefix, node.module]), module_level)
            elif node.module:
                target = absolute(node.module)
                if target:
                    add(target, module_level)
            self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                target = absolute(alias.name)
                if target:
                    add(target, self.depth == 0)
            self.generic_visit(node)

    Visitor().visit(tree)
    return deps


def collect_modules(
    root: pathlib.Path,
) -> tuple[dict[str, dict[str, bool]], list[pathlib.Path], set[str]]:
    files: list[pathlib.Path] = []
    trees: dict[str, ast.AST] = {}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files.append(path)
        trees[module_name(path, root)] = ast.parse(path.read_text(encoding="utf-8"))
    known = set(trees)
    graph: dict[str, dict[str, bool]] = {}
    for name, tree in trees.items():
        edges: dict[str, bool] = {}
        for target, module_level in imported_names(tree, name).items():
            resolved = resolve(target, known)
            if resolved and resolved != name:
                edges[resolved] = edges.get(resolved, False) or module_level
        graph[name] = edges
    return graph, files, known


def resolve(target: str, known: set[str]) -> str | None:
    """Map an import target onto the deepest known module prefix."""
    if target in known:
        return target
    parts = target.split(".")
    for size in range(len(parts), 0, -1):
        candidate = ".".join(parts[:size])
        if candidate in known:
            return candidate
    return None


def package_of(name: str, known: set[str]) -> str | None:
    """Owning package of a module, or None for a flat kernel module.

    A package's own ``__init__`` is keyed by the bare package name, so ``server``
    and ``server.core`` must both resolve to ``server`` — otherwise the package
    root is misread as a kernel module and every rule about it goes quiet.
    """
    if name in known and "." not in name:
        return name if f"{name}.__init__" in known or any(
            k.startswith(f"{name}.") for k in known
        ) else None
    return name.split(".")[0] if "." in name else None


def layer_of(name: str, known: set[str]) -> str:
    pkg = package_of(name, known)
    if pkg in CAPABILITY_PACKAGES:
        return "capability"
    if pkg in FEATURE_PACKAGES:
        return "feature"
    if pkg in APP_PACKAGES:
        return "app"
    return "kernel"


def check_layering(
    graph: dict[str, dict[str, bool]], known: set[str]
) -> tuple[list[str], list[str]]:
    """Split layering violations into hard failures and late-binding warnings."""
    failures: list[str] = []
    warnings: list[str] = []
    for source, targets in sorted(graph.items()):
        src_layer = layer_of(source, known)
        for target, module_level in sorted(targets.items()):
            dst_layer = layer_of(target, known)
            rule: str | None = None
            detail = ""
            if src_layer == "capability" and dst_layer in {"feature", "app"}:
                rule = "R1"
                detail = "capabilities must not depend on features"
            elif src_layer == "feature" and dst_layer == "feature":
                if package_of(source, known) != package_of(target, known):
                    rule = "R2"
                    detail = (
                        "feature packages must stay independent; move the shared "
                        "part into a capability package"
                    )
            elif dst_layer == "app" and source not in ENTRY_MODULES:
                if package_of(source, known) != package_of(target, known):
                    rule = "R3"
                    detail = f"only {sorted(ENTRY_MODULES)} may depend on the app layer"
            elif (
                src_layer == "kernel"
                and dst_layer == "feature"
                and source not in ENTRY_MODULES
            ):
                rule = "R6"
                detail = (
                    "the shared kernel must not depend on a feature package; "
                    "lift the shared part into the kernel instead"
                )
            if rule is None:
                continue
            if (source, target) in EDGE_BASELINE:
                continue
            if (source, target) in KERNEL_EDGE_BASELINE:
                continue
            message = f"{rule} {source} imports {target}: {detail}"
            if module_level:
                failures.append(message)
            else:
                warnings.append(
                    f"{message} (inside a function, so late-bound — fix when convenient)"
                )
    return failures, warnings


def find_cycles(graph: dict[str, set[str]]) -> list[set[str]]:
    """Tarjan's SCC; any component larger than one module is a cycle."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []
    counter = [0]

    def visit(node: str) -> None:
        index[node] = low[node] = counter[0]
        counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, ()):
            if target not in index:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in on_stack:
                low[node] = min(low[node], index[target])
        if low[node] == index[node]:
            component: set[str] = set()
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.add(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(component)

    for node in graph:
        if node not in index:
            visit(node)
    return components


def check_cycles(graph: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    notes: list[str] = []
    components = find_cycles(graph)
    for component in components:
        if any(component <= allowed for allowed in CYCLE_BASELINE):
            continue
        members = ", ".join(sorted(component))
        sample = sorted(
            f"{src} -> {dst}"
            for src in component
            for dst in graph.get(src, ())
            if dst in component
        )[:3]
        failures.append(
            f"R4 new import cycle among {len(component)} modules: {members}\n"
            f"       e.g. {'; '.join(sample)}\n"
            "       break it by inverting one edge (pass a value in, or import "
            "inside the function) rather than widening CYCLE_BASELINE"
        )
    for allowed in CYCLE_BASELINE:
        live = [c for c in components if c <= allowed]
        if not live:
            notes.append(
                "R4 baselined cycle is gone: "
                f"{', '.join(sorted(allowed))} — remove it from CYCLE_BASELINE"
            )
        elif all(len(c) < len(allowed) for c in live):
            notes.append(
                "R4 baselined cycle shrank: "
                f"{', '.join(sorted(allowed))} — tighten CYCLE_BASELINE"
            )
    return failures, notes


def count_top_level(files: list[pathlib.Path], root: pathlib.Path) -> int:
    return sum(
        1 for path in files if path.parent == root and path.name != "__init__.py"
    )


def run(
    root: pathlib.Path, ceiling: int
) -> tuple[list[str], list[str], dict[str, int]]:
    """Return (failures, notes, stats) for the tree at *root*."""
    graph, files, known = collect_modules(root)
    failures: list[str] = []
    layering_failures, layering_warnings = check_layering(graph, known)
    failures.extend(layering_failures)
    # Cycle detection runs on the *module-level* graph only.  A function-level
    # (late-bound) import is the canonical cycle-break: it does not load the
    # target when this module loads, so it cannot trigger a circular-import
    # error and must not count as an architecture cycle.  ``check_layering``
    # already treats such edges as warnings; the SCC pass must agree.
    module_level_graph = {
        src: {dst for dst, module_level in edges.items() if module_level}
        for src, edges in graph.items()
    }
    cycle_failures, notes = check_cycles(module_level_graph)
    failures.extend(cycle_failures)
    notes.extend(layering_warnings)

    top_level = count_top_level(files, root)
    if top_level > ceiling:
        failures.append(
            f"R5 {top_level} flat modules under {root.name}/, ceiling is "
            f"{ceiling}: put the new module in a package instead"
        )
    stats = {
        "modules": len(graph),
        "cycles": len(find_cycles(module_level_graph)),
        "flat": top_level,
        "frozen_edges": sum(
            1 for source, target in EDGE_BASELINE if target in graph.get(source, {})
        ),
        "frozen_kernel_edges": sum(
            1
            for source, target in KERNEL_EDGE_BASELINE
            if target in graph.get(source, {})
        ),
    }
    return failures, notes, stats


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=PACKAGE_ROOT,
        help="package directory to scan (default: src/medagent)",
    )
    parser.add_argument(
        "--ceiling",
        type=int,
        default=TOP_LEVEL_CEILING,
        help="maximum number of flat modules directly under --root",
    )
    args = parser.parse_args(argv)

    failures, notes, stats = run(args.root, args.ceiling)

    for note in notes:
        print(f"note: {note}")

    if failures:
        print("import-rules check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            f"\n{len(failures)} violation(s). The layering is capability -> "
            "feature -> app; imports may only point down.",
            file=sys.stderr,
        )
        return 1

    print(
        f"import-rules check passed: {stats['modules']} modules, "
        f"{stats['cycles']} baselined cycle(s), "
        f"{stats['frozen_edges']} frozen cross-package edge(s), "
        f"{stats['frozen_kernel_edges']} frozen kernel edge(s), "
        f"{stats['flat']} flat modules (ceiling {args.ceiling})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
