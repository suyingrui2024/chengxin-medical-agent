from __future__ import annotations
import json
import os
import re
from copy import deepcopy
from datetime import datetime
from importlib import resources
from typing import Any, Dict, List, Optional
from ..baseline_projection import build_allergy_hint
from ..direction_guards import (
    direction_kept,
    is_affirmed_in_text,
    severity_consistent_text,
)
from ..domain import Assertion, CaseState, Phase, RiskLevel
from ..followups import build_follow_up_plan
from ..handoff import LABELS as FACT_LABELS
from ..reasoning import RuleBasedProposer, validate_workspace
from ..text_match import contains_unnegated, find_unnegated_certain
from ..worry import extract_worry_object, is_worry_sentence

from .constants import BANNED_PHRASES, DEFAULT_GAP_IMPACT, GAP_IMPACTS, MEASUREMENT_FACTS, _REFUSAL_EVIDENCE_MARKERS

def _is_refusal_evidence(text: str) -> bool:
    return bool(text) and any(marker in text for marker in _REFUSAL_EVIDENCE_MARKERS)


def _gap_label(detail: Dict[str, Any], key: str) -> str:
    """Label fallback chain: the summary detail's label, then the
    handoff label table, then the raw key. A detail label that IS the
    raw key (unlocalized, e.g. "sex") is treated as absent so the
    table's localized label wins — raw keys must never reach the
    patient-visible layer."""
    label = detail.get("label")
    if label and label != key:
        return str(label)
    return FACT_LABELS.get(key, key)


def _gaps(state: CaseState, summary, accepted) -> List[Dict[str, Any]]:
    details = (summary or {}).get("unknown_details") or []
    declined_keys = {
        detail.get("key")
        for detail in (summary or {}).get("declined_details") or []
        if detail.get("key")
    }
    discriminating_keys = set()
    for hypothesis in accepted.active():
        discriminating_keys.update(hypothesis.discriminating_questions)
    # discriminating_questions hold question ids; the summary's unknown
    # details carry fact keys — match on fact key membership via question
    # ids is not possible here, so measurement facts and any unknown that
    # shares a key with a discriminating question id both count.
    scored = []
    for detail in details:
        key = detail.get("key")
        if not key:
            continue
        score = 0
        if key in MEASUREMENT_FACTS:
            score += 2
        if key in discriminating_keys:
            score += 2
        fact = state.facts.get(key)
        declined = key in declined_keys or _is_refusal_evidence(
            fact.evidence if fact is not None else ""
        )
        scored.append(
            (
                score,
                key,
                _gap_label(detail, key),
                "declined" if declined else "asked_unknown",
            )
        )
    scored.sort(key=lambda item: -item[0])
    return [
        {
            "fact_key": key,
            "label": label,
            "impact": GAP_IMPACTS.get(key, DEFAULT_GAP_IMPACT),
            "significant": score >= 2,
            "state": gap_state,
        }
        for score, key, label, gap_state in scored[:3]
    ]


def format_window_text(disposition, window_hours=None) -> str:
    """Single-source window/disposition sentence (batch C, G-02/U-02).

    The conclusion sheet and the spoken close line must never carry
    two competing meanings for the same window: the sheet's
    disposition text is generated through this helper, and the close
    layer (which owns ``spoken_close_line``) should call this helper
    with the sheet's disposition dict instead of hard-coding its own
    window sentence. One disposition instance therefore yields one
    window sentence in both surfaces.

    ``disposition`` may be a level string (``"routine_visit"``) or a
    disposition dict (``level`` / ``window_hours`` / ``text``, plus
    the optional generator keys ``window_kind`` and
    ``rule_out_label``). A dict that already carries ``text`` is
    canonicalised from that text, so a composed sheet round-trips
    to its own sentence:

    * ``window_kind == "after_if_not_improved"`` (or a text containing
      “后还没有好转”) -> “可以在家先观察；把下方缺的信息补上，
      如果 {window} 小时后还没有好转，请到线下医疗机构就诊。”
      This is the significant-gap upgrade variant; it is NOT
      rewritten into a “within” sentence, and no new threshold is
      introduced — the sheet's own variant is preserved verbatim.
    * any other ``routine_visit`` -> “请在 {window} 小时内…”
      (with “排除 {label}” when a rule-out label is present).
    * ``urgent_today`` -> today sentence; ``self_care`` -> the
      sheet's observe sentence. Unknown levels fall back to the
      dict's own text, or "" when there is none.
    """
    if isinstance(disposition, dict):
        level = str(disposition.get("level") or "")
        window = window_hours if window_hours is not None else disposition.get("window_hours")
        text = str(disposition.get("text") or "")
        rule_out_label = disposition.get("rule_out_label")
        window_kind = disposition.get("window_kind")
    elif isinstance(disposition, str):
        level = disposition
        window = window_hours
        text = ""
        rule_out_label = None
        window_kind = None
    else:
        return ""
    if level == "urgent_today":
        return "建议今天内到线下医疗机构当面评估，不要在家观察等待。"
    if level == "self_care":
        return "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。"
    if level == "routine_visit":
        if window_kind == "after_if_not_improved" or "后还没有好转" in text:
            if window is None:
                return "可以在家先观察；把下方缺的信息补上，如果还没有好转，请到线下医疗机构就诊。"
            return (
                "可以在家先观察；把下方缺的信息补上，"
                f"如果 {window} 小时后还没有好转，请到线下医疗机构就诊。"
            )
        if rule_out_label is None and "排除" in text:
            match = re.search(r"排除(.+?)。", text)
            if match and match.group(1).strip():
                rule_out_label = match.group(1).strip()
        if window is None:
            return "可以在家先观察；请尽快到线下医疗机构就诊评估。"
        if rule_out_label:
            return (
                f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊，"
                f"排除 {rule_out_label}。"
            )
        return f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
    if text:
        return text if text.endswith(("。", "！", "？")) else text + "。"
    return ""


