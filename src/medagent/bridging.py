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

Group bridges (hypothesis-driven Tier 1, design §3.2) are a separate,
pure-function layer at the end of this module: ``question_group`` classifies
a question id into one of four process groups, ``build_group_bridge``
returns the whitelisted template for a group, and ``lint_group_bridge``
rejects any bridge text that names a hypothesis, a department, or uses
a predictive/conclusive turn of phrase. Wiring a group bridge into
``_record_reply`` belongs to the P2 package, not to this module: nothing
here touches either engine.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .conclusion import BANNED_PHRASES
from .domain import Assertion, CaseState, Fact
from .text_match import find_unnegated

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
# before the question itself. "先确认" and "好判断" are run7-era variants
# of the same purpose half-sentence (healthcheck P2-A-D6: "先确认一下基本
# 信息、好判断接下来哪些筛查适用" was rejected although it already says
# why the question is asked, costing one extra self-correction round).
# They are purpose clauses, not bare openers: a question that merely
# opens with a bridging phrase and asks directly still carries no marker.
WHY_MARKERS = (
    "为了确认",
    "为了区分",
    "为了判断",
    "想先确认",
    "想确认",
    "需要确认",
    "先确认",
    "好判断",
    "以便",
    "因为",
    "为了",
)

# Repair guidance fed back through the self-correction loop when a model
# question lacks its own bridging sentence (B route).
MODEL_BRIDGING_REPAIR_HINT = (
    "问题文本须自带承接句与‘为什么问’半句：先以‘你说/你提到’等呼应已记录事实开头，"
    "再用‘为了…/想确认…/先确认…/好判断…’说明为什么问，然后只问这一件事；无可锚事实的开场轮次可直接提问。"
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


# Batch C POLISH (S-07/P-10): a slot that already ends with sentence
# punctuation must not gain another delimiter from the template
# ("坐着也难受。" + "，" produced "难受。，"). Slots are stripped of
# trailing punctuation at render time, so the template's own delimiter
# is the only one that survives.
_TRAILING_PUNCT = "。！!？?；;，,、 "


def _strip_trailing_punct(text: str) -> str:
    return text.rstrip(_TRAILING_PUNCT)


def _normalize_bridging_sentence(sentence: str) -> str:
    """Final safety net over a composed bridging sentence.

    Slot-level stripping covers the usual case; this pass catches
    punctuation or verb doubling that survives inside a slot value
    itself (e.g. an onset slot that already contains "开始的开始").
    """
    fixed = sentence.replace("。，", "。").replace("，，", "，")
    fixed = fixed.replace("开始的开始", "开始").replace("开始开始", "开始")
    return fixed


def _fragments(text: str) -> List[str]:
    """Split a slot value into symptom fragments for overlap checks."""
    parts = []
    current = ""
    for char in text:
        if char in "、，；。":
            if current.strip():
                parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _fragment_covered(fragment: str, text: str) -> bool:
    """True when ``fragment`` already appears inside ``text``.

    Particles ("也/都/还") are ignored on both sides: chief "说话也不清楚"
    covers associated "说话不清楚", so the same symptom is never echoed
    twice in one bridging sentence (batch C N-09).
    """
    norm = lambda s: s.replace("也", "").replace("都", "").replace("还", "")
    return norm(fragment) in norm(text)


def _uncovered_fragments(text: str, covered_by: str) -> List[str]:
    return [part for part in _fragments(text) if not _fragment_covered(part, covered_by)]


def _render_value(value: Any) -> Optional[str]:
    """Render a fact value as short patient-facing text, or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = _strip_trailing_punct(value.strip())
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
    (run7: "从一天半前开始开始到现在").

    Batch C POLISH (P-05/P-10): the same doubling hides in two more
    shapes -- a slot that already ends in "开始的" ("从昨天开始的开始")
    and a completed-state slot ("两天了" + "从…开始" gave
    "从两天了开始到现在"). All three are stripped so the template's
    own "开始" is the only one that survives.

    Hypothesis-implementation P-23 audit: a fourth shape is an onset
    slot that already ends in "到现在" ("从三天前到现在开始到现在").
    Stripping loops until stable so a slot carrying both a trailing
    "到现在" and a trailing "开始" ("三天前开始到现在") still reduces
    to the bare time expression.
    """
    changed = True
    while changed:
        changed = False
        for suffix in ("到现在", "开始的", "开始", "了"):
            if text.endswith(suffix):
                text = text[: -len(suffix)]
                changed = True
                break
    return text


def _strip_onset_slot(text: str) -> str:
    """Strip template verbs from an onset slot (P-23).

    The onset anchor template already supplies both "从" and "开始到现在",
    so a slot that itself starts with "从" ("从昨天开始") would double
    the "从" exactly as a trailing "开始" used to double the "开始".
    """
    if text.startswith("从"):
        text = text[1:]
    return _strip_trailing_start(text)


# Onset verbs marking a slot as already expressing onset as a completed
# clause (round 3, P-23): "昨天开始拉肚子的" already says when the
# symptom began, so fitting it into the "从……开始到现在" template
# stacks the verb ("开始拉肚子的开始"). A slot that still carries one
# of these verbs after stripping is never re-templated -- the onset
# candidate is dropped and rotation falls back to a generic anchor that
# does not carry the fact (or to no bridge when no other anchor
# exists). The fact itself is untouched; only its rendering as an
# anchor is suppressed. A slot that reduces to a bare time expression
# after stripping ("一天半前开始" -> "一天半前") keeps the template;
# the pinned regression for that shape is preserved.
_ONSET_VERBS = ("开始", "出现", "发病", "犯", "得了", "患上")


def _onset_anchor_text(text: str) -> Optional[str]:
    """Render an onset slot for the 从…开始到现在 template, or None.

    Returns None for a completed-state slot (see _ONSET_VERBS): the
    caller drops the onset candidate rather than stacking the verb.
    """
    stripped = _strip_onset_slot(text)
    if not stripped or any(verb in stripped for verb in _ONSET_VERBS):
        return None
    return stripped


def _has_verb_stacking(sentence: str) -> bool:
    """True when a composed anchor stacks the onset template's own verbs.

    Second line of defence for P-23: if a candidate somehow still
    carries the template verb twice ("从昨天开始拉肚子的开始到现在"
    has two "开始"), the anchor pick must skip it instead of showing
    the doubled sentence. A single occurrence -- e.g. a chief anchor
    echoing "昨天开始拉肚子" -- is ordinary patient wording, not
    stacking, and passes.
    """
    return sentence.count("开始") > 1 or sentence.count("到现在") > 1


# Leading verbs stripped from a slot when the anchor template already
# supplies that verb (P-23 audit). Each entry is only stripped when the
# slot literally starts with it, so symptom text that merely contains
# the verb elsewhere is untouched.
_MENTION_LEADING_VERBS = (
    "还提到过",
    "还提到",
    "提到过",
    "提到",
)

_WORRY_LEADING_VERBS = (
    "最担心的是",
    "最担心是",
    "最担心",
    "担心是",
    "担心",
)

_DENIAL_LEADING_WORDS = (
    "没有",
    "否认",
    "没",
    "未",
)


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
    # P-23: the anchor template already supplies "大约" ("你说现在大约…"),
    # so a slot that itself starts with "大约" would double it.
    return _strip_leading_verbs(raw, ("大约",))


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
            # P-23: the denial template already supplies "没有", so a
            # slot that itself starts with a denial word ("没有发热")
            # would double it. Fall back to the raw slot only if
            # stripping would empty it entirely.
            if denied:
                stripped_denied = _strip_leading_verbs(denied, _DENIAL_LEADING_WORDS)
                if stripped_denied:
                    denied = stripped_denied
            if denied:
                sentence = f"前面你说过没有{denied}，这里再确认一下相关的部分："
                return (
                    (_normalize_bridging_sentence(sentence), "denial")
                    if lint_bridging_sentence(sentence)
                    else (None, "lint_dropped")
                )

    # 3. Entry: first pathway-detail question, anchored on the chief complaint.
    chief = _present_text(facts, "chief_complaint")
    if fact_key in DETAIL_FACT_KEYS and fact_key not in facts and chief:
        sentence = f"你说{chief}，再细问一下这方面的情况："
        return (
            (_normalize_bridging_sentence(sentence), "entry")
            if lint_bridging_sentence(sentence)
            else (None, "lint_dropped")
        )

    # 4. Generic anchor, rotated over the recorded PRESENT facts so
    # consecutive rounds do not repeat the same wording.
    candidates: List[str] = []
    if chief:
        candidates.append(f"你说{chief}，再问一句：")
    onset = _present_text(facts, "onset")
    if onset:
        # P-23: a completed-state slot yields None here and the onset
        # candidate is dropped outright -- never re-templated.
        onset = _onset_anchor_text(onset)
        if onset:
            candidates.append(f"从{onset}开始到现在，再问一句：")
    severity = _severity_text(facts)
    if severity:
        candidates.append(f"你说现在大约{severity}，再问一句：")
    associated = _present_text(facts, "associated_symptoms")
    if associated:
        # P-23: the template already supplies "提到", so a slot that
        # itself starts with a mention verb would double it.
        associated = _strip_leading_verbs(associated, _MENTION_LEADING_VERBS)
    if associated:
        # Batch C POLISH (N-09): fragments already inside the chief
        # complaint are dropped from the associated echo, so one symptom
        # never appears twice in the same bridging sentence. When every
        # fragment is covered, the associated candidate is skipped
        # outright and rotation falls through to the next anchor.
        uncovered = _uncovered_fragments(associated, chief) if chief else None
        if uncovered is not None:
            if uncovered:
                candidates.append(
                    f"除了{chief}，你还提到{'、'.join(uncovered)}，再问一句："
                )
        elif chief:
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
        # Batch C POLISH (O-08): a denial slot ("没有用过药，热敷后稍好一点")
        # is recorded as PRESENT text, so the "你说用过…" template used to
        # produce the self-contradicting "你说用过没有用过药". A leading
        # denial keeps its own wording and never gains the "用过" verb.
        if medications.startswith(("没有", "没用", "未用", "否认")):
            candidates.append(f"你说{medications}，再问一句：")
        else:
            medications = _strip_leading_verbs(medications, _MEDICATION_LEADING_VERBS)
            if medications:
                candidates.append(f"你说用过{medications}，再问一句：")
    goal = _present_text(facts, "patient_goal")
    if goal:
        # P-23: the template already supplies "最担心的是", so a slot
        # that itself starts with a worry verb ("担心是阑尾炎") would
        # double it.
        goal = _strip_leading_verbs(goal, _WORRY_LEADING_VERBS)
    if goal:
        candidates.append(f"你最担心的是{goal}，再问一句：")
    # Widen the pool to recently recorded detail facts (run7: photophobia,
    # neck stiffness, radiating pain, scratching, shower aggravation and
    # first-time shrimp were never re-anchored). Detail anchors are appended
    # after the early-fact anchors so existing rotation order is preserved;
    # the fact currently being asked about never anchors its own question.
    # Batch C POLISH (N-09): a detail fully covered by the chief complaint
    # is skipped, for the same no-double-echo reason as associated above.
    for key in DETAIL_FACT_KEYS_ORDERED:
        if key == fact_key:
            continue
        detail = _present_text(facts, key)
        if detail:
            # P-23: same mention-verb doubling as the associated slot.
            detail = _strip_leading_verbs(detail, _MENTION_LEADING_VERBS)
        if detail:
            if chief and not _uncovered_fragments(detail, chief):
                continue
            candidates.append(f"你还提到{detail}，再问一句：")
    exposures = _present_text(facts, "recent_exposures")
    if exposures:
        # P-23: same mention-verb doubling as the associated slot.
        exposures = _strip_leading_verbs(exposures, _MENTION_LEADING_VERBS)
    if exposures:
        candidates.append(f"你还提到{exposures}，再问一句：")
    # Backstop (O-08): no candidate may affirm and deny the same use in
    # one breath, whatever slot shape produced it.
    candidates = [
        item
        for item in dict.fromkeys(candidates)
        if "用过没有" not in item and "用过没" not in item
    ]
    if not candidates:
        return None, "no_anchor"
    # P-23 fallback: if the rotated pick still stacks the onset verb
    # (a shape the slot filter did not catch), walk forward to the next
    # candidate that does not stack -- the generic anchor without that
    # fact. If every candidate stacks, show the question unadorned
    # rather than a doubled-verb sentence.
    start = question_turn_index % len(candidates)
    sentence = None
    for offset in range(len(candidates)):
        candidate = candidates[(start + offset) % len(candidates)]
        if not _has_verb_stacking(candidate):
            sentence = candidate
            break
    if sentence is None:
        return None, "no_anchor"
    sentence = _normalize_bridging_sentence(sentence)
    return (sentence, "anchor") if lint_bridging_sentence(sentence) else (None, "lint_dropped")


# ---------------------------------------------------------------------------
# Group-level intent bridges (Tier 1, design §3.2) -- pure functions only.
#
# A group bridge says *why* the next batch of questions is being asked
# (process transparency) without naming any hypothesis, department, or
# direction. Templates are a whitelist: ``GROUP_BRIDGES`` is the only
# source of group-bridge text, and new templates require review -- the
# model never freestyles one. ``lint_group_bridge`` is the red line: on
# any hit the whole bridge sentence is dropped, exactly like the
# single-question lint above. This module performs no wiring: the P2
# package owns calling these functions from ``_record_reply``.
# ---------------------------------------------------------------------------

# Verbatim from design §3.2. Do not paraphrase: the acceptance test pins
# these strings character for character.
GROUP_BRIDGES: Dict[str, str] = {
    "deep_dive": "先把你这个症状本身问清楚，部位、性质、时间这些细节会决定接下来问什么。",
    "discriminating": "接下来几个问题，是想把几种可能区分开。",
    "proactive": "除了你说的这些，我再问几个你可能没留意的表现，有没有都请直接说。",
    "background": "最后补几个背景情况，问完我就把结论整理给你。",
}

# Questions that always anchor the deep-dive group, regardless of chief
# complaint (same definition as the P0 ranker: base attributes plus the
# chief-matching detail questions below).
_DEEP_DIVE_BASE = frozenset({"onset", "trajectory", "severity"})

# Cached content-file lookups (loaded once, read-only; content files are
# never written by this module).
_CONTENT_CACHE: Dict[str, Any] = {}


def _load_content_json(filename: str) -> Any:
    if filename not in _CONTENT_CACHE:
        try:
            raw = resources.files("medagent.content").joinpath(filename).read_text(
                encoding="utf-8"
            )
            _CONTENT_CACHE[filename] = json.loads(raw)
        except Exception:
            _CONTENT_CACHE[filename] = None
    return _CONTENT_CACHE[filename]


def _question_catalog() -> Dict[str, Dict[str, Any]]:
    """question id -> {"phase": str, "chief_terms": [str]} from questions.json."""
    if "questions" not in _CONTENT_CACHE:
        raw = _load_content_json("questions.json")
        catalog: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, dict):
            for item in raw.get("questions", []):
                applies = item.get("applies_when") or {}
                catalog[item["id"]] = {
                    "phase": item.get("phase", ""),
                    "chief_terms": list(
                        applies.get("chief_complaint_contains_any", [])
                    ),
                }
        _CONTENT_CACHE["questions"] = catalog
    return _CONTENT_CACHE["questions"]


