"""Map patient wording onto canonical concepts.

Greedy longest-match, left to right, non-overlapping. That is enough for
Chinese medical terms, where the failure mode is a short alias swallowing
a longer one ("痛" inside "胸痛"), and it needs no tokeniser or model.

The matcher returns spans rather than a bare set of ids so callers can
show *which* words were recognised — the consultation UI needs to explain
itself, and a silent concept swap is exactly the kind of thing this
product must not do.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import ConceptStore


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    text: str
    concept_id: str
    kind: str
    canonical: str


class Normalizer:
    def __init__(self, store: ConceptStore):
        self.store = store
        self._aliases = store.all_aliases()  # longest first
        self._kinds = {row["id"]: (row["kind"], row["canonical"]) for row in store.concepts()}

    def _fold(self, text: str) -> str:
        # Only ASCII is case-folded: Chinese has no case, and folding it
        # would mangle full-width forms people actually type.
        return "".join(ch.lower() if ch.isascii() else ch for ch in text)

    def find(self, text: str) -> list[Match]:
        if not text:
            return []
        haystack = self._fold(text)
        taken = [False] * len(haystack)
        matches: list[Match] = []

        for alias, concept_id in self._aliases:
            needle = self._fold(alias)
            if not needle:
                continue
            start = haystack.find(needle)
            while start != -1:
                end = start + len(needle)
                if not any(taken[start:end]):
                    for index in range(start, end):
                        taken[index] = True
                    kind, canonical = self._kinds[concept_id]
                    matches.append(
                        Match(
                            start=start,
                            end=end,
                            text=text[start:end],
                            concept_id=concept_id,
                            kind=kind,
                            canonical=canonical,
                        )
                    )
                start = haystack.find(needle, start + 1)

        matches.sort(key=lambda match: match.start)
        return matches

    def concepts(self, text: str) -> list[str]:
        """Distinct concept ids, in order of first appearance."""
        seen: list[str] = []
        for match in self.find(text):
            if match.concept_id not in seen:
                seen.append(match.concept_id)
        return seen

    def has(self, text: str, kind: str | None = None) -> bool:
        return any(kind is None or match.kind == kind for match in self.find(text))
