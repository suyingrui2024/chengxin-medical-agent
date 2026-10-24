"""P2 point-0 fixes (healthcheck 2026-10-24, section 3, point 0).

Pins the three point-0 items, each with the behaviour its own defect
text allows:

1. observations.py: the model-backed branches are gated by engine *type*
   (``_has_model_backend``), not by ``hasattr(self, "backend")``. A stray
   ``engine.backend = ...`` setattr on a deterministic engine must not
   divert it into the suspend / supplement-re-request branches.
2. handoff.py: under shadow the conclusion section keeps the
   ``directions`` key with value None (fixed v2 shape); every consumer
   judges by value, so the shape is accepted as-is and pinned here.
3. CaseState.follow_up: emergency close stores {} (the field default),
   never a plan; every in-repo reader judges by truthiness, and the
   serializations normalize the same absence to None. Pinned here for
   every reader site, not just the emergency case.
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import ConsultationEngine, UserInput
from medagent.events import EventStore
from medagent.handoff import build_handoff, handoff_lines
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.workspace import Workspace
from tests.test_handoff import _sheet, _state
from tests.test_repeated_observations import measured_case, repeat


class _StubBackend:
    name = "p2-point0-stub"

    def decide(self, identity, messages):
        raise AssertionError("the point-0 tests must not call a model")


def _demo_engine(original):
    """Deterministic engine sharing one adaptive case's sessions."""
    engine = ConsultationEngine(
        original.content,
        original.triage,
        original.self_checks,
        original.events,
        original.ranker,
    )
    for name in (
        "_sessions",
        "_session_locks",
        "_session_conditions",
        "_latest_replies",
    ):
        setattr(engine, name, getattr(original, name))
    return engine


class Point0BackendSwitchTests(unittest.TestCase):
    def test_type_check_ignores_a_setattr_backend_on_a_deterministic_engine(self):
        engine, _, _, _ = measured_case()
        demo = _demo_engine(engine)
        self.assertFalse(demo._has_model_backend())
        demo.backend = _StubBackend()
        self.assertFalse(demo._has_model_backend())
        self.assertTrue(engine._has_model_backend())
        self.assertIsInstance(engine, AdaptiveConsultationEngine)

    def test_repeat_suspend_branch_ignores_setattr_backend(self):
        # Adaptive control: ordinary (non-observation, non-cancel) text
        # during a repeat suspends the task conversation.
        engine, _, state, reply = measured_case()
        offered = repeat(engine, state, reply)
        self.assertTrue(state.observation_resume)
        self.assertEqual(state.active_task, "axillary_temperature")
        calls = []
        engine._suspend_task_conversation = lambda s, t: calls.append(t)
        result = engine._handle_observation_action(
            state, UserInput("昨晚用过药物甲"), "昨晚用过药物甲", "t-p2"
        )
        self.assertEqual(calls, ["t-p2"])
        self.assertIsNone(result)

        # Deterministic engine with a stray backend attribute: same
        # state, same text -- the suspend branch must NOT fire.
        engine2, _, state2, reply2 = measured_case()
        offered2 = repeat(engine2, state2, reply2)
        demo = _demo_engine(engine2)
        demo.backend = _StubBackend()
        calls2 = []
        demo._suspend_task_conversation = lambda s, t: calls2.append(t)
        result2 = demo._handle_observation_action(
            state2, UserInput("昨晚用过药物甲"), "昨晚用过药物甲", "t-p2"
        )
        self.assertEqual(calls2, [])
        self.assertIsNotNone(result2)
        self.assertEqual(result2.kind, "self_check")

    def test_supplement_rerequest_branch_ignores_setattr_backend(self):
        text = "重新测体温37.6℃，昨晚用过药物甲"
        # Adaptive control: a supplemented re-measurement without an
        # axillary-qualified reading is parked for model-side handling.
        engine, _, state, _ = measured_case(summary=True)
        self.assertFalse(state.active_task)
        result = engine._handle_observation_action(
            state, UserInput(text), text, "t-p2"
        )
        self.assertIsNone(result)
        self.assertEqual(len(state.pending_self_checks), 1)
        self.assertEqual(
            state.pending_self_checks[0]["task_id"], "axillary_temperature"
        )

        # Deterministic engine with a stray backend attribute: same
        # text goes straight to a repeat reply, nothing is parked.
        engine2, _, state2, _ = measured_case(summary=True)
        demo = _demo_engine(engine2)
        demo.backend = _StubBackend()
        result2 = demo._handle_observation_action(
            state2, UserInput(text), text, "t-p2"
        )
        self.assertEqual(state2.pending_self_checks, [])
        self.assertIsNotNone(result2)
        self.assertEqual(result2.kind, "self_check")
        self.assertEqual(state2.active_task, "axillary_temperature")


