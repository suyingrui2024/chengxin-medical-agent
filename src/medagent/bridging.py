"""Host-side deterministic bridging sentences for question turns (empathy carrier A).

A bridging sentence is a curated template plus slots filled only from facts
already recorded in ``CaseState.facts``. It never introduces model free text,
a diagnosis, a disposition, or a differential direction, and it is never
written back into ``facts`` -- it only prefixes ``AgentReply.message`` at the
``_record_reply`` choke point shared by both engines.

Priority (first match wins):
1. ``repeat`` -- the same question text was already asked this session
   (matched against earlier assistant transcript lines, which may themselves
   carry a bridging prefix, so the match is ``endswith``) AND its fact was
   actually answered before (recorded in ``facts``). The answered condition
   matters: a question merely restored after an interruption (calculation,
   task, clarification) was never answered, so calling it "asked before"
   would be false -- those rounds fall through to the generic anchor.
2. ``denial`` -- the question's own fact was previously recorded as ABSENT
   ("no X") or UNKNOWN ("not noticed"). UNKNOWN is never rendered as a
   denial: "没注意过" must not become "你没有过敏".
3. ``entry`` -- the first question about a pathway detail, anchored on the
   recorded chief complaint.
4. ``anchor`` -- any other question round, anchored on one recorded PRESENT
   fact, rotated deterministically so consecutive rounds do not repeat the
   same anchor wording.

Lint: the bridging sentence itself (never the original question, which the
host must not rewrite) is checked against ``BANNED_PHRASES`` plus the
clarify branch's prohibited list. On a hit the whole bridging sentence is
dropped and only the original question is shown -- never silently reworded.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from .conclusion import BANNED_PHRASES
from .domain import Assertion, Fact

# Second lint layer: the clarify branch's prohibited list (agent.py).
CLARIFY_PROHIBITED = ("毫克", "mg", "服用", "确诊", "可以排除", "用力按", "屏住呼吸")

BRIDGING_BANNED = tuple(dict.fromkeys(BANNED_PHRASES + CLARIFY_PROHIBITED))

# Pathway detail fact keys: the first question about one of these, while the
# chief complaint is already recorded, gets the entry template.
DETAIL_FACT_KEYS = frozenset(
    {
        "resp_cough_details",
        "resp_breathing",
        "resp_intake",
        "gi_details",
        "head_details",
        "dizzy_details",
        "back_details",
        "urinary_details",
        "skin_details",
    }
)

# Deterministic iteration order for the widened anchor pool (a frozenset
# alone would rotate differently across processes).
DETAIL_FACT_KEYS_ORDERED = (
    "resp_cough_details",
    "resp_breathing",
    "resp_intake",
    "gi_details",
    "head_details",
    "dizzy_details",
    "back_details",
    "urinary_details",
    "skin_details",
)

# Model-written questions that already open with a bridging phrase are left
# alone rather than stacked with a second one. "你说" is the model's most
# common opener (run7: 16 stacked sentences) and must be included; the
# "你刚才/前面/之前说" variants are the same opener with a time adverb.
SELF_BRIDGED_OPENERS = (
    "你刚才说",
    "你刚说",
    "你前面说",
    "你之前说",
    "你提到过",
    "你提到",
    "你说",
    "因为",
    "为了",
)

# "Why ask" markers for the B-route model-side discipline: an ask_patient /
# clarify question must open with a bridging opener and carry one of these
# before the question itself.
WHY_MARKERS = (
    "为了确认",
    "为了区分",
    "为了判断",
    "想先确认",
    "想确认",
    "需要确认",
    "以便",
    "因为",
    "为了",
)

# Repair guidance fed back through the self-correction loop when a model
# question lacks its own bridging sentence (B route).
MODEL_BRIDGING_REPAIR_HINT = (
    "问题文本须自带承接句与‘为什么问’半句：先以‘你说/你提到’等呼应已记录事实开头，"
    "再用‘为了…/想确认…’说明为什么问，然后只问这一件事；无可锚事实的开场轮次可直接提问。"
)

# Fact keys that must never serve as a bridging anchor on their own.
_NON_ANCHOR_KEYS = frozenset({"sex", "age_years", "consult_for"})

_MAX_SLOT_CHARS = 30

# Delimiters at which a slot may be shortened without cutting a phrase.
_PHRASE_DELIMITERS = ("、", "，", ",", "；", ";", " ")


def _truncate_phrase(text: str) -> str:
    """Truncate to _MAX_SLOT_CHARS without cutting a phrase in half.

    Prefer cutting at the last phrase delimiter inside the limit, even if
    that shortens the slot further; only fall back to a hard cut when the
    first phrase itself exceeds the limit (run7: "头有点晕沉" was cut at
    "头"). A one-character prefix is never a useful slot, so a delimiter
    at position 0-1 falls through to the hard cut.
    """
    if len(text) <= _MAX_SLOT_CHARS:
        return text
    cut = text[:_MAX_SLOT_CHARS]
    idx = max(cut.rfind(d) for d in _PHRASE_DELIMITERS)
    if idx >= 2:
        return cut[:idx]
    return cut


def _render_value(value: Any) -> Optional[str]:
    """Render a fact value as short patient-facing text, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.strip()
        return _truncate_phrase(text) if text else None
    if isinstance(value, list):
        parts = [str(item).strip() for item in value[:2] if str(item).strip()]
        if not parts:
            return None
        return _truncate_phrase("、".join(parts))
    if isinstance(value, dict):
        for key in ("text", "label", "value", "name"):
            if value.get(key):
                return _render_value(value[key])
        return None
    return None


