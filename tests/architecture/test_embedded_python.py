"""JavaScript's trusted Python entry points must survive Python module moves."""

import importlib
from pathlib import Path
import re


def test_embedded_host_entry_points_resolve_to_callable_functions():
    root = Path(__file__).resolve().parents[2]
    found = set()
    scripts = [
        script
        for directory in [root / "integrations", root / "tests/fixtures"]
        for script in directory.rglob("*.mjs")
    ]
    for script in scripts:
        imports = re.findall(
            r"from (medagent\.[\w.]+) import (\w+)", script.read_text(encoding="utf-8")
        )
        for module, name in imports:
            entry = getattr(importlib.import_module(module), name)
            assert callable(entry), (script, module, name)
            found.add((module, name))
    assert {
        ("medagent.execution.capabilities", "main"),
        ("medagent.execution.changes", "main"),
        ("medagent.execution.checkpoints", "main"),
    } <= found
