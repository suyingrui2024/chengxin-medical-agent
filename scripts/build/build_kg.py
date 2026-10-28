"""Assemble the knowledge base from the repository seed and imported sources.

Sources, and why each is allowed in:

* **repository content** — already owned and already carrying review status.
* **ICD-10 中文** (``chaseliu/ICD-10-CN``, MIT) — 1,586 coded disease names
  plus the chapter/block hierarchy. The MIT licence is what makes this
  usable at all; the official NHC edition is a 385 MB scan with no text
  layer.
* **Wikidata P780** (CC0) — disease→symptom pairs, the only free source of
  that relation we could reach. Wikidata medical content is
  community-maintained, so every imported row is ``imported_pending_review``.

Deliberately excluded: Wikidata P2176 (drug used for treatment). The CSV
header and the row contents disagree about direction — a coin-flip on
"which side is the drug" is not something to put in a clinical store.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from medagent.extensions.builtin import load_registry
from medagent.kg import Concept, ConceptStore, Relation, seed

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "data" / "kg" / "sources"
OUT_DB = ROOT / "data" / "kg" / "kg.db"
OUT_JSON = ROOT / "data" / "kg" / "kg.json"

ICD_SOURCE = "ICD-10-CN(MIT)@2017 chaseliu/ICD-10-CN"
WD_SOURCE = "Wikidata P780 (CC0) fetched 2026-10-28"
DIAKG_SOURCE = "OpenKG DiaKG (CC BY) fetched 2026-10-28"
DIAKG_DIR = SOURCES / "diakg"
TRIAGE_ALIASES = ROOT / "data" / "kg" / "candidates" / "triage-aliases.tsv"
MODEL_ALIASES = ROOT / "data" / "kg" / "candidates" / "model-aliases.tsv"

#: Entity types we can place in our closed kind set. Anatomy, Class,
#: Treatment, Amount and the rest are dropped: importing them would mean
#: inventing kinds for data nothing consumes.
DIAKG_KINDS = {
    "Disease": "disease",
    "Symptom": "symptom",
    "ADE": "symptom",  # an adverse drug event is a clinical manifestation
    "Drug": "drug",
    "Test_items": "lab",
}

#: relation name -> (head kind, tail kind, predicate, source is head?)
#: In DiaKG a relation named X_Y has head of type X and tail of type Y,
#: verified against 低血糖(ADE) -> 格列本脲(Drug).
DIAKG_RELATIONS = {
    "Symptom_Disease": ("symptom", "disease", "has_symptom", False),
    "ADE_Drug": ("symptom", "drug", "causes_adverse_event", False),
    "Drug_Disease": ("drug", "disease", "treats", True),
    "Test_items_Disease": ("lab", "disease", "has_test", False),
}


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text).strip("_") or "x"


#: ICD-10 Chinese titles are written for classification, not for speech:
#: R07 is 「其他和原因不明的胸痛」, so a patient saying 「胸痛」 matches
#: nothing. Stripping these qualifiers is a mechanical rewrite of an
#: official title — not a synonym, and not a clinical judgement — so it is
#: safe to derive automatically. Longest first, so the specific prefixes
#: are consumed before the generic 「其他」.
ICD_TITLE_QUALIFIERS = (
    "其他和原因不明的",
    "其他和未特指的",
    "未特指的",
    "其他特指的",
    "特指的",
    "其他",
)


def split_alias(canonical: str) -> list[str]:
    """Split a combined ICD title into its parts.

    R07 is 「咽痛和胸痛」: one code, two symptoms, and a patient naming
    either one matches nothing. Splitting on the conjunction is a
    mechanical reading of the official title — both halves are literally
    in it — so the parts are safe to register.
    """
    parts = [part.strip() for part in re.split(r"[和与、]", canonical)]
    if len(parts) < 2:
        return []
    return [part for part in parts if len(part) >= 2]


def short_alias(canonical: str) -> str | None:
    """The head noun of an ICD title, when a qualifier hides it."""
    for qualifier in ICD_TITLE_QUALIFIERS:
        if canonical.startswith(qualifier):
            stripped = canonical[len(qualifier):].strip()
            # A two-character remainder is the useful case (胸痛, 发热);
            # anything shorter is a fragment, not a term.
            if len(stripped) >= 2:
                return stripped
    return None


def icd_concepts() -> tuple[list[Concept], list[Relation]]:
    concepts: list[Concept] = []
    relations: list[Relation] = []
    rows = list(
        csv.DictReader(
            (SOURCES / "disease.csv").open(encoding="utf-8"), delimiter="\t"
        )
    )
    for row in rows:
        code = row["code"].strip()
        name = row["disease"].strip()
        if not code or not name:
            continue
        # Chapter R is 症状、体征和临床与实验室异常所见 — filing those as
        # "disease" would make 头痛 a diagnosis instead of a complaint.
        kind = "symptom" if code.startswith("R") else "disease"
        aliases = [name, code]
        short = short_alias(name)
        if short and short not in aliases:
            aliases.append(short)
        for part in split_alias(name):
            if part not in aliases:
                aliases.append(part)
        concepts.append(
            Concept(
                id=f"icd10:{code}",
                kind=kind,
                canonical=name,
                source=ICD_SOURCE,
                review_status="imported_pending_review",
                aliases=tuple(aliases),
            )
        )

    # Hierarchy: chapter -> block -> code. Chapters and blocks are real
    # ICD categories, so they get concepts of their own (kind
    # "category"); without them every leaf hangs in space and half the
    # graph is a flat list.
    known = {concept.id for concept in concepts}
    tree = json.loads((SOURCES / "diseases.json").read_text(encoding="utf-8-sig"))

    def walk(node, parent: str | None):
        identity = f"icd10:{node['value']}"
        if identity not in known:
            concepts.append(
                Concept(
                    id=identity,
                    kind="category",
                    canonical=node["label"],
                    source=ICD_SOURCE,
                    review_status="imported_pending_review",
                    note="icd_group",
                    aliases=(node["label"],),
                )
            )
            known.add(identity)
        if parent:
            relations.append(
                Relation(src=parent, predicate="contains", dst=identity, source=ICD_SOURCE)
            )
        for child in node.get("children") or []:
            walk(child, identity)

    for top in tree:
        walk(top, None)
    return concepts, relations


def wikidata_concepts(
    index: dict[str, str] | None = None
) -> tuple[list[Concept], list[Relation]]:
    """Symptom→disease from P780, inverted into disease has_symptom symptom.

    ``index`` maps an existing canonical name to its concept id. Without
    it this import mints ``disease:wd:偏头痛`` beside ``icd10:G43`` — two
    nodes for one disease, and every relation hangs off whichever one
    happened to be created first.
    """
    index = index if index is not None else {}
    symptoms: dict[str, Concept] = {}
    diseases: dict[str, Concept] = {}
    relations: list[Relation] = []

    with (SOURCES / "wikidata_p780_symptoms.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            disease = (row.get("dLabel") or "").strip()
            symptom = (row.get("sLabel") or "").strip()
            if not disease or not symptom:
                continue
            disease_id = index.get(disease) or f"disease:wd:{_slug(disease)}"
            symptom_id = index.get(symptom) or f"symptom:wd:{_slug(symptom)}"

            # Only mint a node when the name is new. Re-inserting an id
            # that came from ICD would overwrite its source and review
            # status with Wikidata's, silently re-labelling imported
            # terminology as something it is not.
            if disease_id not in index.values():
                diseases.setdefault(
                    disease_id,
                    Concept(
                        id=disease_id,
                        kind="disease",
                        canonical=disease,
                        source=WD_SOURCE,
                        review_status="imported_pending_review",
                        aliases=(disease,),
                    ),
                )
                index[disease] = disease_id
            if symptom_id not in index.values():
                symptoms.setdefault(
                    symptom_id,
                    Concept(
                        id=symptom_id,
                        kind="symptom",
                        canonical=symptom,
                        source=WD_SOURCE,
                        review_status="imported_pending_review",
                        aliases=(symptom,),
                    ),
                )
                index[symptom] = symptom_id
            relations.append(
                Relation(
                    src=disease_id,
                    predicate="has_symptom",
                    dst=symptom_id,
                    source=WD_SOURCE,
                )
            )
    return list(diseases.values()) + list(symptoms.values()), relations


def triage_aliases() -> dict[str, list[str]]:
    """Colloquial phrases the triage rules already treat as red flags.

    These are not new clinical claims. ``red_flags.json`` already asserts
    that 「叫不醒」 indicates the unresponsive flag and acts on it every
    turn; this only teaches the concept layer the same phrases, so the
    graph and the rule layer stop disagreeing about what a sentence says.

    The TSV is the review surface: mark a row ``rejected`` and it stops
    being loaded. Rows are included by default because excluding them
    would mean the concept layer ignores wording the product already
    trusts.
    """
    grouped: dict[str, list[str]] = {}
    if not TRIAGE_ALIASES.is_file():
        return grouped
    for raw in TRIAGE_ALIASES.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        concept, alias, _, status = parts[0], parts[1], parts[2], parts[3]
        if status.strip() == "rejected":
            continue
        grouped.setdefault(concept, []).append(alias.strip())
    return grouped


def apply_triage_aliases(store: ConceptStore) -> int:
    """Attach the aliases to their red-flag concepts. Returns how many."""
    applied = 0
    for concept_id, aliases in triage_aliases().items():
        concept = store.get(concept_id)
        if not concept:
            continue
        with store.connection:
            for alias in aliases:
                clash = store.connection.execute(
                    "SELECT concept_id FROM alias WHERE text = ?", (alias,)
                ).fetchone()
                if clash and clash["concept_id"] != concept_id:
                    continue
                store.connection.execute(
                    "INSERT OR REPLACE INTO alias (concept_id, text, source)"
                    " VALUES (?,?,?)",
                    (concept_id, alias, "derived:content/red_flags.json"),
                )
                applied += 1
    return applied


def collapse_duplicate_names(store: ConceptStore) -> int:
    """Fold concepts that share a canonical name into one node.

    Two imports can reach the same entity by different routes — 偏头痛
    from ICD and from Wikidata, 巨球蛋白血症 once as a disease and once
    as a symptom. Resolving by name during import handles most of it;
    this is the backstop, because import order should not decide whether
    the graph is connected.

    The survivor is the node with the most edges, so relations stay
    attached to the richer side, and edges are re-pointed rather than
    dropped.
    """
    groups: dict[str, list[dict]] = {}
    for concept in store.concepts():
        groups.setdefault(concept["canonical"], []).append(concept)

    priority = {"disease": 0, "symptom": 1, "drug": 2, "lab": 3, "category": 4}
    merged = 0
    for name, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(
            key=lambda c: (-len(store.relations(c["id"])), priority.get(c["kind"], 9))
        )
        keep = items[0]["id"]
        for other in items[1:]:
            removed = other["id"]
            with store.connection:
                for edge in store.relations(removed):
                    src = keep if edge["src"] == removed else edge["src"]
                    dst = keep if edge["dst"] == removed else edge["dst"]
                    if src == dst:
                        continue
                    store.connection.execute(
                        "INSERT OR IGNORE INTO relation (src, predicate, dst, source)"
                        " VALUES (?,?,?,?)",
                        (src, edge["predicate"], dst, edge["source"]),
                    )
                store.connection.execute(
                    "DELETE FROM relation WHERE src = ? OR dst = ?", (removed, removed)
                )
                store.connection.execute(
                    "DELETE FROM alias WHERE concept_id = ?", (removed,)
                )
                store.connection.execute("DELETE FROM concept WHERE id = ?", (removed,))
            merged += 1
    return merged


def ensure_alias(store: ConceptStore) -> int:
    """Give every concept its own name as an alias.

    Alias de-duplication keeps the most specific concept when two claim
    the same string, which can leave a concept with no alias at all — a
    node that exists but cannot be found. Its own canonical name is
    always a legitimate alias for it.
    """
    added = 0
    for concept in store.concepts():
        if store.aliases(concept["id"]):
            continue
        with store.connection:
            store.connection.execute(
                "INSERT OR REPLACE INTO alias (concept_id, text, source) VALUES (?,?,?)",
                (concept["id"], concept["canonical"], concept["source"]),
            )
        added += 1
    return added


def apply_model_aliases(store: ConceptStore) -> int:
    """Attach aliases a model proposed, keyed by canonical name.

    Adding an alias is monotonic: it can only make the matcher fire more
    often, never less. For emergency detection that is the safe direction
    — a spurious red flag sends someone to be checked, a missing one does
    not. The opposite risk is the one to watch, and it is why these carry
    ``model_proposed`` in their source rather than passing as imported
    knowledge.

    Confidence is kept so a reviewer can start with the low ones.
    """
    if not MODEL_ALIASES.is_file():
        return 0
    by_name = {concept["canonical"]: concept["id"] for concept in store.concepts()}
    applied = 0
    seen: set[tuple[str, str]] = set()
    for raw in MODEL_ALIASES.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split("\t")
        if len(parts) < 4:
            continue
        canonical, alias, confidence, status = parts[0], parts[1], parts[2], parts[3]
        if status.strip() == "rejected":
            continue
        concept_id = by_name.get(canonical)
        if not concept_id:
            continue
        key = (concept_id, alias)
        if key in seen:
            continue
        seen.add(key)
        with store.connection:
            clash = store.connection.execute(
                "SELECT concept_id FROM alias WHERE text = ?", (alias,)
            ).fetchone()
            if clash and clash["concept_id"] != concept_id:
                continue
            store.connection.execute(
                "INSERT OR REPLACE INTO alias (concept_id, text, source)"
                " VALUES (?,?,?)",
                (concept_id, alias, f"model_proposed:{confidence.strip()}"),
            )
            applied += 1
    return applied


def diakg_concepts(
    index: dict[str, str]
) -> tuple[list[Concept], list[Relation]]:
    """Import DiaKG: entities and relations from Chinese diabetes guidelines.

    Entities are resolved against concepts we already hold, by canonical
    name, so 「低血糖」 stays one node instead of splitting into an ICD
    one and a DiaKG one. Only relations whose both ends land in a known
    kind are kept.
    """
    concepts: dict[str, Concept] = {}
    relations: list[Relation] = []

    def resolve(entity: str, entity_type: str) -> str | None:
        kind = DIAKG_KINDS.get(entity_type)
        if not kind or not entity:
            return None
        if entity in index:
            return index[entity]
        identity = f"diakg:{kind}:{_slug(entity)}"
        if identity not in concepts:
            concepts[identity] = Concept(
                id=identity,
                kind=kind,
                canonical=entity,
                source=DIAKG_SOURCE,
                review_status="imported_pending_review",
                aliases=(entity,),
            )
            index[entity] = identity
        return identity

    for path in sorted(DIAKG_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for paragraph in payload.get("paragraphs", []):
            for sentence in paragraph.get("sentences", []):
                by_id = {
                    entry["entity_id"]: entry
                    for entry in sentence.get("entities", [])
                    if "entity_id" in entry
                }
                for relation in sentence.get("relations", []):
                    rule = DIAKG_RELATIONS.get(relation.get("relation_type"))
                    if not rule:
                        continue
                    head_kind, tail_kind, predicate, source_is_head = rule
                    head = by_id.get(relation.get("head_entity_id"))
                    tail = by_id.get(relation.get("tail_entity_id"))
                    if not head or not tail:
                        continue
                    if DIAKG_KINDS.get(head.get("entity_type")) != head_kind:
                        continue
                    if DIAKG_KINDS.get(tail.get("entity_type")) != tail_kind:
                        continue
                    head_id = resolve(head["entity"], head["entity_type"])
                    tail_id = resolve(tail["entity"], tail["entity_type"])
                    if not head_id or not tail_id or head_id == tail_id:
                        continue
                    src, dst = (head_id, tail_id) if source_is_head else (tail_id, head_id)
                    relations.append(
                        Relation(src=src, predicate=predicate, dst=dst, source=DIAKG_SOURCE)
                    )

    return list(concepts.values()), relations


def build(store: ConceptStore | None = None) -> ConceptStore:
    store = store or ConceptStore.in_memory()
    seed.build(load_registry(), store)

    icd, icd_rel = icd_concepts()
    store.add(icd, icd_rel)

    # Every later source resolves against what is already loaded, so one
    # disease stays one node. Without this, 偏头痛 exists as both
    # icd10:G43 and disease:wd:偏头痛 and the graph splits in two.
    index = {concept["canonical"]: concept["id"] for concept in store.concepts()}
    wd, wd_rel = wikidata_concepts(index)
    store.add(wd, wd_rel)

    index.update({concept["canonical"]: concept["id"] for concept in store.concepts()})
    dk, dk_rel = diakg_concepts(index)
    store.add(dk, dk_rel)

    store.set_meta("icd10_rows", str(len(icd)))
    store.set_meta("wikidata_rows", str(len(wd)))
    store.set_meta("diakg_rows", str(len(dk)))
    store.set_meta("triage_aliases", str(apply_triage_aliases(store)))
    store.set_meta("model_aliases", str(apply_model_aliases(store)))
    store.set_meta("merged_duplicates", str(collapse_duplicate_names(store)))
    store.set_meta("orphan_aliases", str(ensure_alias(store)))
    store.set_meta("built", "2026-10-28")
    return store


def main() -> int:
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()
    store = build(ConceptStore.open(OUT_DB))
    store.export(OUT_JSON)

    kinds: dict[str, int] = {}
    for concept in store.concepts():
        kinds[concept["kind"]] = kinds.get(concept["kind"], 0) + 1
    print(f"db   -> {OUT_DB}")
    print(f"json -> {OUT_JSON}")
    print("counts:", store.counts())
    print("kinds:", dict(sorted(kinds.items())))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
