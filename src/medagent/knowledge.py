from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Dict, List, Mapping, Optional

from .domain import Assertion, Fact, Phase, QuickReply
from .extensions.builtin import load_registry
from .text_match import find_unnegated

# Patient-facing age prompt for non-child subjects, shared by both engines.
# The catalog text ("婴幼儿请尽量写清月龄…") is what the *model* sees in its
# catalog; presenting it verbatim to a 32- or 65-year-old asking for
# themselves reads as a template bug (live-eval cases 1/5/9). Child
# consultations keep the catalog wording, where the months phrasing is
# the point.
ADULT_AGE_PROMPT = "你多大？可直接写年龄，例如 32 岁。"


def _applies_term_match(text: str, term: str) -> bool:
    """Whether ``term`` is supported by one corpus text.

    ``find_unnegated`` semantics (a negated mention never counts as
    support), plus the explicit denial construction 「不是<term>…」:
    a patient correcting themselves ("不是肚子不舒服，是胃胀") names
    the term only to deny it. The negation-cue list in text_match does
    not cover 不是 — there it would be wrong to add it globally, since
    in evidence matching 「不是」 usually denies a *cause*, not the
    symptom term that follows. For applies matching the immediate
    「不是」+ term adjacency is exactly a denial of that term, so each
    unnegated occurrence directly preceded by 不是 is skipped (masked)
    and the search continues with the remaining occurrences.
    """
    working = text
    while True:
        index = find_unnegated(working, term)
        if index < 0:
            return False
        if not (index >= 2 and working[index - 2 : index] == "不是"):
            return True
        working = working[:index] + "\x00" + working[index + 1 :]


def _is_infant_subject(facts: Mapping[str, Fact]) -> bool:
    """Whether the consultation subject is an infant who cannot be
    presumed to report pain location / character (P-17, question layer).

    Mirrors ``conclusion._is_infant_case``: the subject must be a child
    consultation, and either a recorded age of 3 years or under, or a
    recorded fact saying pain cannot be described （说不清 + 疼/痛 in
    the same fact text). The 3-year bound is reused from
    ``_is_infant_case`` / the infant question's ``age_between_when_known``
    window — no new clinical threshold is introduced. Assertion values
    are read the same tolerant way as the infant-question subject gate
    in ``question_applies_in_state`` (enum or plain string), and a
    numeric-string age parses the same way that gate parses it.
    """
    consult = facts.get("consult_for")
    if consult is None:
        return False
    consult_value = (
        consult.value
        if getattr(consult.assertion, "value", consult.assertion) == "present"
        else None
    )
    if consult_value != "child":
        return False
    age_fact = facts.get("age_years")
    age_value = None
    if age_fact is not None and (
        getattr(age_fact.assertion, "value", age_fact.assertion) == "present"
    ):
        raw_age = age_fact.value
        if isinstance(raw_age, bool):
            raw_age = None
        elif isinstance(raw_age, (int, float)):
            age_value = float(raw_age)
        elif isinstance(raw_age, str):
            try:
                age_value = float(raw_age)
            except ValueError:
                age_value = None
    if age_value is not None and age_value <= 3:
        return True
    for fact in facts.values():
        text = ""
        if fact.value is not None:
            text += str(fact.value)
        if fact.evidence:
            text += str(fact.evidence)
        if "说不清" in text and ("疼" in text or "痛" in text):
            return True
    return False


def question_fact_gates_pass(question: "Question", facts: Mapping[str, Fact]) -> bool:
    """Fact-dependent eligibility, evaluated at candidate-computation time.

    A question drops out of the candidate set when a recorded fact
    excludes it (``excludes_fact_equals``, e.g. pregnancy screening
    once sex is recorded male) or when a known age falls outside its
    ``age_between_when_known`` window. An unknown age keeps the
    question eligible: the gate only fires on known facts. Shared by
    the adaptive and deterministic engines so both apply identical
    gating semantics.
    """
    for key, excluded in (question.excludes_fact_equals or {}).items():
        fact = facts.get(key)
        if fact is not None and fact.value in excluded:
            return False
    window = question.age_between_when_known
    if window:
        age = facts.get("age_years")
        if (
            age is not None
            and age.assertion == Assertion.PRESENT
            and isinstance(age.value, (int, float))
            and not isinstance(age.value, bool)
            and not window[0] <= age.value <= window[1]
        ):
            return False
    return True