def _chief_corpus(state: Optional[CaseState]) -> List[str]:
    """Chief-complaint texts, mirroring knowledge.chief_complaint_texts.

    The recorded value can drift from what the patient actually said, so
    the fact's verbatim ``evidence`` joins the corpus, as does the
    evidence of superseded chief-complaint facts -- but only while a
    current chief-complaint fact exists, so a wiped case never inherits
    a previous subject's complaints.
    """
    if state is None:
        return []
    current = state.facts.get("chief_complaint")
    if current is None:
        return []
    texts = [str(current.value)]
    if current.evidence:
        texts.append(str(current.evidence))
    for fact in state.history:
        if fact.key == "chief_complaint" and fact.evidence:
            texts.append(str(fact.evidence))
    return [text for text in texts if text]


def _chief_term_match(text: str, term: str) -> bool:
    """Negation-aware chief-term match, mirroring knowledge._applies_term_match.

    A term occurrence is support only when it is unnegated (``find_unnegated``)
    and not directly preceded by an immediate 「不是<term>」 denial, which
    names the term only to correct it away.
    """
    lowered = text.lower()
    term = term.lower()
    working = lowered
    while True:
        index = find_unnegated(working, term)
        if index < 0:
            return False
        if not (index >= 2 and working[index - 2 : index] == "不是"):
            return True
        working = working[:index] + "\x00" + working[index + 1 :]