# Leading verb phrases stripped from a slot when the template already
# supplies that verb (run7: "你说用过自己吃过几天益生菌"). Only multi-
# character past-use phrases are stripped: a bare "吃" could be a symptom
# ("吃不下"), not a medication history, and must survive.
_MEDICATION_LEADING_VERBS = (
    "自己吃过",
    "自己用过",
    "自己吃",
    "自己用",
    "服用过",
    "服用",
    "吃过",
    "用过",
    "喝过",
)


def _strip_leading_verbs(text: str, verbs: Tuple[str, ...]) -> str:
    for verb in verbs:
        if text.startswith(verb):
            return text[len(verb) :]
    return text


def _strip_trailing_start(text: str) -> str:
    """Drop a trailing "开始" so the onset template does not double it
    (run7: "从一天半前开始开始到现在")."""
    if text.endswith("开始"):
        return text[:-2]
    return text


def has_anchorable_fact(facts: Mapping[str, Fact]) -> bool:
    """True when some recorded PRESENT fact may serve as a bridge anchor."""
    for key, fact in facts.items():
        if key not in _NON_ANCHOR_KEYS and getattr(fact, "assertion", None) == Assertion.PRESENT:
            return True
    return False


def model_question_bridging_status(text: str) -> Tuple[bool, bool]:
    """Check a model-written question for the B-route discipline.

    Returns ``(has_bridge, has_why)``: the text opens with a bridging
    opener and carries a "why ask" marker. The host never rewrites the
    question; callers use this to accept or send the model back for repair.
    """
    stripped = text.lstrip()
    return (
        stripped.startswith(SELF_BRIDGED_OPENERS),
        any(marker in stripped for marker in WHY_MARKERS),
    )


def _present_text(facts: Mapping[str, Fact], key: str) -> Optional[str]:
    fact = facts.get(key)
    if fact is None or fact.assertion != Assertion.PRESENT:
        return None
    return _render_value(fact.value)


def _severity_text(facts: Mapping[str, Fact]) -> Optional[str]:
    fact = facts.get("severity")
    if fact is None or fact.assertion != Assertion.PRESENT:
        return None
    raw = _render_value(fact.value)
    if raw is None:
        return None
    if raw.isdigit():
        return f"{raw}分"
    return raw


def lint_bridging_sentence(sentence: str) -> bool:
    """Return True when the sentence passes both lint layers."""
    return not any(banned in sentence for banned in BRIDGING_BANNED)


