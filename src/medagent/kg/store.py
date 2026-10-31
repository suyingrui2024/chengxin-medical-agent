"""A small, governed concept store — the knowledge base under the graph.

Design constraints, in order of importance:

1. **Every row carries its provenance.** A concept without a source and a
   review status is indistinguishable from a guess, and this product
   cannot afford guesses. ``source`` and ``review_status`` are therefore
   not nullable.
2. **Local only.** SQLite from the standard library. A desktop app that
   already ships a Python runtime does not need a graph server for a
   table of a few thousand terms.
3. **The graph is relations over concepts, not a separate system.** A
   relation is ``(src, predicate, dst)`` with the same provenance rules.

What this store deliberately does *not* do: invent clinical content. The
seed comes from what the repository already contains (capabilities, red
flags, reference ranges, departments); anything wider must be imported
from an official source through :mod:`medagent.kg.loaders`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

#: Concept kinds. Kept closed so a typo cannot silently create a new axis.
KINDS = (
    "symptom",
    "sign",
    "disease",
    "drug",
    "lab",
    "department",
    "scale",
    "formula",
    "red_flag",
    # An ICD chapter or block: a classification grouping, not a clinical
    # entity. Kept distinct so consumers filtering for "symptom" or
    # "disease" never pick one up.
    "category",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS concept (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    canonical     TEXT NOT NULL,
    source        TEXT NOT NULL,
    review_status TEXT NOT NULL,
    note          TEXT
);
CREATE TABLE IF NOT EXISTS alias (
    concept_id TEXT NOT NULL REFERENCES concept(id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (concept_id, text)
);
CREATE INDEX IF NOT EXISTS alias_text ON alias(text);
CREATE TABLE IF NOT EXISTS relation (
    src        TEXT NOT NULL,
    predicate  TEXT NOT NULL,
    dst        TEXT NOT NULL,
    source     TEXT NOT NULL,
    PRIMARY KEY (src, predicate, dst)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Concept:
    id: str
    kind: str
    canonical: str
    source: str
    review_status: str
    note: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"未知概念类型：{self.kind}")
        if not self.id or not self.canonical:
            raise ValueError("概念必须有 id 与规范名")
        if not self.source or not self.review_status:
            raise ValueError("概念必须有来源与审核状态")


@dataclass(frozen=True)
class Relation:
    src: str
    predicate: str
    dst: str
    source: str

    def __post_init__(self):
        if not self.source:
            raise ValueError("关系必须有来源")


class ConceptStore:
    """SQLite-backed store. Build once, read many times."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        # Idempotent (IF NOT EXISTS), so every entry point gets a usable
        # store without remembering to create the schema first.
        self.connection.executescript(SCHEMA)

    @classmethod
    def open(cls, path: str | Path) -> "ConceptStore":
        return cls(sqlite3.connect(str(path)))

    @classmethod
    def in_memory(cls) -> "ConceptStore":
        return cls(sqlite3.connect(":memory:"))

    def close(self) -> None:
        self.connection.close()

    def add(self, concepts: Iterable[Concept], relations: Iterable[Relation] = ()) -> None:
        with self.connection:
            for concept in concepts:
                self.connection.execute(
                    "INSERT OR REPLACE INTO concept"
                    " (id, kind, canonical, source, review_status, note)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        concept.id,
                        concept.kind,
                        concept.canonical,
                        concept.source,
                        concept.review_status,
                        concept.note,
                    ),
                )
                for text in concept.aliases:
                    # One alias, one concept. Without this, 「恶心」 is
                    # claimed by both ICD R11 「恶心和呕吐」 and Wikidata
                    # 「恶心」, and the matcher's answer depends on
                    # insertion order. Keep the more specific concept —
                    # the shorter canonical name — so the result is
                    # deterministic and the reader gets the narrow term.
                    clash = self.connection.execute(
                        "SELECT concept_id FROM alias WHERE text = ?", (text,)
                    ).fetchone()
                    if clash and clash["concept_id"] != concept.id:
                        other = self.connection.execute(
                            "SELECT canonical FROM concept WHERE id = ?",
                            (clash["concept_id"],),
                        ).fetchone()
                        if other and len(other["canonical"]) <= len(concept.canonical):
                            continue
                        self.connection.execute(
                            "DELETE FROM alias WHERE text = ? AND concept_id = ?",
                            (text, clash["concept_id"]),
                        )
                    self.connection.execute(
                        "INSERT OR REPLACE INTO alias (concept_id, text, source)"
                        " VALUES (?,?,?)",
                        (concept.id, text, concept.source),
                    )
            for relation in relations:
                self.connection.execute(
                    "INSERT OR REPLACE INTO relation (src, predicate, dst, source)"
                    " VALUES (?,?,?,?)",
                    (relation.src, relation.predicate, relation.dst, relation.source),
                )

    def set_meta(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value)
            )

    def meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.connection.execute("SELECT * FROM meta")}

    def concepts(self) -> list[dict]:
        rows = self.connection.execute("SELECT * FROM concept ORDER BY id")
        return [dict(row) for row in rows]

    def get(self, concept_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM concept WHERE id = ?", (concept_id,)
        ).fetchone()
        return dict(row) if row else None

    def aliases(self, concept_id: str) -> list[str]:
        rows = self.connection.execute(
            "SELECT text FROM alias WHERE concept_id = ? ORDER BY text", (concept_id,)
        )
        return [row["text"] for row in rows]

    def all_aliases(self) -> list[tuple[str, str]]:
        """(alias text, concept id), longest first — the matcher relies on it."""
        rows = self.connection.execute("SELECT text, concept_id FROM alias")
        pairs = [(row["text"], row["concept_id"]) for row in rows]
        pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
        return pairs

    def relations(self, concept_id: str | None = None) -> list[dict]:
        if concept_id is None:
            rows = self.connection.execute("SELECT * FROM relation ORDER BY src, predicate")
        else:
            rows = self.connection.execute(
                "SELECT * FROM relation WHERE src = ? OR dst = ? ORDER BY predicate",
                (concept_id, concept_id),
            )
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        def scalar(sql: str) -> int:
            return int(self.connection.execute(sql).fetchone()[0])

        return {
            "concepts": scalar("SELECT COUNT(*) FROM concept"),
            "aliases": scalar("SELECT COUNT(*) FROM alias"),
            "relations": scalar("SELECT COUNT(*) FROM relation"),
        }

    def export(self, destination: str | Path) -> None:
        payload = {
            "meta": self.meta(),
            "concepts": [
                {**concept, "aliases": self.aliases(concept["id"])}
                for concept in self.concepts()
            ],
            "relations": self.relations(),
        }
        Path(destination).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def load_export(source: str | Path) -> tuple[list[Concept], list[Relation], dict[str, str]]:
    """Read a JSON export back into dataclasses (used by tests and seeds)."""
    payload = json.loads(Path(source).read_text(encoding="utf-8"))
    concepts = [
        Concept(
            id=entry["id"],
            kind=entry["kind"],
            canonical=entry["canonical"],
            source=entry["source"],
            review_status=entry["review_status"],
            note=entry.get("note"),
            aliases=tuple(entry.get("aliases") or ()),
        )
        for entry in payload["concepts"]
    ]
    relations = [
        Relation(
            src=entry["src"],
            predicate=entry["predicate"],
            dst=entry["dst"],
            source=entry["source"],
        )
        for entry in payload["relations"]
    ]
    return concepts, relations, payload.get("meta", {})
