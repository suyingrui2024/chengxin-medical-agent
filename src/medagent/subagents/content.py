from __future__ import annotations
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib import resources
from threading import BoundedSemaphore, Event as ThreadEvent, Lock, Thread
from time import monotonic
from typing import Any, Dict, List, Optional
from uuid import uuid4
from ..backends import parse_decision
from ..conclusion import BANNED_PHRASES, GAP_IMPACTS, MEASUREMENT_FACTS
from ..decision_wait import DecisionInterrupted
from ..domain import CaseState, utc_now
from ..events import Event
from ..reasoning import ReasoningWorkspace, load_pathways

from .constants import _content_index_cache

def watermark(state: CaseState) -> Dict[str, Any]:
    """Staleness watermark for a case (design §5.6).

    Exported for the wiring layer: capture before a spawn, compare at
    delivery; any change means the case moved on and results are stale.
    """
    fact_fingerprint = "|".join(
        sorted(f"{key}:{fact.fact_id}" for key, fact in state.facts.items())
    )
    conclusion = state.conclusion if isinstance(state.conclusion, dict) else {}
    return {
        "phase": state.phase.value,
        "revision": state.revision,
        "updated_at": state.updated_at,
        "fact_ids_hash": hashlib.sha256(fact_fingerprint.encode()).hexdigest(),
        "conclusion_revision": conclusion.get("revision"),
    }


def _load_content_json(name: str) -> Dict[str, Any]:
    raw = (
        resources.files("medagent.content").joinpath(name).read_text(encoding="utf-8")
    )
    return json.loads(raw)


def _content_index() -> Dict[str, Dict[str, str]]:
    """locator -> {title, text} over differentials + self_care content.

    Locators are hypothesis ids, pathway ids and self_care group ids;
    the text is the entry's canonical JSON. Knowledge quotes are NOT
    verified against this full text at validation time — only against
    the excerpts actually fed to the branch (see _validate_report).
    """
    global _content_index_cache
    if _content_index_cache is not None:
        return _content_index_cache
    index: Dict[str, Dict[str, str]] = {}
    for pathway in load_pathways():
        index[pathway["id"]] = {
            "title": pathway["id"],
            "text": json.dumps(pathway, ensure_ascii=False),
        }
        for hypothesis in pathway.get("hypotheses", []):
            index[hypothesis["id"]] = {
                "title": hypothesis.get("label", hypothesis["id"]),
                "text": json.dumps(hypothesis, ensure_ascii=False),
            }
    for group in _load_content_json("self_care.json").get("groups", []):
        index[group["id"]] = {
            "title": group["id"],
            "text": json.dumps(group, ensure_ascii=False),
        }
    _content_index_cache = index
    return index


def _known_fact_keys(state: CaseState) -> set:
    """v1 allowed fact-key universe for scope clamping.

    Union of: the case's own fact keys, every fact key referenced by the
    differentials content (support/refute conditions and discriminating
    questions), and the conclusion layer's gap/measurement key sets.
    Scope is additionally intersected with the case's recorded facts, so
    an allowed-but-absent key is still clamped out of the view.
    """
    keys = set(state.facts)
    for pathway in load_pathways():
        for hypothesis in pathway.get("hypotheses", []):
            for condition in hypothesis.get("support", []) + hypothesis.get(
                "refute", []
            ):
                if condition.get("fact_key"):
                    keys.add(condition["fact_key"])
            keys.update(hypothesis.get("discriminating_questions", []))
    keys |= set(GAP_IMPACTS) | set(MEASUREMENT_FACTS)
    return keys


def _knowledge_search(query: str, max_results: int = 3) -> List[Dict[str, Any]]:
    """Deterministic keyword-substring retrieval over the content pool."""
    terms = [term for term in re.split(r"[\s,，、;；]+", query or "") if len(term) >= 2]
    if not terms:
        return []
    scored = []
    for ref, entry in _content_index().items():
        hits = sum(1 for term in terms if term in entry["text"])
        if hits:
            scored.append((hits, ref, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    cap = max(1, min(int(max_results or 3), 5))
    return [
        {"ref": ref, "title": entry["title"], "excerpt": entry["text"][:1200]}
        for _, ref, entry in scored[:cap]
    ]


def _banned_hit(text: str) -> bool:
    return any(phrase in text for phrase in BANNED_PHRASES)
