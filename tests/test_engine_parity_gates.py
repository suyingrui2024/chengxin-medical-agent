"""Engine parity: fact gates, blocking semantics and age-prompt adaptation.

The adaptive engine gained fact-gate evaluation (``excludes_fact_equals``
/ ``age_between_when_known``), non-blocking close semantics and an
adult age prompt in the wave-2 work. The deterministic engine originally
had none of these: it asked pregnancy_status to a 32-year-old man and to
a 6-year-old girl, and presented the infant-months age wording to
adults. These tests pin the shared implementation
(``knowledge.question_fact_gates_pass`` / ``knowledge.ADULT_AGE_PROMPT``)
across both deterministic selection paths (DifferentialPlanner default
and RulePlanner) plus the close-gate behaviour for non-blocking
questions (sex, reported_temperature).
"""
from __future__ import annotations

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Assertion, Fact, Phase, Source
from medagent.knowledge import ADULT_AGE_PROMPT, question_fact_gates_pass
from medagent.planner import RulePlanner
from medagent.reasoning import DifferentialPlanner
from medagent.runtime import build_runtime

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("男", "male"),
    "age": ("32岁", None),
    "chief_complaint": ("肚子疼，右下腹疼", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("昨天开始", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("5分", None),
    "associated_symptoms": ("没有", None),
    "gi_details": ("右下腹隐痛，按压有点疼，没有呕吐腹泻", None),
    "urinary_details": ("小便正常，没有尿痛", None),
    "pregnancy_status": ("没有怀孕，月经没有推迟", "not_pregnant"),
    "medications_taken": ("没有", None),
    "allergies": ("没有", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想知道要不要去医院", None),
    "reported_temperature": ("没量过", None),
}


def drive(engine, state, reply, overrides, max_turns=40):
    """Run a scripted consultation; returns (asked_ids, age_prompt, reply)."""
    answers = dict(ANSWERS)
    answers.update(overrides)
    asked = []
    age_prompt = None
    for _ in range(max_turns):
        if reply.kind in {"summary", "emergency", "urgent", "closed"}:
            break
        if reply.kind == "self_check":
            reply = engine.submit(state.session_id, UserInput("暂时跳过", "skip"))
            continue
        asked.append(reply.question_id)
        if reply.question_id == "age":
            age_prompt = reply.message
        text, choice = answers[reply.question_id]
        reply = engine.submit(state.session_id, UserInput(text, choice))
    return asked, age_prompt, reply


class DeterministicFlowParityTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def run_case(self, overrides):
        state, reply = self.engine.create_session()
        asked, age_prompt, reply = drive(self.engine, state, reply, overrides)
        return state, asked, age_prompt, reply

    def test_adult_male_abdominal_never_asked_pregnancy_and_closes(self):
        state, asked, age_prompt, reply = self.run_case({})
        self.assertNotIn("pregnancy_status", asked)
        self.assertNotIn("pregnancy_status", state.facts)
        self.assertEqual(reply.kind, "summary")
        # The summary can be confirmed into a normal close.
        reply = self.engine.submit(
            state.session_id, UserInput("确认", "confirm_summary")
        )
        self.assertEqual(state.phase, Phase.CLOSED)

    def test_adult_male_abdominal_rule_planner_parity(self):
        self.engine.planner = RulePlanner(self.engine)
        state, asked, age_prompt, reply = self.run_case({})
        self.assertNotIn("pregnancy_status", asked)
        self.assertNotIn("pregnancy_status", state.facts)
        self.assertEqual(reply.kind, "summary")

    def test_child_girl_abdominal_never_asked_pregnancy(self):
        state, asked, age_prompt, reply = self.run_case(
            {
                "consult_for": ("替孩子", "child"),
                "sex": ("女", "female"),
                "age": ("6岁", None),
            }
        )
        self.assertNotIn("pregnancy_status", asked)
        self.assertNotIn("pregnancy_status", state.facts)
        self.assertEqual(reply.kind, "summary")

    def test_adult_female_abdominal_asked_pregnancy(self):
        # Guard against over-gating: a woman of childbearing age with an
        # abdominal complaint must still get the pregnancy screen.
        state, asked, age_prompt, reply = self.run_case(
            {"sex": ("女", "female"), "age": ("28岁", None)}
        )
        self.assertIn("pregnancy_status", asked)
        self.assertIn("pregnancy_status", state.facts)
        self.assertEqual(reply.kind, "summary")

    def test_age_prompt_adapted_for_adult_subject(self):
        state, asked, age_prompt, reply = self.run_case({})
        self.assertIsNotNone(age_prompt)
        self.assertNotIn("婴幼儿", age_prompt)
        self.assertEqual(age_prompt, ADULT_AGE_PROMPT)

    def test_age_prompt_keeps_months_wording_for_child_subject(self):
        state, asked, age_prompt, reply = self.run_case(
            {
                "consult_for": ("替孩子", "child"),
                "sex": ("女", "female"),
                "age": ("6岁", None),
            }
        )
        self.assertIsNotNone(age_prompt)
        self.assertIn("月龄", age_prompt)


def filled_state(engine, *, skip_fact_keys=(), chief="发烧两天"):
    """A state where every question fact is recorded except the skipped
    ones, so planner close behaviour can be observed directly."""
    state, _ = engine.create_session()
    values = {"chief_complaint": chief, "sex": "female", "age_years": 30}
    for question in engine.content.questions:
        if question.fact_key in skip_fact_keys:
            continue
        state.record(
            Fact(
                key=question.fact_key,
                value=values.get(question.fact_key, "已填"),
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t",
            )
        )
    # The fever chief complaint also makes the temperature self-check
    # task applicable; record its result so planning reaches the finish
    # instead of offering the task.
    state.record(
        Fact(
            key="temperature_c",
            value=37.8,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t",
        )
    )
    return state


class NonBlockingCloseGateTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_only_non_blocking_remaining_finishes_rule_path(self):
        state = filled_state(self.engine, skip_fact_keys={"reported_temperature"})
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "summary")

    def test_only_non_blocking_remaining_finishes_differential_path(self):
        state = filled_state(self.engine, skip_fact_keys={"reported_temperature"})
        reply = DifferentialPlanner(self.engine).plan(state)
        self.assertEqual(reply.kind, "summary")

    def test_non_blocking_still_asked_while_blocking_outstanding(self):
        # reported_temperature (history, non-blocking) must stay askable
        # when a blocking field (patient_goal, background) is still open:
        # phase order asks it first in both selection paths.
        state = filled_state(
            self.engine, skip_fact_keys={"reported_temperature", "patient_goal"}
        )
        reply = self.engine._plan_next(state)
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "reported_temperature")

        state = filled_state(
            self.engine, skip_fact_keys={"reported_temperature", "patient_goal"}
        )
        reply = DifferentialPlanner(self.engine).plan(state)
        self.assertEqual(reply.kind, "question")
        self.assertEqual(reply.question_id, "reported_temperature")


class SharedGateFunctionTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.pregnancy = self.engine.content.question("pregnancy_status")

    def tearDown(self):
        self.host.stop()

    @staticmethod
    def facts(**entries):
        return {
            key: Fact(
                key=key,
                value=value,
                assertion=assertion,
                source=Source.PATIENT,
                turn_id="t",
            )
            for key, (value, assertion) in entries.items()
        }

    def test_male_sex_excludes(self):
        facts = self.facts(sex=("male", Assertion.PRESENT))
        self.assertFalse(question_fact_gates_pass(self.pregnancy, facts))

    def test_age_outside_window_excludes(self):
        facts = self.facts(
            sex=("female", Assertion.PRESENT), age_years=(6, Assertion.PRESENT)
        )
        self.assertFalse(question_fact_gates_pass(self.pregnancy, facts))
        facts = self.facts(
            sex=("female", Assertion.PRESENT), age_years=(60, Assertion.PRESENT)
        )
        self.assertFalse(question_fact_gates_pass(self.pregnancy, facts))

    def test_childbearing_female_passes(self):
        facts = self.facts(
            sex=("female", Assertion.PRESENT), age_years=(28, Assertion.PRESENT)
        )
        self.assertTrue(question_fact_gates_pass(self.pregnancy, facts))

    def test_unknown_facts_keep_question_eligible(self):
        # Gate fires only on known facts: no facts at all, and an
        # UNKNOWN-assertion age, both keep the question eligible.
        self.assertTrue(question_fact_gates_pass(self.pregnancy, {}))
        facts = self.facts(age_years=(None, Assertion.UNKNOWN))
        self.assertTrue(question_fact_gates_pass(self.pregnancy, facts))

    def test_adaptive_engine_delegates_to_shared_gate(self):
        # The adaptive engine's _fact_gates_pass must agree with the
        # shared implementation (it is a thin delegation now).
        state, _ = self.engine.create_session()
        for key, (value, assertion) in {
            "sex": ("male", Assertion.PRESENT),
            "age_years": (32, Assertion.PRESENT),
        }.items():
            state.record(
                Fact(
                    key=key,
                    value=value,
                    assertion=assertion,
                    source=Source.PATIENT,
                    turn_id="t",
                )
            )
        self.assertFalse(
            AdaptiveConsultationEngine._fact_gates_pass(state, self.pregnancy)
        )
        self.assertEqual(
            AdaptiveConsultationEngine._fact_gates_pass(state, self.pregnancy),
            question_fact_gates_pass(self.pregnancy, state.facts),
        )

    def test_adult_age_prompt_text_is_shared(self):
        # agent.py keeps its class-level literal (edit scope); pin it to
        # the shared constant so the two engines cannot drift apart.
        self.assertEqual(
            AdaptiveConsultationEngine._ADULT_AGE_PROMPT, ADULT_AGE_PROMPT
        )


if __name__ == "__main__":
    unittest.main()