class Point0HandoffShapeTests(unittest.TestCase):
    def test_shadow_directions_key_present_but_none_and_consumers_read_value(self):
        handoff = build_handoff(
            _state(sheet=_sheet(directions_visible=False, department_visible=False))
        )
        conclusion = handoff["conclusion"]
        # Fixed v2 shape: the key stays, the value is the gate.
        self.assertIn("directions", conclusion)
        self.assertIsNone(conclusion["directions"])
        # Every consumer judges by value, so nothing renders.
        self.assertEqual(conclusion.get("directions") or [], [])
        self.assertFalse(
            any("普通感冒" in line for line in handoff_lines(handoff))
        )

    def test_visible_directions_still_flow_through(self):
        handoff = build_handoff(_state(sheet=_sheet()))
        directions = handoff["conclusion"]["directions"]
        self.assertEqual(directions[0]["patient_label"], "普通感冒")
        self.assertTrue(
            any("普通感冒" in line for line in handoff_lines(handoff))
        )


class Point0FollowUpContractTests(unittest.TestCase):
    def _emergency_case(self):
        host = build_runtime()
        context = host.start()
        self.addCleanup(host.stop)
        engine = context.services.require("consultation")
        events = context.services.require("events")
        state, _ = engine.create_session()
        engine.submit(state.session_id, UserInput("我自己", "self"))
        reply = engine.submit(state.session_id, UserInput("他现在叫不醒"))
        self.assertEqual(reply.kind, "emergency")
        return engine, events, state, reply

    def test_emergency_close_stores_empty_dict_and_every_reader_treats_it_as_absent(self):
        engine, events, state, reply = self._emergency_case()
        # Stored representation: the field default, not a plan.
        self.assertEqual(state.follow_up, {})
        # Reply side was already None.
        self.assertIsNone(reply.follow_up)
        # Scheduler: no plan to schedule, idempotent no-op.
        self.assertIsNone(engine._schedule_follow_up(state))
        self.assertEqual(state.follow_up, {})
        # Outcome recording: no plan means the guard raises.
        with self.assertRaises(ValueError):
            engine.record_follow_up_outcome(state.session_id, "recovered", "")
        # Serializations normalize the same absence to None.
        self.assertIsNone(state.to_dict()["follow_up"])
        handoff = build_handoff(state)
        self.assertIsNone(handoff["follow_up"])
        self.assertFalse(
            any(line.startswith("随访") for line in handoff_lines(handoff))
        )
        # Workspace list: the falsy guard excludes the emergency case.
        workspace = Workspace(engine, events, None)
        self.assertNotIn(
            state.session_id,
            [item["session_id"] for item in workspace.follow_ups()],
        )

    def test_scheduled_plan_still_flows_through_every_reader(self):
        engine, events, state, _ = self._emergency_case()
        plan = {
            "kind": "routine_checkin",
            "status": "scheduled",
            "due_in_hours": 48,
            "due_at": "2026-10-26T00:00:00+00:00",
            "reason": "例行回访",
        }
        state.follow_up = dict(plan)
        # Idempotent scheduler returns the stored plan, not a rebuild.
        self.assertEqual(engine._schedule_follow_up(state), plan)
        self.assertEqual(state.to_dict()["follow_up"], plan)
        self.assertEqual(build_handoff(state)["follow_up"], plan)
        workspace = Workspace(engine, events, None)
        listed = [
            item
            for item in workspace.follow_ups()
            if item["session_id"] == state.session_id
        ]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["plan"], plan)


if __name__ == "__main__":
    unittest.main()
