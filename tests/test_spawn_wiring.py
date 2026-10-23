"""Wiring tests for the spawn_subagent branch of the adaptive engine.

Covers the mode gates and the second-opinion disagreement flag
(design §5.7.2): shadow runs surface only a placeholder, off/disabled
runs report their real status, and in advisory mode a blinded second
opinion whose structured tendency lands in a different risk group than
the case's current one is flagged for the main model.
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.domain import RiskLevel
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class _ScriptBackend:
    name = "scripted-test-only"

    def decide(self, session_id, messages):  # pragma: no cover - unused
        raise RuntimeError("no scripted command")


class _FakeCoordinator:
    def __init__(self, mode, result):
        self.mode = mode
        self.result = result
        self.calls = []

    def run_spawn(self, state, proposal, *, session_id, cancelled=None):
        self.calls.append((session_id, proposal))
        return self.result


def _spawn_result(tendency):
    return {
        "ok": True,
        "spawn_id": "spawn-1",
        "mode": "advisory",
        "task_kind": "second_opinion",
        "branches": [
            {
                "subagent_id": "sub-1",
                "branch_id": "b1",
                "task_kind": "second_opinion",
                "status": "completed",
                "conclusion": "独立复核意见",
                "findings": [],
                "disposition_tendency": tendency,
            }
        ],
        "usage_total": {"total_tokens": 10},
    }


SPAWN_COMMAND = {
    "tool": "spawn_subagent",
    "arguments": {
        "task_kind": "second_opinion",
        "objective": "独立复核",
        "branches": [{"branch_id": "b1", "objective": "复核", "scope": {}}],
    },
}


class SpawnWiringTests(unittest.TestCase):
    def setUp(self):
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=_ScriptBackend(),
        )
        self.state, _ = self.engine.create_session()
        self.state.risk.level = RiskLevel.ROUTINE

    def _execute(self):
        return self.engine._execute(
            self.state, SPAWN_COMMAND, "复核一下", "turn-1"
        )

    def test_advisory_disagreement_is_flagged(self):
        self.engine.subagent_coordinator = _FakeCoordinator(
            "advisory", _spawn_result("urgent_today")
        )
        result = self._execute()
        self.assertTrue(result["ok"])
        self.assertTrue(result["spawn"]["second_opinion_disagreement"])
        self.assertEqual(
            result["spawn"]["second_opinion_tendency"], "urgent_today"
        )

    def test_advisory_agreement_is_not_flagged(self):
        self.engine.subagent_coordinator = _FakeCoordinator(
            "advisory", _spawn_result("routine_visit")
        )
        result = self._execute()
        self.assertTrue(result["ok"])
        self.assertNotIn("second_opinion_disagreement", result["spawn"])

    def test_shadow_returns_placeholder_without_subagent_content(self):
        self.engine.subagent_coordinator = _FakeCoordinator(
            "shadow", _spawn_result("urgent_today")
        )
        result = self._execute()
        self.assertEqual(
            result,
            {
                "ok": True,
                "spawn": {
                    "status": "shadow_recorded",
                    "spawn_id": "spawn-1",
                },
            },
        )

    def test_off_mode_reports_real_status(self):
        self.engine.subagent_coordinator = _FakeCoordinator(
            "off", {"ok": False, "status": "disabled"}
        )
        result = self._execute()
        self.assertEqual(result, {"ok": False, "status": "disabled"})

    def test_second_spawn_in_same_round_is_refused(self):
        fake = _FakeCoordinator("advisory", _spawn_result("routine_visit"))
        self.engine.subagent_coordinator = fake
        self.assertTrue(self._execute()["ok"])
        refused = self._execute()
        self.assertFalse(refused["ok"])
        self.assertEqual(len(fake.calls), 1)

    def test_no_coordinator_is_unavailable(self):
        result = self._execute()
        self.assertEqual(result, {"ok": False, "status": "unavailable"})


if __name__ == "__main__":
    unittest.main()
