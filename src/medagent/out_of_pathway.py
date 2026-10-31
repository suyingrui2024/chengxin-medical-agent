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


# Domains whose ground is already covered by the combined
# ``emergency_screen`` question asked before deep-dive (design §5.3:
# 急诊筛查顺序不变). When that screen was explicitly answered 「都没有」,
# re-asking the same three domains would be a visible regression, so
# the question layer skips them. This is question economy, not a
# clinical threshold: an unanswered or 「不确定」 screen asks all six.
# Review note: clinical sign-off (C1) may widen or narrow this list;
# it is a Python constant, not content, precisely so it stays a
# reviewable engineering decision rather than content drift.
SUBSUMED_BY_EMERGENCY_SCREEN = ("意识", "呼吸", "出血")


def warning_domain_specs(
    emergency_screen_absent: bool = False,
) -> List[Dict[str, str]]:
    """Askable specs for the P3 warning domains (question-layer seam).

    ``warning_questions`` keeps its frozen ``{domain, question}``
    contract (tests/test_out_of_pathway.py pins the content schema, and
    ``bridging.py`` derives ``warning_<domain>`` ids from it). This
    function is the seam that adds the id/fact_key the question layer
    needs. The id convention is the same ``warning_<domain>`` that
    bridging already derives, so both agree without touching the
    content schema.

    ``emergency_screen_absent`` drops the domains listed in
    :data:`SUBSUMED_BY_EMERGENCY_SCREEN`; see that constant for why.
    """
    specs: List[Dict[str, str]] = []
    for item in _load_warning_signs().get("domains") or []:
        domain = str(item["domain"])
        if emergency_screen_absent and domain in SUBSUMED_BY_EMERGENCY_SCREEN:
            continue
        specs.append(
            {
                "id": f"warning_{domain}",
                "fact_key": f"warning_{domain}",
                "domain": domain,
                "question": str(item["question"]),
            }
        )
    return specs
