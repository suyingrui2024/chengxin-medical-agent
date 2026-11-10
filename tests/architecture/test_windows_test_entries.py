"""Windows acceptance must keep pointing at real tests after package moves."""

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ENTRIES = [
    ROOT / ".github/workflows/windows-native.yml",
    *sorted((ROOT / "scripts/platform/windows").glob("*.ps1")),
]


def test_native_and_packaged_python_test_files_exist():
    found = set()
    for entry in ENTRIES:
        for path in re.findall(r"\btests[/\\][\w/\\-]+\.py", entry.read_text(encoding="utf-8")):
            normalized = path.replace("\\", "/")
            assert (ROOT / normalized).is_file(), (entry, normalized)
            found.add(normalized)
    assert "tests/execution/test_windows_execution.py" in found


def test_workflow_unittest_modules_classes_and_methods_exist():
    source = ENTRIES[0].read_text(encoding="utf-8")
    references = re.findall(r"\btests(?:\.[A-Za-z_]\w*)+", source)
    assert references, "No unittest entry points found in the Windows workflow"
    for reference in references:
        parts = reference.split(".")
        module_end = next(i for i, part in enumerate(parts) if part.startswith("test_")) + 1
        path = ROOT.joinpath(*parts[:module_end]).with_suffix(".py")
        assert path.is_file(), reference
        body = ast.parse(path.read_text(encoding="utf-8")).body
        for name in parts[module_end:]:
            node = next((node for node in body if getattr(node, "name", None) == name), None)
            assert node is not None, reference
            body = getattr(node, "body", [])