def build_bridging_sentence(
    facts: Mapping[str, Fact],
    fact_key: Optional[str],
    original_message: str,
    prior_assistant_messages: List[str],
    question_turn_index: int,
) -> Tuple[Optional[str], str]:
    """Build the bridging sentence for one question turn.

    Returns ``(sentence, kind)`` where ``kind`` is one of ``repeat``,
    ``denial``, ``entry``, ``anchor``, ``self_bridged``, ``lint_dropped`` or
    ``no_anchor``. ``sentence`` is None unless kind is one of the first four.
    """
    if original_message.startswith(SELF_BRIDGED_OPENERS):
        return None, "self_bridged"

    # 1. Repeat: this exact question text was already asked this session
    # and its fact was answered (see the module docstring: a restored
    # unanswered question is not a repeat).
    if (
        original_message
        and fact_key
        and fact_key in facts
        and any(
            msg and msg.endswith(original_message) for msg in prior_assistant_messages
        )
    ):
        sentence = "这个问题之前问过一次，刚才那处还没完全确认，我再确认一下："
        return (sentence, "repeat") if lint_bridging_sentence(sentence) else (None, "lint_dropped")

    # 2. Denial: re-asking the question's own fact after it was denied or
    # left unknown. ABSENT renders as a real denial; UNKNOWN renders only
    # as "还没注意过", never as a denial.
    if fact_key:
        fact = facts.get(fact_key)
        if fact is not None and fact.assertion == Assertion.UNKNOWN:
            sentence = "前面这项你说过还没注意过，这里再确认一下："
            return (sentence, "denial") if lint_bridging_sentence(sentence) else (None, "lint_dropped")
        if fact is not None and fact.assertion == Assertion.ABSENT:
            denied = _render_value(fact.value)
            if denied:
                sentence = f"前面你说过没有{denied}，这里再确认一下相关的部分："
                return (sentence, "denial") if lint_bridging_sentence(sentence) else (None, "lint_dropped")

    # 3. Entry: first pathway-detail question, anchored on the chief complaint.
    chief = _present_text(facts, "chief_complaint")
    if fact_key in DETAIL_FACT_KEYS and fact_key not in facts and chief:
        sentence = f"你说{chief}，再细问一下这方面的情况："
        return (sentence, "entry") if lint_bridging_sentence(sentence) else (None, "lint_dropped")

    # 4. Generic anchor, rotated over the recorded PRESENT facts so
    # consecutive rounds do not repeat the same wording.
    candidates: List[str] = []
    if chief:
        candidates.append(f"你说{chief}，再问一句：")
    onset = _present_text(facts, "onset")
    if onset:
        onset = _strip_trailing_start(onset)
        if onset:
            candidates.append(f"从{onset}开始到现在，再问一句：")
    severity = _severity_text(facts)
    if severity:
        candidates.append(f"你说现在大约{severity}，再问一句：")
    associated = _present_text(facts, "associated_symptoms")
    if associated:
        if chief:
            candidates.append(f"除了{chief}，你还提到{associated}，再问一句：")
        else:
            candidates.append(f"你还提到{associated}，再问一句：")
    history = _present_text(facts, "relevant_history")
    if history:
        history = _strip_leading_verbs(history, ("提到过", "提到"))
        if history:
            candidates.append(f"你提到{history}，再问一句：")
    medications = _present_text(facts, "medications_taken")
    if medications:
        medications = _strip_leading_verbs(medications, _MEDICATION_LEADING_VERBS)
        if medications:
            candidates.append(f"你说用过{medications}，再问一句：")
    goal = _present_text(facts, "patient_goal")
    if goal:
        candidates.append(f"你最担心的是{goal}，再问一句：")
    # Widen the pool to recently recorded detail facts (run7: photophobia,
    # neck stiffness, radiating pain, scratching, shower aggravation and
    # first-time shrimp were never re-anchored). Detail anchors are appended
    # after the early-fact anchors so existing rotation order is preserved;
    # the fact currently being asked about never anchors its own question.
    for key in DETAIL_FACT_KEYS_ORDERED:
        if key == fact_key:
            continue
        detail = _present_text(facts, key)
        if detail:
            candidates.append(f"你还提到{detail}，再问一句：")
    exposures = _present_text(facts, "recent_exposures")
    if exposures:
        candidates.append(f"你还提到{exposures}，再问一句：")
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None, "no_anchor"
    sentence = candidates[question_turn_index % len(candidates)]
    return (sentence, "anchor") if lint_bridging_sentence(sentence) else (None, "lint_dropped")
