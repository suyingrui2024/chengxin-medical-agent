"""Knowledge base: governed concepts, aliases and relations.

The consultation engine needs one thing from a "knowledge graph" that it
cannot get from prose: a stable way to decide that 「心口疼」, 「胸口痛」
and 「胸痛」 mean the same thing, and that this concept is not the same as
「腹痛」. That is terminology normalisation, and it is the whole of what
this package does today.

Deliberately absent: a graph database, embeddings, and any inferred
relation. Relations exist in the schema because a reviewed source may
supply them later; nothing is inferred, because an inferred edge in a
clinical product is an unsourced claim wearing a data structure.
"""

from .normalize import Match, Normalizer
from .store import Concept, ConceptStore, Relation
from . import seed

__all__ = ["Concept", "ConceptStore", "Match", "Normalizer", "Relation", "seed"]
