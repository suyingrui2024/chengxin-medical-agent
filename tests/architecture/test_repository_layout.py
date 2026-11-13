"""Repository entry points and current documents have one discoverable home."""

from pathlib import Path
import json
import re
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]


def test_research_materials_have_a_single_home():
    for name in ("benchmarks", "experiments", "tools"):
        assert not (ROOT / name).exists(), f"Use research/{name}, not a new root directory"
        assert (ROOT / "research" / name).is_dir()


def test_root_docs_only_contains_the_navigation():
    assert {p.name for p in (ROOT / "docs").glob("*.md")} == {"README.md"}


def test_editor_workspace_is_scoped_to_this_project():
    path = ROOT / ".vscode/medical-agent.code-workspace"
    config = json.loads(path.read_text())
    assert [(path.parent / folder["path"]).resolve() for folder in config["folders"]] == [ROOT]
    settings = json.loads((ROOT / ".vscode/settings.json").read_text())
    assert settings["files.exclude"]["node_modules"] is True
    assert "src" not in settings["files.exclude"]
    assert ".env" not in settings["files.exclude"]


def test_current_markdown_links_resolve():
    archive = json.loads((ROOT / "docs/evaluations/archive-manifest.json").read_text())
    archived = {item["path"] for item in archive["files"]}
    documents = [ROOT / "README.md", ROOT / "config/README.md", ROOT / "research/README.md"]
    for group in ("getting-started", "architecture", "features", "development"):
        documents.extend((ROOT / "docs" / group).glob("*.md"))
    broken = []
    for document in documents:
        text = re.sub(r"```.*?```", "", document.read_text(), flags=re.S)
        for link in re.findall(r"\]\(([^)\n]+)\)", text):
            link = link.strip("<>")
            if re.match(r"(?:[a-z]+:|/|#)", link):
                continue
            target = (document.parent / unquote(link.split("#", 1)[0])).resolve()
            relative = target.relative_to(ROOT).as_posix() if target.is_relative_to(ROOT) else ""
            if not target.exists() and relative not in archived:
                broken.append((document.relative_to(ROOT).as_posix(), link))
    assert not broken, f"Broken current document links: {broken}"
