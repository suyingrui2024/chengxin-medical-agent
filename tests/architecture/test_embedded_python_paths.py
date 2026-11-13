"""Module moves must update Python entry points embedded in JS/PowerShell."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
IMPORT = re.compile(r"\b(?:from|import) (medagent(?:\.[a-zA-Z_]\w*)+)")


def test_embedded_python_imports_resolve_to_current_modules():
    broken = []
    for folder in ("integrations", "desktop", "scripts", "tests/fixtures"):
        for file in (ROOT / folder).rglob("*"):
            if file.suffix not in {".mjs", ".cjs", ".ps1"}:
                continue
            for name in IMPORT.findall(file.read_text()):
                target = ROOT / "src" / name.replace(".", "/")
                if (
                    not target.with_suffix(".py").is_file()
                    and not (target / "__init__.py").is_file()
                ):
                    broken.append((file.relative_to(ROOT).as_posix(), name))
    assert not broken, f"Embedded Python module paths are stale: {broken}"
