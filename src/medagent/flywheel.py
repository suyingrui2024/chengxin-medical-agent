"""Review flywheel (design doc §6): clinician review outcomes become
evaluation corpus candidates, so every corrected case makes the release
gates stricter instead of evaporating into a chat log.

A candidate is inert data until it is merged into a corpus file and the
corpus is re-run through the release evaluation. Merging refuses
unreviewed candidates and deduplicates by normalized text, because a
corpus that silently accepts unreviewed rows would corrupt the very
metrics the flywheel exists to improve.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    return _WHITESPACE.sub("", text.strip().lower())


def build_candidate(
    *,
    sample_id: str,
    text: str,
    expect_trigger: bool,
    red_flag_id: str = "",
    category: str = "clinician_review",
    reviewer: str,
    note: str = "",
) -> Dict[str, Any]:
    """One corpus candidate from a clinician's review of a real case."""
    if not reviewer.strip():
        raise ValueError("复核来源样本必须注明复核人")
    if expect_trigger and not red_flag_id:
        raise ValueError("阳性样本必须标注红旗 id")
    return {
        "id": sample_id,
        "red_flag_id": red_flag_id,
        "category": category,
        "text": text,
        "expect_trigger": bool(expect_trigger),
        "review_status": "clinician_reviewed",
        "reviewer": reviewer,
        "note": note,
    }


def merge_candidates(
    corpus: Dict[str, Any], candidates: List[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Return (new corpus, report) without mutating the input corpus."""
    existing_texts = {
        normalize_text(sample["text"]) for sample in corpus.get("samples", [])
    }
    existing_ids = {sample["id"] for sample in corpus.get("samples", [])}
    merged = {**corpus, "samples": list(corpus.get("samples", []))}
    report: Dict[str, List[str]] = {"added": [], "duplicates": [], "rejected": []}
    for candidate in candidates:
        if candidate.get("review_status") != "clinician_reviewed":
            report["rejected"].append(candidate.get("id", "?"))
            continue
        if (
            normalize_text(candidate["text"]) in existing_texts
            or candidate["id"] in existing_ids
        ):
            report["duplicates"].append(candidate["id"])
            continue
        sample = {
            key: candidate[key]
            for key in ("id", "red_flag_id", "category", "text", "expect_trigger")
        }
        merged["samples"].append(sample)
        existing_texts.add(normalize_text(candidate["text"]))
        existing_ids.add(candidate["id"])
        report["added"].append(candidate["id"])
    return merged, report
