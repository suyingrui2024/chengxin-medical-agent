"""Importers for terminology that the repository does not own.

Nothing in this module fetches anything. A loader takes a file someone
else produced and turns it into concepts, so that the acquisition step —
which involves licences, OCR quality and human review — stays explicit
and outside the build.

Why the official NHC PDF is not a loader target: the 2023 edition
(``常用临床医学名词（2023年版）``, 385 MB) is a pure scan — the file
contains 3,878 image objects, zero font objects and no ToUnicode map. Its
terms exist only as pixels, so importing them means OCR, and an OCR
substitution in a medical term ("二尖瓣" read as "二间瓣") is a defect
this product cannot ship. Use an authorised text export instead.

Accepted inputs:

1. A TSV of ``canonical<TAB>alias`` rows, one alias per row, canonical
   repeated. This is the shape a curated list or an authorised export
   naturally takes.
2. A plain list of canonical terms, one per line, with no aliases.
"""

from __future__ import annotations

from pathlib import Path

from .store import Concept

DEFAULT_REVIEW = "imported_pending_review"


def _slug(text: str) -> str:
    """A stable, readable id fragment for terms that have no external code."""
    return text.strip().replace(" ", "_")


def load_tsv(
    path: str | Path,
    *,
    kind: str,
    source: str,
    review_status: str = DEFAULT_REVIEW,
    id_prefix: str = "term",
    limit: int | None = None,
) -> list[Concept]:
    """Read ``canonical<TAB>alias`` rows into concepts.

    A canonical that appears on several rows collects those aliases into
    one concept; a row with an empty alias contributes the canonical name
    itself as its own alias, so a term is always findable.
    """
    buckets: dict[str, list[str]] = {}
    order: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        canonical = parts[0].strip()
        if not canonical:
            continue
        alias = parts[1].strip() if len(parts) > 1 else ""
        if canonical not in buckets:
            buckets[canonical] = []
            order.append(canonical)
            if limit is not None and len(order) > limit:
                break
        if alias and alias != canonical:
            buckets[canonical].append(alias)

    return [
        Concept(
            id=f"{id_prefix}:{_slug(canonical)}",
            kind=kind,
            canonical=canonical,
            source=source,
            review_status=review_status,
            aliases=tuple(dict.fromkeys([canonical, *buckets[canonical]])),
        )
        for canonical in order
    ]


def load_terms(
    path: str | Path,
    *,
    kind: str,
    source: str,
    review_status: str = DEFAULT_REVIEW,
    id_prefix: str = "term",
) -> list[Concept]:
    """One canonical term per line, no aliases."""
    terms = [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [
        Concept(
            id=f"{id_prefix}:{_slug(term)}",
            kind=kind,
            canonical=term,
            source=source,
            review_status=review_status,
            aliases=(term,),
        )
        for term in dict.fromkeys(terms)
    ]