def _disposition(state, tier, directions, due_hours, show_directions):
    if tier == "urgent":
        return {
            "level": "urgent_today",
            "window_hours": due_hours or 24,
            "text": format_window_text("urgent_today"),
            "department_hint": None,
        }
    rule_outs = [d for d in directions if d["group"] == "rule_out"]
    if rule_outs:
        window = due_hours or 48
        top = rule_outs[0]
        label = top.get("patient_label") if show_directions else None
        text = format_window_text(
            {
                "level": "routine_visit",
                "window_hours": window,
                "rule_out_label": label,
            },
            window,
        )
        return {
            "level": "routine_visit",
            "window_hours": window,
            "text": text,
            "department_hint": None,
        }
    return {
        "level": "self_care",
        "window_hours": due_hours,
        "text": format_window_text("self_care"),
        "department_hint": None,
    }


def spoken_close_line(conclusion: Optional[Dict[str, Any]]) -> str:
    """One or two plain-language closing sentences derived from a sheet.

    The sheet itself renders as a card; this line is what the closing
    chat message can *say* out loud: the disposition first, then the
    single most important return_now_if alert. Returns "" when there is
    no usable sheet. Fail-closed: a banned phrase anywhere in the
    assembled line voids it, same as the sheet lint.
    """
    if not conclusion:
        return ""
    disposition = conclusion.get("disposition") or {}
    level = disposition.get("level")
    if not level:
        return ""
    window = disposition.get("window_hours")
    if level == "urgent_today":
        main = "建议今天内到线下医疗机构当面评估，不要在家观察等待。"
    elif level == "routine_visit":
        # Batch C rework (G-02/U-02): single-source the spoken window
        # sentence from the sheet's own disposition dict via
        # format_window_text, so the upgrade variant ("48 小时后还没
        # 有好转") and a visible rule-out label ("排除 …") round-trip
        # verbatim instead of being rewritten into a "within" sentence
        # or dropped. window_kind is not stored on the composed dict,
        # but the helper recovers both from the sheet text; a hidden
        # label stays hidden because the sheet text carries no label.
        # Fallback to the previous hard-coded sentences only if the
        # helper returns "" (it does not for routine_visit today).
        main = format_window_text(disposition) or (
            f"可以在家先观察；请在 {window} 小时内到线下医疗机构就诊评估。"
            if window
            else "可以在家先观察；请尽快到线下医疗机构就诊评估。"
        )
    elif level == "self_care":
        # Batch C rework (P-10): the framing opener already says the
        # case "可以在家先观察处理", so the main sentence must not
        # repeat the home-observation concept ("可以先在家观察，…").
        # Keep only the no-visit clause; framing is untouched.
        main = "暂时不需要专门跑一趟医院。"
    else:
        text = str(disposition.get("text") or "").strip()
        if not text:
            return ""
        main = text if text.endswith(("。", "！", "？")) else text + "。"
    alerts = [
        str(item).strip()
        for item in (conclusion.get("return_now_if") or [])
        if str(item).strip()
    ]
    # The spoken line opens with the narrative's framing sentence for
    # this disposition level (benchmark design §3.1), then the existing
    # disposition + first alert + 120 elements, unchanged. The framing
    # comes from the curated asset, not the sheet, so sheets composed
    # before the narrative existed still speak with it; a missing asset
    # simply drops the opener (fail-open).
    framing = (
        (_narrative_templates().get("spoken_framing") or {}).get(level) or ""
    ).strip()
    line = framing + main if framing else main
    if alerts:
        line += f"如果出现{alerts[0]}，请及时就医；情况紧急时请拨打 120。"
    if any(phrase in line for phrase in BANNED_PHRASES):
        return ""
    return line


def emergency_explanation(triggers) -> str:
    """Patient-facing explanation line for an emergency close.

    ``triggers`` are the deterministic scanner's trigger names (plain
    patient-facing strings, as stored on RiskAssessment.triggers); dicts
    carrying a patient-facing label are also accepted defensively. The
    line names the danger category, tells the patient to call 120 now,
    and gives waiting guidance. Fail-closed on the banned-phrase lint:
    if a trigger name somehow trips it, the names are dropped rather
    than the whole explanation.
    """
    names: List[str] = []
    for trigger in triggers or []:
        text = ""
        if isinstance(trigger, str):
            text = trigger.strip()
        elif isinstance(trigger, dict):
            for key in ("patient_text", "label", "description", "name", "id"):
                value = trigger.get(key)
                if isinstance(value, str) and value.strip():
                    text = value.strip()
                    break
        if text and text not in names:
            names.append(text)
    guidance = "请现在拨打 120，等待时保持安静休息，不要自行开车，有家人陪同更好。"
    if names:
        line = (
            "你描述的情况包含需要立即处理的危险信号（"
            + "、".join(names[:3])
            + "）。"
            + guidance
        )
        if not any(phrase in line for phrase in BANNED_PHRASES):
            return line
    return "你描述的情况包含需要立即处理的危险信号。" + guidance

from .narrative import _narrative_templates
