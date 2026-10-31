"""P1 wiring: the P3 off-pathway warning screen reaches the patient.

The P3 package shipped content (``warning_signs.json``) plus a contract
(``out_of_pathway.warning_questions``) but nothing compiled it into the
question layer: the ids were id-less, so ``_warning_ids`` returned []
forever and 42% of chiefs (no differential pathway) were screened for
no danger-signal domain at all. These tests pin the wiring end to end:

1. the questions exist in the askable catalog with the derived
   ``warning_<domain>`` id that ``bridging._p3_warning_ids`` already
   derives;
2. they apply to an off-pathway chief and never to a pathway chief;
3. the combined ``emergency_screen`` answer narrows the set (three
   domains are subsumed by it; only an explicit 「都没有」 counts);
4. a driven consultation actually asks them and records the answers;
5. every fact key that can land has a patient-facing handoff label.
"""

from __future__ import annotations

import unittest

from medagent.consultation import UserInput
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.handoff import LABELS
from medagent.knowledge import ContentRepository
from medagent.runtime import build_runtime

# Off-pathway chiefs (no differentials.json pathway term matches) and
# pathway chiefs (they do), used only as applicability fixtures.
OFF_PATHWAY_CHIEFS = ["膝盖疼", "眼睛闪光", "失眠", "牙龈出血"]
PATHWAY_CHIEFS = ["咳嗽两天", "胸痛", "头痛", "尿频尿急", "身上起疹子", "腰痛"]

SUBSUMED_IDS = {"warning_意识", "warning_呼吸", "warning_出血"}
UNCOVERED_IDS = {"warning_剧痛", "warning_持续加重", "warning_功能骤降"}


def _applies(repo, state, question_id):
    question = repo.question(question_id)
    return repo.question_applies_in_state(question, state)


class _State:
    """Minimal real CaseState: chief corpus + facts."""

    def __new__(cls, chief, facts=None):
        state = CaseState(session_id="t-off-pathway")
        state.facts["chief_complaint"] = _fact("chief_complaint", chief)
        for key, fact in (facts or {}).items():
            state.facts[key] = fact
        return state


def _fact(key, value, assertion="present", evidence=None):
    return Fact(
        key=key,
        value=value,
        assertion=Assertion(assertion),
        source=Source.PATIENT,
        turn_id="t1",
        evidence=evidence if evidence is not None else (str(value) if value else None),
    )


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.repo = ContentRepository()

    def test_warning_questions_are_askable_with_derived_ids(self):
        ids = {q.id for q in self.repo.warning_questions}
        self.assertEqual(ids, SUBSUMED_IDS | UNCOVERED_IDS)
        for question in self.repo.warning_questions:
            self.assertEqual(question.id, question.fact_key)
            self.assertEqual(question.phase.value, "history")
            self.assertEqual(question.input, "yes_no_unknown")
            self.assertTrue(question.blocking)
            # Addressable by id so intake can record the answer.
            self.assertIs(self.repo.question(question.id), question)

    def test_warning_questions_stay_out_of_questions_json(self):
        # The P3 contract keeps warning content in its own file; the
        # repository merge is a compile step, not a content edit.
        raw_ids = {item["id"] for item in self.repo._questions_raw["questions"]}
        self.assertFalse(any(qid.startswith("warning_") for qid in raw_ids))


class ApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.repo = ContentRepository()

    def test_off_pathway_chief_gets_the_uncovered_domains(self):
        for chief in OFF_PATHWAY_CHIEFS:
            state = _State(chief, {"emergency_screen": _fact("emergency_screen", None, "absent", "都没有")})
            applied = {
                q.id for q in self.repo.warning_questions if _applies(self.repo, state, q.id)
            }
            self.assertEqual(applied, UNCOVERED_IDS, chief)

    def test_pathway_chief_gets_none(self):
        for chief in PATHWAY_CHIEFS:
            state = _State(chief, {"emergency_screen": _fact("emergency_screen", None, "absent", "都没有")})
            applied = {
                q.id for q in self.repo.warning_questions if _applies(self.repo, state, q.id)
            }
            self.assertEqual(applied, set(), chief)

    def test_unanswered_or_uncertain_screen_asks_all_six(self):
        # Uncertainty is not a negative: only an explicit absent screen
        # subsumes the three covered domains.
        for facts in (
            {},
            {"emergency_screen": _fact("emergency_screen", None, "unknown", "不确定")},
        ):
            state = _State("膝盖疼", facts)
            applied = {
                q.id for q in self.repo.warning_questions if _applies(self.repo, state, q.id)
            }
            self.assertEqual(applied, SUBSUMED_IDS | UNCOVERED_IDS, facts)

    def test_empty_chief_asks_nothing(self):
        # No chief recorded yet: the screen is not a fallback for the
        # opening questions.
        state = _State("")
        applied = {
            q.id for q in self.repo.warning_questions if _applies(self.repo, state, q.id)
        }
        self.assertEqual(applied, set())


class DrivenConsultationTests(unittest.TestCase):
    """A real (offline) consultation must ask them and record answers."""

    def setUp(self):
        self.host = build_runtime(backend="demo")
        self.engine = self.host.start().services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _drive(self, chief, emergency="no", limit=24):
        state, reply = self.engine.create_session()
        asked = []
        for _ in range(limit):
            question_id = reply.question_id or reply.kind
            asked.append(question_id)
            if reply.kind in {"summary", "closed", "emergency", "notice"}:
                break
            if question_id == "consult_for":
                reply = self.engine.submit(state.session_id, UserInput("我自己", "self"))
            elif question_id == "sex":
                reply = self.engine.submit(state.session_id, UserInput("男", "male"))
            elif question_id == "age":
                reply = self.engine.submit(state.session_id, UserInput("32岁"))
            elif question_id == "chief_complaint":
                reply = self.engine.submit(state.session_id, UserInput(chief))
            elif question_id == "emergency_screen":
                reply = self.engine.submit(state.session_id, UserInput("都没有", emergency))
            elif question_id and question_id.startswith("warning_"):
                reply = self.engine.submit(state.session_id, UserInput("没有", "no"))
            elif reply.quick_replies:
                first = reply.quick_replies[0]
                reply = self.engine.submit(
                    state.session_id, UserInput(first.label, first.value)
                )
            else:
                reply = self.engine.submit(state.session_id, UserInput("没有其他了"))
        return state, asked

    def test_off_pathway_consultation_asks_and_records_the_screen(self):
        for chief in OFF_PATHWAY_CHIEFS:
            state, asked = self._drive(chief)
            asked_warnings = [q for q in asked if q and q.startswith("warning_")]
            self.assertEqual(set(asked_warnings), UNCOVERED_IDS, chief)
            recorded = {key for key in state.facts if key.startswith("warning_")}
            self.assertEqual(recorded, UNCOVERED_IDS, chief)

    def test_pathway_consultation_asks_none(self):
        for chief in ["咳嗽两天", "头痛"]:
            state, asked = self._drive(chief)
            asked_warnings = [q for q in asked if q and q.startswith("warning_")]
            self.assertEqual(asked_warnings, [], chief)
            self.assertFalse(any(key.startswith("warning_") for key in state.facts), chief)


class HandoffLabelTests(unittest.TestCase):
    def test_every_warning_fact_has_a_patient_facing_label(self):
        repo = ContentRepository()
        for question in repo.warning_questions:
            self.assertIn(question.fact_key, LABELS)
            self.assertNotEqual(LABELS[question.fact_key], question.fact_key)


if __name__ == "__main__":
    unittest.main()
