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
  血压/胸围 and the same negation guard.

A future reasoning-review provider (design phase 3) plugs in behind the
``SemanticProvider`` protocol; anything it returns may only *add* hits.

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
                if phrase_not_negated(text, match.start()) and not span_trailing_denied(
                    text, match.end()
                ):
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
                    if span_trailing_denied(text, match.end()):
                        continue
                elif not span_not_negated(
                    text, match.start(), match.end()
                ) or span_trailing_denied(text, match.end()):
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
        best: Optional[Tuple[int, int]] = None
        for loc in locations:
            for sig in signals:
                # A signal directly negated in place (胸口不疼, 不闷) never
                # counts, however close the location is.
                if sig[0] > 0 and text[sig[0] - 1] in "不没无":
                    continue
                gap = max(0, max(loc[0], sig[0]) - min(loc[1], sig[1]))
                if gap > combo.window:
                    continue
                span = (min(loc[0], sig[0]), max(loc[1], sig[1]))
                if best is None or span[1] - span[0] < best[1] - best[0]:
                    best = span
        if best is None:
            return None
        if not span_not_negated(text, best[0], best[1]):
            return None
        if span_trailing_denied(text, best[1]):
            return None
        return FlagHit(definition.id, definition.label, "semantic_combo", text[best[0]:best[1]])

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
                hits.append(
                    FlagHit(extra.flag_id, definition.label, "provider", extra.evidence)
                )
                seen.add(extra.flag_id)
        return hits