def _emergency_screen_absent(state) -> bool:
    """Whether the combined emergency screen was explicitly answered 「没有」.

    Only an explicit absent answer subsumes the covered warning domains
    (see ``out_of_pathway.SUBSUMED_BY_EMERGENCY_SCREEN``). An unanswered
    screen, or one answered 「不确定」, leaves those domains to be asked:
    uncertainty is not a negative, which is the same rule the rest of
    the engine applies to denied vs. unconfirmed mentions.
    """
    facts = getattr(state, "facts", None) or {}
    fact = facts.get("emergency_screen")
    if fact is None:
        return False
    return getattr(fact.assertion, "value", fact.assertion) == "absent"


@dataclass(frozen=True)
class Question:
    id: str
    phase: Phase
    fact_key: str
    prompt: str
    input: str
    options: List[QuickReply]
    applies_when: Dict[str, Any]
    # Wave-2 gating metadata. ``blocking=False`` questions stay askable and
    # recordable but never gate review_summary / controller auto-summary.
    # ``excludes_fact_equals`` / ``age_between_when_known`` are evaluated
    # against current facts at candidate-computation time; they are engine
    # metadata and are never forwarded into the model's question catalog.
    blocking: bool = True
    excludes_fact_equals: Dict[str, List[Any]] = field(default_factory=dict)
    age_between_when_known: Optional[List[float]] = None
    # Fact-keyed fallback applicability, evaluated only by
    # ``question_applies_in_state`` (never by the chief-only
    # ``question_applies``): {fact_key: [terms]}. A question applies
    # when the named fact is recorded PRESENT and its value/evidence
    # contains an unnegated term. Currently used by
    # reported_temperature only: fever mentioned in associated
    # symptoms (not just the chief complaint) makes the temperature
    # question requestable (batch A, G-09 note). Denied or merely
    # unconfirmed mentions never trigger it (negation-aware match +
    # PRESENT assertion requirement).
    applies_also_when: Dict[str, List[str]] = field(default_factory=dict)