def _chief_matches(state: Optional[CaseState], terms: List[str]) -> bool:
    if not terms:
        return False
    corpus = _chief_corpus(state)
    return any(
        _chief_term_match(text, term) for text in corpus for term in terms
    )


def _chief_text(state: Optional[CaseState]) -> str:
    corpus = _chief_corpus(state)
    return corpus[0] if corpus else ""


def _p3_warning_ids(state: Optional[CaseState]) -> frozenset:
    """Question ids of the P3 out-of-pathway warning set, if P3 has landed.

    P3 warning questions live in their own file, not in questions.json,
    so they are invisible to the catalog lookup. This seam calls P3's
    ``warning_questions(chief)`` when the module exists and returns the
    ids it reports (dict entries under ``id``/``question_id``/``fact_key``/
    ``key``, or bare strings). The P3 module as shipped reports
    ``{"domain": ..., "question": ...}`` dicts with no id field, so a
    ``domain`` entry additionally yields the derived id
    ``warning_<domain>`` (e.g. ``warning_意识``): that derived id is the
    convention P2 wiring must pass to :func:`question_group` for a P3
    warning question. While P3 is not present the seam degrades to an
    empty set; a P3 failure likewise degrades to empty rather than
    breaking group classification.
    """
    try:
        from .out_of_pathway import warning_questions
    except Exception:
        return frozenset()
    try:
        items = warning_questions(_chief_text(state))
    except Exception:
        return frozenset()
    ids = set()
    for item in items or []:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict):
            for key in ("id", "question_id", "fact_key", "key"):
                if item.get(key):
                    ids.add(str(item[key]))
                    break
            else:
                domain = item.get("domain")
                if domain:
                    ids.add(f"warning_{domain}")
    return frozenset(ids)


