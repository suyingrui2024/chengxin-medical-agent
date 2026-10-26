"""W4 (round 3, P-17): gi_details bundle must not be asked of infants.

Third-round paediatric case P5 (infant dehydration-like presentation,
1-year-old, parent reporting): the gi_details bundle asked for pain
location / character ("腹痛在哪个位置、是什么样的痛…") and the parent
answered "宝宝还小，说不清肚子疼的位置和疼法". The bundle is a single
fixed prompt string, so the question-layer fix (triage branch (a)) is
an applicability gate: for an infant subject — same predicate as
``conclusion._is_infant_case`` (child consultation + age <= 3, or child
consultation + a recorded "说不清 + 疼/痛" fact) — gi_details does not
apply, and the catalog's infant_observable_signs question (caregiver-
observable items) remains askable as the replacement. Older children
and adults keep the bundle unchanged.
"""

from __future__ import annotations

import unittest

from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.knowledge import ContentRepository


def _state(chief, extra=()):
    state = CaseState(session_id="round3-infant-gi-test")
    state.record(
        Fact(
            key="chief_complaint",
            value=chief,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=chief,
        )
    )
    for key, value, evidence in extra:
        state.record(
            Fact(
                key=key,
                value=value,
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence=evidence,
            )
        )
    return state


class TestInfantGiDetailsGate(unittest.TestCase):
    def setUp(self):
        self.repo = ContentRepository()
        self.gi = self.repo.question("gi_details")
        self.infant = self.repo.question("infant_observable_signs")

    def test_p5_shape_gi_bundle_suppressed_infant_question_askable(self):
        # Round-3 P5 verbatim shape: parent consulting for a 1-year-old
        # with diarrhoea; age was recorded (turn 3) before the gi round.
        state = _state(
            "拉肚子一天了，嘴干，尿很少，精神蔫蔫的",
            [
                ("consult_for", "child", "我家宝宝"),
                ("age_years", 1, "宝宝1岁"),
            ],
        )
        self.assertFalse(self.repo.question_applies_in_state(self.gi, state))
        self.assertTrue(
            self.repo.question_applies_in_state(self.infant, state)
        )

    def test_adult_self_consultation_keeps_gi_bundle(self):
        state = _state(
            "拉肚子，肚子绞着疼",
            [("consult_for", "self", "我自己")],
        )
        self.assertTrue(self.repo.question_applies_in_state(self.gi, state))

    def test_older_child_keeps_gi_bundle(self):
        # Above the reused 3-year bound, the adult wording is kept —
        # same boundary as conclusion._is_infant_case.
        state = _state(
            "拉肚子，肚子疼",
            [
                ("consult_for", "child", "孩子"),
                ("age_years", 5, "5岁"),
            ],
        )
        self.assertTrue(self.repo.question_applies_in_state(self.gi, state))

    def test_age_three_boundary_is_infant(self):
        state = _state(
            "拉肚子，肚子疼",
            [
                ("consult_for", "child", "孩子"),
                ("age_years", 3, "3岁"),
            ],
        )
        self.assertFalse(self.repo.question_applies_in_state(self.gi, state))

    def test_cannot_describe_pain_gate_without_known_age(self):
        # Secondary _is_infant_case gate: child consultation, age not
        # captured, but a recorded fact already says pain cannot be
        # described — the bundle must not be (re-)asked.
        state = _state(
            "拉肚子，肚子疼",
            [
                ("consult_for", "child", "孩子"),
                (
                    "associated_symptoms",
                    "宝宝还小，说不清肚子疼的位置和疼法",
                    "宝宝还小，说不清肚子疼的位置和疼法",
                ),
            ],
        )
        self.assertFalse(self.repo.question_applies_in_state(self.gi, state))
        self.assertTrue(
            self.repo.question_applies_in_state(self.infant, state)
        )

    def test_child_unknown_age_without_unreportable_pain_keeps_bundle(self):
        # Mirrors the prescribed predicate exactly: without a known age
        # <= 3 and without a 说不清 + 疼/痛 fact, the bundle still
        # applies (narrow gap: the age question precedes pathway-detail
        # questions in phase order, so age is normally known by then).
        state = _state(
            "拉肚子，肚子疼",
            [("consult_for", "child", "孩子")],
        )
        self.assertTrue(self.repo.question_applies_in_state(self.gi, state))


if __name__ == "__main__":
    unittest.main()