class ContentRepository:
    def __init__(self, registry=None) -> None:
        package = resources.files("medagent.content")
        self._questions_raw = json.loads(package.joinpath("questions.json").read_text(encoding="utf-8"))
        self.registry = registry or load_registry()
        # P3 off-pathway warning domains are compiled from their own
        # content file and appended to the askable catalog. They stay
        # out of ``questions.json`` (the P3 contract), and their
        # ``applies_when={"off_pathway": True}`` marker makes them
        # inapplicable for every pathway chief, so both engines see
        # them only where the pathway model has nothing to offer.
        self.warning_questions = self._make_warning_questions()
        self.questions = [
            self._make_question(item) for item in self._questions_raw["questions"]
        ] + self.warning_questions
        self.tasks = self.registry.tasks
        if {task["result_key"] for task in self.tasks.values()} & {q.fact_key for q in self.questions}:
            raise ValueError("观察结果不能覆盖核心问诊字段")

    @staticmethod
    def _make_warning_questions() -> List["Question"]:
        """Compile the P3 warning domains into askable questions.

        Input is ``yes_no_unknown`` with the same three options as the
        combined emergency screen, so intake records them through the
        existing interpreter path. ``blocking=True`` is deliberate: an
        off-pathway consultation has no pathway-specific red-flag
        screen, so these are its safety net and must be asked before
        the summary gate opens (design §4 rule 2).
        """
        try:
            from .out_of_pathway import warning_domain_specs
        except Exception:  # pragma: no cover - content always ships
            return []
        return [
            Question(
                id=spec["id"],
                phase=Phase.HISTORY,
                fact_key=spec["fact_key"],
                prompt=spec["question"],
                input="yes_no_unknown",
                options=[
                    QuickReply("有", "yes"),
                    QuickReply("没有", "no"),
                    QuickReply("不确定", "unknown"),
                ],
                applies_when={"off_pathway": True},
                blocking=True,
            )
            for spec in warning_domain_specs()
        ]

    @staticmethod
    def _make_question(raw: Dict[str, Any]) -> Question:
        return Question(
            id=raw["id"],
            phase=Phase(raw["phase"]),
            fact_key=raw["fact_key"],
            prompt=raw["prompt"],
            input=raw["input"],
            options=[QuickReply(**item) for item in raw.get("options", [])],
            applies_when=dict(raw.get("applies_when", {})),
            blocking=bool(raw.get("blocking", True)),
            excludes_fact_equals={
                str(key): list(values)
                for key, values in dict(raw.get("excludes_fact_equals", {})).items()
            },
            age_between_when_known=(
                list(raw["age_between_when_known"])
                if raw.get("age_between_when_known")
                else None
            ),
            applies_also_when={
                str(key): list(values)
                for key, values in dict(raw.get("applies_also_when", {})).items()
            },
        )

    def question(self, question_id: str) -> Question:
        for item in self.questions:
            if item.id == question_id:
                return item
        raise KeyError(question_id)

    def applicable_task(self, chief_complaint: str) -> Optional[Dict[str, Any]]:
        tasks = self.applicable_tasks(chief_complaint)
        return tasks[0] if tasks else None

    def applicable_tasks(self, chief_complaint: str) -> List[Dict[str, Any]]:
        complaint = chief_complaint.lower()
        output = []
        for task in self.tasks.values():
            terms = task.get("applies_when", {}).get("chief_complaint_contains_any", [])
            if any(term.lower() in complaint for term in terms):
                output.append(dict(task))
        return output

    @staticmethod
    def question_applies(question: Question, chief_complaint: str) -> bool:
        # P3 off-pathway warnings are decidable from the chief text
        # alone: they apply exactly when no pathway matches. Without
        # this branch the marker key would be ignored and the warnings
        # would look applicable to every chief, including pathway ones.
        if question.applies_when.get("off_pathway"):
            from .conclusion import chief_matches_pathway

            chief = (chief_complaint or "").strip()
            return bool(chief) and not chief_matches_pathway(chief)
        terms = question.applies_when.get("chief_complaint_contains_any", [])
        if not terms:
            return True
        value = chief_complaint.lower()
        return any(_applies_term_match(value, term.lower()) for term in terms)

    @staticmethod
    def chief_complaint_texts(state) -> List[str]:
        """Corpus that ``question_applies`` should be evaluated against.

        The recorded chief-complaint *value* is the model's normalized
        restatement and can drift from what the patient actually said:
        「肚子不舒服」 was once recorded as 「肚子时不时不舒服」, after
        which the gi question's applies term 「肚子不舒服」 no longer
        matched and the fixed question was wrongly rejected. The fact's
        ``evidence`` is the verbatim patient utterance, validated as a
        substring of the patient's message at record time in both
        engines, so it joins the corpus alongside the value — as does
        the evidence of superseded chief-complaint facts in the history
        (a correction turn quotes the patient's own earlier words).
        Superseded evidence only counts while a current chief-complaint
        fact exists, so a wiped case never inherits a previous
        subject's complaints. Matching stays negation-aware per text, so
        adding texts can only widen, never narrow, applicability.
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
        return texts

    def question_applies_in_state(self, question: Question, state) -> bool:
        """``question_applies`` over the state's full chief-complaint
        corpus (recorded value + verbatim patient utterances). Every
        candidate computation — adaptive engine, deterministic engine,
        differential planner — goes through here so no path keeps the
        old value-only behaviour. After the chief-corpus check, a
        question's ``applies_also_when`` metadata is evaluated against
        the named facts' values + evidence (PRESENT facts only,
        negation-aware): this is how a fever recorded only under
        associated_symptoms makes reported_temperature requestable.

        P4 (G-11 / E-10): the infant-observable question has its own
        subject gate on top of the chief check, because its old
        "unknown age keeps it eligible" behaviour asked adults who
        had already said they consult for themselves about babies.
        It applies only when the subject is plausibly an infant:
        consult_for=self never applies; a known age over 3 never
        applies; a known age of 3 or under applies; a consultation
        for a child applies even when the age is still unknown; an
        unknown age with no child consultation does not apply. The
        question text (questions.json) and the separate
        age_between_when_known fact gate are untouched.

        P-17 (round 3, question layer): the gi_details bundle asks for
        pain location / character, which presumes the subject can report
        pain. For an infant subject (same predicate as
        ``conclusion._is_infant_case``) the bundle is not applicable at
        all — branch (a) of the W4 triage — and the catalog's
        infant_observable_signs question (caregiver-observable items)
        carries the GI detail round instead. Sub-item shielding (branch
        (b)) is not implementable here: the bundle is a single fixed
        prompt string rendered verbatim by ask_question paths outside
        this module, so shielding only its pain sub-items would need a
        catalog split (questions.json, supervisor approval) — see the
        W4 report for the non-pain sub-item coverage check."""
        if question.id == "gi_details" and state is not None:
            if _is_infant_subject(getattr(state, "facts", {}) or {}):
                return False
        if question.id == "infant_observable_signs" and state is not None:
            facts = getattr(state, "facts", {}) or {}
            consult = facts.get("consult_for")
            consult_value = (
                consult.value
                if consult is not None
                and getattr(consult, "assertion", None) is not None
                and getattr(consult.assertion, "value", consult.assertion)
                == "present"
                else None
            )
            if consult_value == "self":
                return False
            age_fact = facts.get("age_years")
            age_value = None
            if age_fact is not None and (
                getattr(age_fact.assertion, "value", age_fact.assertion)
                == "present"
            ):
                raw_age = age_fact.value
                if isinstance(raw_age, bool):
                    raw_age = None
                elif isinstance(raw_age, (int, float)):
                    age_value = float(raw_age)
                elif isinstance(raw_age, str):
                    try:
                        age_value = float(raw_age)
                    except ValueError:
                        age_value = None
            if age_value is not None:
                if age_value > 3:
                    return False
            elif consult_value != "child":
                return False
        # P3 off-pathway warning domains: applicability is the *absence*
        # of a pathway, not a chief term, so it cannot be expressed in
        # ``applies_when.chief_complaint_contains_any``. Evaluated on the
        # same chief-complaint corpus as everything else (value +
        # verbatim patient utterances), and narrowed by the emergency
        # screen's explicit negative — see
        # ``out_of_pathway.warning_domain_specs``.
        if question.applies_when.get("off_pathway"):
            if state is None:
                return False
            texts = [text for text in self.chief_complaint_texts(state) if text]
            if not texts:
                return False
            from .conclusion import chief_matches_pathway
            from .out_of_pathway import warning_domain_specs

            if any(chief_matches_pathway(text) for text in texts):
                return False
            allowed = {
                spec["id"]
                for spec in warning_domain_specs(_emergency_screen_absent(state))
            }
            return question.id in allowed
        terms = question.applies_when.get("chief_complaint_contains_any", [])
        if not terms:
            return True
        lowered_terms = [term.lower() for term in terms]
        if any(
            _applies_term_match(text.lower(), term)
            for text in self.chief_complaint_texts(state)
            if text
            for term in lowered_terms
        ):
            return True
        if state is None:
            return False
        for fact_key, extra_terms in (question.applies_also_when or {}).items():
            fact = state.facts.get(fact_key)
            if fact is None or fact.assertion != Assertion.PRESENT:
                continue
            texts = [str(fact.value)] + (
                [str(fact.evidence)] if fact.evidence else []
            )
            if any(
                _applies_term_match(text.lower(), term.lower())
                for text in texts
                if text
                for term in extra_terms
            ):
                return True
        return False

    @property
    def versions(self) -> Dict[str, str]:
        return {
            "questions": self._questions_raw["version"],
            "questions_sha256": hashlib.sha256(json.dumps(self._questions_raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "self_checks": self.registry.revision,
        }