def question_group(question_id: str, state: CaseState) -> str:
    """Classify a question into its Tier 1 intent-bridge group.

    Order (first match wins):
    1. BACKGROUND-phase questions -> ``background``.
    2. ``associated_symptoms`` and P3 warning questions -> ``proactive``.
    3. HISTORY deep-dive group (base attributes plus chief-matching
       detail questions, same definition as the P0 ranker) -> ``deep_dive``.
    4. Remaining HISTORY questions -> ``discriminating``.
    5. Anything else (opening / emergency screen / observation / summary /
       unknown ids) -> ``other`` (no group bridge).

    Notes: only ``applies_when`` chief-complaint terms count for the
    deep-dive detail test -- ``applies_also_when`` (e.g. fever surfacing
    via associated symptoms) does not by itself promote a question to
    deep-dive. A denied chief term (immediate 「不是<term>」 or another
    negation cue) never counts as a chief match.
    """
    catalog = _question_catalog()
    entry = catalog.get(question_id)
    if entry is not None and entry["phase"] == "background":
        return "background"
    if question_id == "associated_symptoms" or question_id in _p3_warning_ids(state):
        return "proactive"
    if entry is not None and entry["phase"] == "history":
        if question_id in _DEEP_DIVE_BASE or _chief_matches(
            state, entry["chief_terms"]
        ):
            return "deep_dive"
        return "discriminating"
    return "other"


