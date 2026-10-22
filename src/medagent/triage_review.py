"""Reasoning-review layer for triage (design doc §4.2, layer 3).

Adapts any structured-decision callable (e.g. a model backend) to the
``SemanticProvider`` protocol consumed by ``RedFlagScanner``. The callable
receives the raw text plus the red-flag definition catalogue and must
return ``{"hits": [{"flag_id": ..., "evidence": ...}]}``. Hits are only
ever *added* to the deterministic layers, and the scanner independently
validates that flag ids exist and evidence is a verbatim substring, so a
malformed or hallucinating reviewer cannot lower a risk level or invent
a trigger out of thin air.

This layer is opt-in: it activates only when a provider is injected into
``TriageService``. The deterministic engine ships without one.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .red_flags import FlagHit, load_definitions


class ReasoningReviewProvider:
    def __init__(
        self,
        decide: Callable[[str, List[Dict[str, Any]]], Dict[str, Any]],
        catalogue: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self._decide = decide
        if catalogue is None:
            catalogue = [
                {"id": d.id, "label": d.label, "phrases": list(d.phrases)}
                for d in load_definitions()
            ]
        self._catalogue = catalogue

    def scan(self, text: str) -> List[FlagHit]:
        try:
            result = self._decide(text, self._catalogue)
        except Exception:
            return []  # reviewer unavailable: deterministic layers stand
        hits: List[FlagHit] = []
        for item in (result or {}).get("hits", []):
            if not isinstance(item, dict):
                continue
            flag_id = item.get("flag_id")
            evidence = item.get("evidence")
            if not flag_id or not isinstance(evidence, str) or not evidence:
                continue
            hits.append(FlagHit(flag_id, "", "provider", evidence))
        return hits
