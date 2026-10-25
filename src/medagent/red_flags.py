"""Data-driven red-flag definitions and the layered text scanner.

Layer model (design doc §4.2):
- ``rule_phrase``: exact phrase matching with the historical prefix-window
  negation check. This is the deterministic floor; its behaviour matches
  the original ``triage.py`` implementation except that 、 (enumeration
  comma) counts as a clause separator, so each item of a symptom list is
  its own clause.
- ``semantic_pattern``: curated paraphrase regexes per red flag, guarded by
  clause-scoped negation suppression.
- ``semantic_combo``: location x signal co-occurrence within a window
  (e.g. chest location + pain signal), with exclusion compounds such as
  血压/胸围 and the same negation guard. A combo hit is bare
  co-occurrence, not an explicit affirmed symptom statement, so it
  carries ``FlagHit.requires_clarification=True`` -- UNLESS the sentence
  containing the hit also carries an explicit affirmed discriminating
  feature (see ``_combo_is_discriminated``: diaphoresis, radiation to a
  named site, a pressing/constricting quality phrase, a severe-intensity
  statement, or a compound symptom term of two adjacent signal tokens).
  Such a hit is an explicitly affirmed statement, not bare co-occurrence,
  so it keeps the pre-batch-A deterministic emergency path
  (``requires_clarification=False``). Recurrence wording alone
  （一阵阵）, exertion/relief, and duration statements are NOT
  discriminators. The controller must ask a discriminating clarification
  question first for clarification hits and only take the emergency path
  after the patient confirms. ``scan_text`` / ``assess`` deliberately
  still include combo labels (corpus / release-gate compatibility); the
  clarification gate lives on ``scan_detailed``.

Assertion-state gating (batch A): a hit whose span falls inside an
uncertainty expression （没注意/不清楚/不知道/不确定/没留意/说不清…,
either immediately before the span with only a question scaffold between,
or trailed by such a marker in the same sentence) is not an affirmed
symptom and is dropped. Uncertainty in a *different* clause, or about the
*cause* of an affirmed symptom （我不知道为什么会咳血）, does not
suppress. A 、-separated list led by a negation （也没有A、B、C） likewise
denies every item in that list segment, unless a contrast marker or an
intensifier on the hit's own item breaks the scope.

Definitions live in ``content/red_flags.json`` so clinicians can review and
version them as data. Changing them requires a corpus evaluation report
(``scripts/evaluation/medical/evaluate_red_flags.py``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import List, Optional, Protocol, Tuple

NEGATIONS = ("没有", "无", "否认", "不是", "未出现", "不伴")

_SEPARATORS = re.compile(r"[，。、！？；,;!?\n]|但是|但|不过|现在")


def phrase_not_negated(text: str, start: int) -> bool:
    """Historical check: negation token in the last 6 chars of the clause
    prefix immediately before ``start``. Preserved exactly (the clause
    split now also treats 、 as a boundary; see module docstring)."""
    prefix = _SEPARATORS.split(text[:start])[-1][-6:]
    return not any(token in prefix for token in NEGATIONS)


def _clause_start(text: str, pos: int) -> int:
    start = 0
    for match in _SEPARATORS.finditer(text, 0, pos):
        start = match.end()
    return start


def span_not_negated(text: str, start: int, end: int) -> bool:
    """Clause-scoped check for pattern/combo hits: a negation token anywhere
    in the clause from its start up to the end of the matched span
    suppresses the hit (e.g. 嘴唇没有发紫, 否认胸痛)."""
    window = text[_clause_start(text, start):end]
    return not any(token in window for token in NEGATIONS)


_SENTENCE_BREAK = re.compile(r"[。！？.!?\n；;]")

# Trailing enumeration denial. Chinese symptom lists put the negation
# *after* the items ("发烧、腰痛、呕吐、尿血这些都没有"), where neither
# the prefix check nor the clause window (both look backwards) can see it.
# A hit counts as denied when everything between the end of the hit and
# the end of the sentence is only further list items (each led by 、 or
# ，) plus the denial scaffold itself: optional 这些/那些/以上/这几项 +
# optional 都/全都 + 没有/未出现/没/无 + optional 了/的. Deliberately
# strict — any residual content word defeats the suppression, so true
# positives such as 咳血，没有停 or 呕血没有止住 survive.
#
# Range-word exception: an item led by 其他/别的/其余/其它 scopes the
# denial to symptoms *other than* the ones already stated, so it never
# reaches back to an affirmed hit before it ("吐血，其他症状都没有"
# affirms the bleeding and denies only the rest). Recall-first: any
# range word in the trailing text defeats the suppression outright.
_TRAILING_DENIAL = re.compile(
    r"^(?:[、，][^，。；;、！？!?\n]{1,12}){0,6}"
    r"[、，]?"
    r"(?:这些|那些|以上|这几项|这几个|上述)?"
    r"(?:都|全都|全)?"
    r"(?:没有|未出现|没|无)"
    r"(?:了|的)?"
    r"[\s，。；;、！？!?]*$"
)


_RANGE_WORDS = ("其他", "别的", "其余", "其它")

# Uncertainty markers: the patient has not affirmed the symptom, only that
# they did not notice / do not know / cannot say whether it occurred.
# "算不算" covers a bare screening echo ("算不算大出血") where the patient
# questions the classification rather than affirming the symptom.
_UNCERTAINTY_MARKERS = (
    "没注意",
    "不清楚",
    "不知道",
    "不确定",
    "没留意",
    "说不清",
    "没留心",
    "记不清",
    "不记得",
    "没看清",
    "没印象",
    "不肯定",
    "算不算",
)

# Question scaffolds allowed between an uncertainty marker and the symptom
# it scopes over ("不清楚有没有咳血", "不确定算不算大出血"). Anything else
# in between -- notably a cause word （为什么/原因） -- means the
# uncertainty is not about whether the symptom occurred, so it does not
# suppress ("我不知道为什么会咳血" still affirms 咳血).
_UNCERTAINTY_SCAFFOLD = (
    "是不是",
    "算不算",
    "有没有",
    "有没",
    "是否",
    "到底",
    "究竟",
    "这个",
    "那个",
    "过",
    "呢",
    "吗",
    "也",
    "还",
    "的",
)

_TRAILING_UNCERTAINTY = re.compile(
    r"^(?:[、，和与或][^，。；;、！？!?\n]{1,12}){0,6}"
    r"(?:我|这个|这些)?"
    r"(?:没注意|不清楚|不知道|不确定|没留意|说不清|没留心|记不清|不记得|没看清|没印象|不肯定)"
    # Question scaffold after the marker ("咳血不知道有没有").
    r"(?:是不是|算不算|有没有|有没|是否|到底|究竟|这个|那个|过|呢|吗|也|还|的|了)*"
    r"[\s，。；;、！？!?]*$"
)

_LIST_NEGATION_LEAD = re.compile(
    r"^(?:我|他|她|患者|宝宝|孩子)?(?:也没有|没有|未出现|否认|不伴|无)"
)
_CONTRAST_WORDS = ("但是", "但", "不过", "然而")
_INTENSIFIERS = ("很", "非常", "特别", "确实", "真的", "厉害", "剧烈", "加重")


def _strip_scaffold(text: str) -> str:
    """Remove uncertainty-scaffold tokens repeatedly; what remains, if
    anything, is content that breaks the uncertainty scope."""
    changed = True
    while changed:
        changed = False
        for token in _UNCERTAINTY_SCAFFOLD:
            if text.startswith(token):
                text = text[len(token):]
                changed = True
                break
    return text


def span_is_uncertain(text: str, start: int, end: int) -> bool:
    """True when the hit span is inside an uncertainty expression.

    Two shapes, both clause/sentence-scoped so uncertainty elsewhere in
    the turn cannot suppress an affirmed symptom:
    - preceding: an uncertainty marker in the same clause before the span
      with only question scaffolding between marker and span;
    - trailing: the sentence remainder after the span is only further
      list items plus an uncertainty marker ("胸痛和夜间加重没注意").
    """
    clause_start = _clause_start(text, start)
    prefix = text[clause_start:start]
    for marker in _UNCERTAINTY_MARKERS:
        idx = prefix.find(marker)
        while idx != -1:
            between = prefix[idx + len(marker):]
            if _strip_scaffold(between) == "":
                return True
            idx = prefix.find(marker, idx + 1)
    rest = _SENTENCE_BREAK.split(text[end:], maxsplit=1)[0]
    if rest and _TRAILING_UNCERTAINTY.match(rest) is not None:
        return True
    return False


def span_list_negated(text: str, start: int, end: int) -> bool:
    """True when the hit sits in a 、-separated list led by a negation
    ("也没有抽搐、嘴唇发紫、严重呼吸困难和胸痛").

    Scope is the ，-segment containing the hit: it must start with a
    negation lead, and everything between the lead and the hit must be
    bare list items. A contrast marker, a range word, or an intensifier
    on the hit's own item ("胸痛很厉害") breaks the scope, so a later
    affirmed item in the same segment is not swallowed.
    """
    sent_start = 0
    for match in _SENTENCE_BREAK.finditer(text, 0, start):
        sent_start = match.end()
    sent_end = len(text)
    match = _SENTENCE_BREAK.search(text, end)
    if match is not None:
        sent_end = match.start()
    piece_start = sent_start
    for idx, char in enumerate(text[sent_start:start]):
        if char == "，":
            piece_start = sent_start + idx + 1
    piece_end = sent_end
    for idx in range(start - sent_start, sent_end - sent_start):
        if text[sent_start + idx] == "，":
            piece_end = sent_start + idx
            break
    piece = text[piece_start:piece_end]
    prefix = text[piece_start:start]
    if any(word in prefix for word in _CONTRAST_WORDS):
        return False
    if any(word in prefix for word in _RANGE_WORDS):
        return False
    lead = _LIST_NEGATION_LEAD.match(piece)
    if lead is None:
        return False
    remainder = piece[lead.end():start - piece_start]
    # Remainder ends at the hit, so it is either empty (hit is the first
    # item) or a run of complete items each followed by its separator.
    if not re.fullmatch(
        r"(?:[^、，和与或及]{1,12}[、和与或及])*(?:[^、，和与或及]{1,12})?",
        remainder,
    ):
        return False
    # Intensifier on the hit's own item ("胸痛很厉害") re-affirms it.
    item_tail = text[end:piece_end]
    for sep in ("、", "和", "与", "或", "及"):
        cut = item_tail.find(sep)
        if cut != -1:
            item_tail = item_tail[:cut]
            break
    if any(word in item_tail for word in _INTENSIFIERS):
        return False
    return True


def span_is_suppressed(text: str, start: int, end: int) -> bool:
    """Combined assertion-state suppression: trailing enumeration denial,
    uncertainty expression, or negation-led list scope."""
    return (
        span_trailing_denied(text, end)
        or span_is_uncertain(text, start, end)
        or span_list_negated(text, start, end)
    )


def span_trailing_denied(text: str, end: int) -> bool:
    """True when a hit ending at ``end`` is denied by a trailing
    enumeration denial later in the same sentence (see _TRAILING_DENIAL).
    A range word （其他/别的/其余/其它） anywhere in the trailing text
    scopes the denial away from this hit and defeats the suppression."""
    rest = _SENTENCE_BREAK.split(text[end:], maxsplit=1)[0]
    if not rest:
        return False
    if any(word in rest for word in _RANGE_WORDS):
        return False
    return _TRAILING_DENIAL.match(rest) is not None


@dataclass(frozen=True)
class ComboRule:
    any_location: Tuple[str, ...]
    any_signal: Tuple[str, ...]
    window: int = 12
    exclusions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RedFlagDefinition:
    id: str
    label: str
    level: str = "emergency"
    phrases: Tuple[str, ...] = ()
    patterns: Tuple[str, ...] = ()
    combo: Optional[ComboRule] = None
    # Patterns whose matched text legitimately contains a negation word as
    # part of the symptom itself (e.g. 反应都没有, 喊了半天没有反应). For
    # these, only a negation in the clause *before* the match suppresses.
    negation_exempt_patterns: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FlagHit:
    flag_id: str
    label: str
    layer: str  # rule_phrase | semantic_pattern | semantic_combo | provider
    evidence: str
    # True only for semantic_combo hits whose sentence carries no
    # discriminating feature (see _combo_is_discriminated): the evidence
    # is bare location x signal co-occurrence, not an explicit affirmed
    # symptom statement, so the controller must clarify before the
    # emergency path. Discriminated combo hits keep False.
    requires_clarification: bool = False


def load_definitions() -> Tuple[RedFlagDefinition, ...]:
    raw = (
        resources.files("medagent.content")
        .joinpath("red_flags.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(raw)
    definitions = []
    for item in data["flags"]:
        combo = None
        if item.get("combo"):
            c = item["combo"]
            combo = ComboRule(
                any_location=tuple(c["any_location"]),
                any_signal=tuple(c["any_signal"]),
                window=int(c.get("window", 12)),
                exclusions=tuple(c.get("exclusions", ())),
            )
        definitions.append(
            RedFlagDefinition(
                id=item["id"],
                label=item["label"],
                level=item.get("level", "emergency"),
                phrases=tuple(item.get("phrases", ())),
                patterns=tuple(item.get("patterns", ())),
                combo=combo,
                negation_exempt_patterns=tuple(
                    item.get("negation_exempt_patterns", ())
                ),
            )
        )
    return tuple(definitions)


class SemanticProvider(Protocol):
    """Optional extra recall layer (design phase 3). Implementations return
    candidate hits; the scanner validates flag ids and evidence substrings
    before accepting them, and providers may never remove hits."""

    def scan(self, text: str) -> List[FlagHit]:
        ...


@dataclass
class RedFlagScanner:
    definitions: Tuple[RedFlagDefinition, ...] = field(default_factory=load_definitions)
    provider: Optional[SemanticProvider] = None
    _compiled: List[Tuple[RedFlagDefinition, List[re.Pattern]]] = field(
        default_factory=list, repr=False
    )

    def __post_init__(self) -> None:
        self._compiled = [
            (definition, [re.compile(p) for p in definition.patterns])
            for definition in self.definitions
        ]
        self._by_id = {d.id: d for d in self.definitions}

    # -- layers ---------------------------------------------------------
    def _phrase_hit(self, definition: RedFlagDefinition, text: str) -> Optional[FlagHit]:
        for phrase in definition.phrases:
            for match in re.finditer(re.escape(phrase), text):
                if not phrase_not_negated(text, match.start()):
                    continue
                if span_is_suppressed(text, match.start(), match.end()):
                    continue
                return FlagHit(definition.id, definition.label, "rule_phrase", phrase)
        return None

    def _pattern_hit(
        self, definition: RedFlagDefinition, patterns: List[re.Pattern], text: str
    ) -> Optional[FlagHit]:
        exempt = set(definition.negation_exempt_patterns)
        for pattern in patterns:
            for match in pattern.finditer(text):
                if pattern.pattern in exempt:
                    # Negation inside the match is the symptom itself; only
                    # a negation earlier in the clause suppresses — plus a
                    # trailing enumeration denial after the match, which
                    # denies the match from outside itself
                    # (叫不醒、抽搐、…这些都没有).
                    if not phrase_not_negated(text, match.start()):
                        continue
                    if span_is_suppressed(text, match.start(), match.end()):
                        continue
                else:
                    if not span_not_negated(text, match.start(), match.end()):
                        continue
                    if span_is_suppressed(text, match.start(), match.end()):
                        continue
                return FlagHit(
                    definition.id, definition.label, "semantic_pattern", match.group(0)
                )
        return None

    def _combo_hit(self, definition: RedFlagDefinition, text: str) -> Optional[FlagHit]:
        combo = definition.combo
        if combo is None:
            return None
        excluded_spans: List[Tuple[int, int]] = []
        for token in combo.exclusions:
            for match in re.finditer(re.escape(token), text):
                excluded_spans.append((match.start(), match.end()))

        def occurrences(tokens: Tuple[str, ...]) -> List[Tuple[int, int]]:
            found: List[Tuple[int, int]] = []
            for token in tokens:
                for match in re.finditer(re.escape(token), text):
                    span = (match.start(), match.end())
                    if any(span[0] < e and s < span[1] for s, e in excluded_spans):
                        continue
                    found.append(span)
            return found

        locations = occurrences(combo.any_location)
        signals = occurrences(combo.any_signal)
        candidates: List[Tuple[int, int]] = []
        for loc in locations:
            for sig in signals:
                # A signal directly negated in place (胸口不疼, 不闷) never
                # counts, however close the location is.
                if sig[0] > 0 and text[sig[0] - 1] in "不没无":
                    continue
                gap = max(0, max(loc[0], sig[0]) - min(loc[1], sig[1]))
                if gap > combo.window:
                    continue
                candidates.append((min(loc[0], sig[0]), max(loc[1], sig[1])))
        # Shortest span first, as before; a candidate suppressed by
        # negation / uncertainty / list scope no longer blocks a later
        # affirmed co-occurrence in the same turn.
        for span in sorted(candidates, key=lambda s: (s[1] - s[0], s[0])):
            if not span_not_negated(text, span[0], span[1]):
                continue
            if span_is_suppressed(text, span[0], span[1]):
                continue
            return FlagHit(
                definition.id,
                definition.label,
                "semantic_combo",
                text[span[0]:span[1]],
                requires_clarification=not self._combo_is_discriminated(
                    text, span, combo
                ),
            )
        return None

    # -- combo clarification narrowing ----------------------------------
    # Discriminating features that lift a semantic_combo hit out of the
    # clarification layer. These are NOT new clinical thresholds and do
    # not diagnose anything: they only restore the pre-batch-A split
    # between an explicitly affirmed symptom statement (deterministic
    # path) and bare location x signal co-occurrence (clarify first).
    # Deliberately narrow, so a bare signal token alone never counts:
    # - sweat markers are multi-character diaphoresis words, not bare 汗;
    # - quality markers are phrases beyond the bare signal tokens 压/紧
    #   themselves (像压/压榨/压迫/勒), not 压 or 紧 alone;
    # - radiation needs BOTH a radiation verb (窜/放射) AND a named site
    #   in the same sentence, so a bare 窜 never counts;
    # - severity needs an explicit intensity statement, not a bare signal;
    # - the compound rule needs two ADJACENT signal tokens (闷痛/疼痛),
    #   i.e. a compound symptom term, not two signals anywhere nearby
    #   (so 按着疼 in a later clause of the C-01 sentence does not count).
    # A marker occurrence counts only when it is itself affirmed: a
    # negated or uncertain occurrence (没有出汗) does not discriminate.
    # Scope is the sentence containing the hit, so a discriminator in an
    # unrelated sentence cannot flip a bare hit. Recurrence wording
    # （一阵阵/一阵一阵）, exertion/relief, and duration statements are
    # intentionally absent from these lists.
    _COMBO_SWEAT_MARKERS = ("出冷汗", "冷汗", "冒汗", "出汗", "大汗", "一身汗")
    _COMBO_QUALITY_MARKERS = ("压榨", "压迫", "像压", "勒")
    _COMBO_SEVERITY_MARKERS = (
        "疼得厉害",
        "痛得厉害",
        "闷得厉害",
        "疼得非常厉害",
        "痛得非常厉害",
        "剧烈",
    )
    _COMBO_RADIATION_SIGNALS = ("窜", "放射")
    _COMBO_RADIATION_TARGETS = (
        "左胳膊",
        "左臂",
        "左肩",
        "左手",
        "胳膊",
        "手臂",
        "肩膀",
        "下颌",
        "后背",
    )

    @staticmethod
    def _sentence_span(text: str, pos: int) -> Tuple[int, int]:
        start = 0
        for match in _SENTENCE_BREAK.finditer(text, 0, pos):
            start = match.end()
        end = len(text)
        match = _SENTENCE_BREAK.search(text, pos)
        if match is not None:
            end = match.start()
        return start, end

    @staticmethod
    def _affirmed_in_sentence(
        text: str, sent_start: int, sent_end: int, token: str
    ) -> bool:
        idx = text.find(token, sent_start)
        while idx != -1 and idx + len(token) <= sent_end:
            if span_not_negated(text, idx, idx + len(token)) and not (
                span_is_suppressed(text, idx, idx + len(token))
            ):
                return True
            idx = text.find(token, idx + 1)
        return False

    def _combo_is_discriminated(
        self, text: str, span: Tuple[int, int], combo: ComboRule
    ) -> bool:
        sent_start, sent_end = self._sentence_span(text, span[0])
        for token in (
            self._COMBO_SWEAT_MARKERS
            + self._COMBO_QUALITY_MARKERS
            + self._COMBO_SEVERITY_MARKERS
        ):
            if self._affirmed_in_sentence(text, sent_start, sent_end, token):
                return True
        if any(
            self._affirmed_in_sentence(text, sent_start, sent_end, token)
            for token in self._COMBO_RADIATION_SIGNALS
        ) and any(
            self._affirmed_in_sentence(text, sent_start, sent_end, token)
            for token in self._COMBO_RADIATION_TARGETS
        ):
            return True
        single_signals = {s for s in combo.any_signal if len(s) == 1}
        for idx in range(sent_start, sent_end - 1):
            if text[idx] in single_signals and text[idx + 1] in single_signals:
                if span_not_negated(text, idx, idx + 2) and not (
                    span_is_suppressed(text, idx, idx + 2)
                ):
                    return True
        return False

    # -- public ---------------------------------------------------------
    def scan(self, text: str) -> List[FlagHit]:
        hits: List[FlagHit] = []
        for definition, patterns in self._compiled:
            hit = self._phrase_hit(definition, text)
            if hit is None:
                hit = self._pattern_hit(definition, patterns, text)
            if hit is None:
                hit = self._combo_hit(definition, text)
            if hit is not None:
                hits.append(hit)
        if self.provider is not None:
            seen = {hit.flag_id for hit in hits}
            for extra in self.provider.scan(text):
                definition = self._by_id.get(extra.flag_id)
                if definition is None or extra.flag_id in seen:
                    continue
                if extra.evidence and extra.evidence not in text:
                    continue  # evidence must be a verbatim substring
                # Provider hits pass the same assertion-state gate: every
                # occurrence of the evidence must be uncertain / denied
                # for the hit to be dropped.
                if extra.evidence:
                    affirmed = False
                    idx = text.find(extra.evidence)
                    while idx != -1:
                        if not span_is_suppressed(
                            text, idx, idx + len(extra.evidence)
                        ):
                            affirmed = True
                            break
                        idx = text.find(extra.evidence, idx + 1)
                    if not affirmed:
                        continue
                hits.append(
                    FlagHit(extra.flag_id, definition.label, "provider", extra.evidence)
                )
                seen.add(extra.flag_id)
        return hits
