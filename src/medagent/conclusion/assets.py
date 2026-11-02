"""Content-asset loading for the conclusion sheet.

These four helpers used to live in ``sheet`` while ``narrative`` imported
``_load_json_optional`` back from it and ``sheet`` imported
``_narrative_templates`` back from ``narrative``.  Both directions were real, so
neither module could be read or tested alone; the file-bottom imports only hid
that from the interpreter.

Loading a JSON asset needs nothing but the cache and ``importlib.resources``, so
it belongs below both of them.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict


from .constants import _cache


def _load_json(name: str) -> Dict[str, Any]:
    if name not in _cache:
        raw = (
            resources.files("medagent.content")
            .joinpath(name)
            .read_text(encoding="utf-8")
        )
        _cache[name] = json.loads(raw)
    return _cache[name]


def _load_json_optional(name: str) -> Dict[str, Any]:
    """_load_json for assets that may legitimately be absent (narrative
    templates, department map): a missing or corrupt file degrades the
    feature to off instead of failing composition (fail-open, matching
    the composer's I3 style)."""
    try:
        data = _load_json(name)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _patient_labels() -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for pathway in _load_json("differentials.json").get("pathways", []):
        for hypothesis in pathway.get("hypotheses", []):
            if hypothesis.get("patient_label"):
                labels[hypothesis["id"]] = hypothesis["patient_label"]
    return labels


def _narrative_templates() -> Dict[str, Any]:
    """Curated narrative templates (content/conclusion_narrative.json).
    Like the self-care groups, the asset counts as absent unless it
    carries review metadata; absent/corrupt -> {} and the sheet simply
    carries narrative=None (fail-open)."""
    data = _load_json_optional("conclusion_narrative.json")
    return data if data.get("reviewed_by") else {}
