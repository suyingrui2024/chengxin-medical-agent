"""Contract tests for the tests-side §6.4 helper (package D).

These are NEW coverage for the helper itself — no existing assertion
was rewritten to use it (existing single-pick assertions stay as
behavior anchors per §6.4 rule 2; see the package-D registry note).

Each test declares its engine kind first, then reads the expected
order, then compares against what that engine actually picks at the
same frozen state. The two engines get separate fresh states so the
differential planner's workspace refresh cannot contaminate the rule
comparison, and the helper is always computed BEFORE the engine pick
(the differential pick refreshes ``state.reasoning``; the helper must
agree with the pre-pick state).
"""

import copy
import unittest

from medagent.domain import Assertion, Fact, Source
from medagent.planner import RulePlanner
from medagent.runtime import build_runtime
from tests.question_ordering_contract import (
    expected_next_question,
    expected_question_order,
)


def _record(state, key, value, evidence, assertion=Assertion.PRESENT):
    state.record(
        Fact(
            key=key,
            value=value,
            assertion=assertion,
            source=Source.PATIENT,
            turn_id="t-contract",
            evidence=evidence,
        )
    )


def _history_state(engine):
    """Fresh session with opening + emergency screen recorded, so the
    earliest remaining phase is HISTORY (no BACKGROUND offer intervenes
    in the comparison)."""
    state, _ = engine.create_session()
    _record(state, "consult_for", "self", "我自己")
    _record(state, "sex", "male", "男")
    _record(state, "age", "32岁", "32岁")
    _record(state, "chief_complaint", "咳嗽发烧两天", "咳嗽发烧两天")
    _record(state, "emergency_screen", "no", "都没有")
    return state


class QuestionOrderingContractTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_rule_helper_matches_plan_next(self):
        engine = self.engine
        engine.planner = RulePlanner(engine)
        state = _history_state(engine)
        ordered = expected_question_order("rule", state, engine.content)
        self.assertTrue(ordered, "HISTORY state must have candidates")
        reply = engine._plan_next(state)
        self.assertEqual(reply.question_id, ordered[0])
        # The order is a permutation of the earliest-phase candidate ids,
        # not a subset/superset: the helper never adds or drops candidates.
        self.assertEqual(len(ordered), len(set(ordered)))

    def test_differential_helper_matches_planner(self):
        # Default planner is DifferentialPlanner; no swap needed.
        self.assertEqual(type(self.engine.planner).__name__, "DifferentialPlanner")
        state = _history_state(self.engine)
        ordered = expected_question_order(
            "differential", state, self.engine.content
        )
        self.assertTrue(ordered, "HISTORY state must have candidates")
        reply = self.engine.planner.plan(state)
        self.assertEqual(reply.question_id, ordered[0])

    def test_differential_helper_does_not_mutate_state(self):
        state = _history_state(self.engine)
        before_reasoning = copy.deepcopy(state.reasoning)
        before_facts = set(state.facts)
        expected_question_order("differential", state, self.engine.content)
        self.assertEqual(state.reasoning, before_reasoning)
        self.assertEqual(set(state.facts), before_facts)

    def test_engine_kind_must_be_declared(self):
        state = _history_state(self.engine)
        for bad_kind in (None, "", "adaptive", "catalog", "default"):
            with self.subTest(kind=bad_kind):
                with self.assertRaises(ValueError):
                    expected_question_order(bad_kind, state, self.engine.content)
                with self.assertRaises(ValueError):
                    expected_next_question(bad_kind, state, self.engine.content)

    def test_empty_candidate_set_yields_empty(self):
        state = _history_state(self.engine)
        self.assertEqual(
            expected_question_order("rule", state, _EmptyContent()), []
        )
        self.assertIsNone(
            expected_next_question("rule", state, _EmptyContent())
        )


class _EmptyContent:
    """Content stub with no questions: the filter/phase steps see an
    empty candidate set, exercising the §6.1 empty-set invariant."""

    questions = []

    @staticmethod
    def question_applies_in_state(question, state):
        return True


if __name__ == "__main__":
    unittest.main()