def build_group_bridge(group: str) -> Optional[str]:
    """Return the whitelisted Tier 1 template for ``group``, else None."""
    return GROUP_BRIDGES.get(group)


# Extra Tier 1 banned wording (design §3.2 red line), beyond BRIDGING_BANNED.
# "不是" is handled separately below: it must not fire on "是不是", which
# is a question echo / patient-utterance retelling, not a conclusion.
GROUP_BRIDGE_BANNED_WORDS = (
    "初步考虑",
    "很像",
    "倾向于",
    "排除",
    "急诊",
    "住院",
    "留观",
    "手术",
)


def _has_bare_bushi(text: str) -> bool:
    """True when text contains a conclusive 「不是」 outside 「是不是」."""
    return "不是" in text.replace("是不是", "\x00\x00\x00")


def _hypothesis_labels() -> List[str]:
    """All hypothesis names, loaded live from differentials.json (never hardcoded)."""
    raw = _load_content_json("differentials.json")
    labels: List[str] = []
    if isinstance(raw, dict):
        for pathway in raw.get("pathways", []):
            for hypothesis in pathway.get("hypotheses", []):
                for key in ("label", "patient_label"):
                    value = hypothesis.get(key)
                    if value:
                        labels.append(value)
    return labels


def _department_names() -> List[str]:
    """All department names, loaded live from departments.json (never hardcoded)."""
    raw = _load_content_json("departments.json")
    names: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            value = node.get("department")
            if isinstance(value, str) and value:
                names.append(value)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(raw)
    return names


def lint_group_bridge(text: str) -> bool:
    """Return True when a group-bridge sentence may be shown; False drops it whole.

    Rejects: the existing ``BRIDGING_BANNED`` set, the Tier 1 wording
    ban (with 「不是」 judged outside 「是不是」), any hypothesis name
    from differentials.json (``label`` or ``patient_label``), and any
    department name from departments.json. The lint applies to the bridge
    text itself only, never to patient utterances.
    """
    if any(banned in text for banned in BRIDGING_BANNED):
        return False
    if any(word in text for word in GROUP_BRIDGE_BANNED_WORDS):
        return False
    if _has_bare_bushi(text):
        return False
    if any(label and label in text for label in _hypothesis_labels()):
        return False
    if any(name and name in text for name in _department_names()):
        return False
    return True
