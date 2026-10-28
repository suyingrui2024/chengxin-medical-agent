"""Seed the concept store from content the repository already owns.

Nothing here is invented. Each concept is derived from a file that is
already part of the product — the capability registry, the red-flag
list, the reference ranges, the department map — so the seed inherits
whatever review status those files carry, and nothing more.

Wider terminology (the NHC term list, ICD-10) arrives through
:mod:`medagent.kg.loaders`, not here.
"""

from __future__ import annotations

import json
from pathlib import Path

from .store import Concept, ConceptStore, Relation

CONTENT = Path(__file__).resolve().parents[1] / "content"


def _read(name: str) -> dict:
    return json.loads((CONTENT / name).read_text(encoding="utf-8"))


def _review_status(payload: dict, default: str) -> str:
    if payload.get("review_status"):
        return str(payload["review_status"])
    if payload.get("reviewed_by"):
        return f"reviewed_by:{payload['reviewed_by']}"
    return default


def capability_concepts(registry) -> list[Concept]:
    concepts = []
    for item in registry.descriptions():
        if not item.get("patient_form"):
            continue
        kind = "scale" if isinstance(item.get("scale"), dict) else "formula"
        aliases = tuple(item.get("aliases") or ())
        if not aliases:
            continue
        concepts.append(
            Concept(
                id=f"capability:{item['id']}",
                kind=kind,
                canonical=item["name"],
                source=f"capability-registry:{item['id']}@{item.get('version', '?')}",
                review_status=str(item.get("review_status", "engineering_verified_not_clinical")),
                aliases=aliases,
            )
        )
    return concepts


def red_flag_concepts() -> list[Concept]:
    payload = _read("red_flags.json")
    status = _review_status(payload, "engineering_seed_not_reviewed")
    concepts = []
    for flag in payload["flags"]:
        # The label is what the product shows; the id is the stable key.
        concepts.append(
            Concept(
                id=f"red_flag:{flag['id']}",
                kind="red_flag",
                canonical=flag["label"],
                source=f"content/red_flags.json@{payload.get('version', '?')}",
                review_status=status,
                note=f"level={flag.get('level', '?')}",
                aliases=(flag["label"],),
            )
        )
    return concepts


def lab_concepts() -> list[Concept]:
    payload = _read("reference_ranges.json")
    status = _review_status(payload, "engineering_seed_not_reviewed")
    concepts = []
    for key, item in payload["items"].items():
        concepts.append(
            Concept(
                id=f"lab:{key}",
                kind="lab",
                canonical=item["name"],
                source=f"content/reference_ranges.json@{payload.get('version', '?')}",
                review_status=status,
                note=f"unit={item.get('unit', '')}",
                aliases=(item["name"], key.upper()),
            )
        )
    return concepts


def department_concepts() -> list[Concept]:
    payload = _read("departments.json")
    status = _review_status(payload, "engineering_seed_not_reviewed")
    names: set[str] = set()
    # by_group maps a symptom group to {department, reason}.
    for entry in payload.get("by_group", {}).values():
        name = entry.get("department") if isinstance(entry, dict) else entry
        if isinstance(name, str):
            names.add(name)
    urgent = payload.get("urgent", {}).get("department")
    if urgent:
        names.add(urgent)
    return [
        Concept(
            id=f"department:{name}",
            kind="department",
            canonical=name,
            source=f"content/departments.json@{payload.get('version', '?')}",
            review_status=status,
            aliases=(name,),
        )
        for name in sorted(names)
    ]


def build(registry, store: ConceptStore | None = None) -> ConceptStore:
    store = store or ConceptStore.in_memory()
    concepts = (
        capability_concepts(registry)
        + red_flag_concepts()
        + lab_concepts()
        + department_concepts()
    )
    store.add(concepts)
    store.set_meta("seed", "repository-content")
    store.set_meta("seeded_concepts", str(len(concepts)))
    return store


def relations() -> list[Relation]:
    """Placeholder: the seed adds no relations.

    Relations are where clinical judgement lives (symptom -> condition),
    so they must come from a reviewed source, not from us filling gaps.
    """
    return []
