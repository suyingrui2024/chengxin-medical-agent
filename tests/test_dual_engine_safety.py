"""Dual-engine safety differential (design phases 2-3): the two engines
may differ in dialogue strategy, but never in safety outcome. Every
red-flag corpus text must route to emergency on BOTH engines as a first
message, and no negative control may route to emergency on either."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

FIXTURES = Path(__file__).parent / "fixtures"


class _NullBackend:
    name = "null-test-backend"

    def decide(self, session_id, messages):
        raise RuntimeError("model must not be consulted for this test")


def corpus_samples():
    samples = []
    for name in ("red_flag_corpus_v0.json", "red_flag_probe_v1.json"):
        data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        samples.extend(data["samples"])
    return samples


class DualEngineSafetyTests(unittest.TestCase):
    def _deterministic_reply(self, text):
        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        try:
            state, _ = engine.create_session()
            return engine.submit(state.session_id, UserInput(text)).kind
        finally:
            host.stop()

    def _adaptive_reply(self, text):
        engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=_NullBackend(),
        )
        state, _ = engine.create_session()
        return engine.submit(state.session_id, UserInput(text)).kind

    def test_emergency_texts_route_to_emergency_on_both_engines(self):
        for sample in corpus_samples():
            if not sample["expect_trigger"]:
                continue
            with self.subTest(sample=sample["id"]):
                self.assertEqual(
                    self._deterministic_reply(sample["text"]), "emergency"
                )
                self.assertEqual(
                    self._adaptive_reply(sample["text"]), "emergency"
                )

    def test_negative_controls_never_route_to_emergency(self):
        for sample in corpus_samples():
            if sample["expect_trigger"]:
                continue
            with self.subTest(sample=sample["id"]):
                self.assertNotEqual(
                    self._deterministic_reply(sample["text"]), "emergency"
                )
                self.assertNotEqual(
                    self._adaptive_reply(sample["text"]), "emergency"
                )


if __name__ == "__main__":
    unittest.main()
