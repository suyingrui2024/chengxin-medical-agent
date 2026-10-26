"""Off-pathway first-class flow: content + module package (P3).

Implements docs/假设驱动问诊设计文档.md §4 for the off-pathway case:
when a chief complaint triggers no differential pathway, the consultation
asks a fixed set of symptom-domain warning questions (danger-signal
domains only, no organ-specific wording) instead of borrowing the
nearest pathway's templates, and the close states plainly what this
consultation can and cannot deliver.

Scope of this module (P3 contract, frozen for the P0 sorter):
- ``warning_questions(chief)`` returns the full domain list if and only
  if ``chief_matches_pathway(chief)`` (conclusion.py, read-only reuse)
  is False; otherwise it returns an empty list.
- ``SCOPE_NOTE`` is the verbatim scope-disclosure text. Wiring it into
  the close message is P2's job in ``_record_reply``; this module does
  not touch ``consultation.py``.
- No new pathway, no new triage signal, no diagnosis wording, no
  department name: the warning questions live in the standalone
  ``warning_signs.json`` file and are never mixed into ``questions.json``.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict, List, Optional

from .conclusion import chief_matches_pathway

# Verbatim scope disclosure (P3 contract). Wired into the off-pathway
# close by P2; this module only owns the constant text.
SCOPE_NOTE = (
    "这次问诊没有覆盖到你这个问题的专属分诊路径，"
    "结论只给到观察与就诊指征，具体判断仍需线下评估。"
)

_cache: Optional[Dict[str, Any]] = None


def _load_warning_signs() -> Dict[str, Any]:
    """Load ``warning_signs.json`` once (same resources pattern as conclusion)."""
    global _cache
    if _cache is None:
        raw = (
            resources.files("medagent.content")
            .joinpath("warning_signs.json")
            .read_text(encoding="utf-8")
        )
        _cache = json.loads(raw)
    return _cache


def warning_questions(chief: str) -> List[Dict[str, str]]:
    """Symptom-domain warning questions for an off-pathway chief.

    Returns a fresh list of ``{"domain": ..., "question": ...}`` dicts
    covering every domain in ``warning_signs.json`` (in file order)
    when ``chief_matches_pathway(chief)`` is False, and ``[]`` otherwise.
    Callers may mutate the returned dicts; the cached content is never
    exposed directly.
    """
    if chief_matches_pathway(chief):
        return []
    domains = _load_warning_signs().get("domains") or []
    return [
        {"domain": str(item["domain"]), "question": str(item["question"])}
        for item in domains
    ]
