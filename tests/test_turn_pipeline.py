"""Turn pipeline structure (design phase 2): handler registry order,
short-circuit tracing, planner swap seam, stale guard, phase graph."""
from __future__ import annotations

import unittest

from medagent.consultation import StaleActionError, UserInput
from medagent.domain import AgentReply, Phase
from medagent.phase_graph import can_transition, planning_rank
from medagent.runtime import build_runtime

EXPECTED_ORDER = [
    "location_revision",
    "closed_session",
    "subject_control",
    "prepared_location",
    "observation_action",
    "summary_review",
    "intake",
    "risk_escalation",
    "planning",
]


class _StubPlanner:
    name = "stub"

    def __init__(self):
        self.calls = 0

    def plan(self, state):
        self.calls += 1
        return AgentReply("（桩规划器回复）", state.phase, kind="question")


class PipelineStructureTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def test_pipeline_registry_order(self):
        names = [name for name, _ in self.engine._turn_pipeline]
        self.assertEqual(names, EXPECTED_ORDER)

    def test_normal_turn_reaches_planning_with_full_trace(self):
        ctx_holder = {}
        original = self.engine._begin_turn

        def capturing(state, user_input):
            ctx = original(state, user_input)
            ctx_holder["ctx"] = ctx
            return ctx

        self.engine._begin_turn = capturing
        reply = self.engine.submit(
            self.state.session_id, UserInput("我自己", "self")
        )
        self.assertEqual(reply.question_id, "sex")
        self.assertEqual(ctx_holder["ctx"].trace, EXPECTED_ORDER)

    def test_direct_emergency_bypasses_pipeline(self):
        self.engine.submit(self.state.session_id, UserInput("我自己", "self"))
        ctx_holder = {}
        original = self.engine._begin_turn

        def capturing(state, user_input):
            ctx = original(state, user_input)
            ctx_holder["ctx"] = ctx
            return ctx

        self.engine._begin_turn = capturing
        reply = self.engine.submit(
            self.state.session_id, UserInput("他现在叫不醒")
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(ctx_holder["ctx"].trace, [])

    def test_planner_is_swappable(self):
        stub = _StubPlanner()
        self.engine.planner = stub
        reply = self.engine.submit(
            self.state.session_id, UserInput("我自己", "self")
        )
        self.assertEqual(reply.message, "（桩规划器回复）")
        self.assertEqual(stub.calls, 1)

    def test_stale_action_guard_still_raises_and_logs(self):
        with self.assertRaises(StaleActionError):
            self.engine.submit(
                self.state.session_id,
                UserInput("随便", None, expected_action_id="bogus:999"),
            )
        kinds = [
            e.event_type for e in self.events.for_session(self.state.session_id)
        ]
        self.assertIn("turn.rejected", kinds)


class PhaseGraphTests(unittest.TestCase):
    def test_planning_rank_preserves_legacy_values(self):
        self.assertEqual(planning_rank(Phase.OPENING), 0)
        self.assertEqual(planning_rank(Phase.EMERGENCY_SCREEN), 1)
        self.assertEqual(planning_rank(Phase.HISTORY), 2)
        self.assertEqual(planning_rank(Phase.BACKGROUND), 3)
        self.assertEqual(planning_rank(Phase.OBSERVATION), 9)
        self.assertEqual(planning_rank(Phase.SUMMARY), 9)
        self.assertEqual(planning_rank(Phase.CLOSED), 9)

    def test_main_chain_and_safety_back_edges(self):
        self.assertTrue(can_transition(Phase.OPENING, Phase.EMERGENCY_SCREEN))
        self.assertTrue(can_transition(Phase.HISTORY, Phase.BACKGROUND))
        self.assertTrue(can_transition(Phase.BACKGROUND, Phase.HISTORY))
        self.assertTrue(can_transition(Phase.SUMMARY, Phase.EMERGENCY_SCREEN))
        self.assertTrue(can_transition(Phase.OBSERVATION, Phase.CLOSED))
        self.assertFalse(can_transition(Phase.CLOSED, Phase.HISTORY))


if __name__ == "__main__":
    unittest.main()
