"""Regression: the advertised tool catalog must follow the coordinator
that production assembly injects AFTER engine construction.

The wave-2 trim resolved the spawn_subagent advertisement once in
AdaptiveConsultationEngine.__init__ from the constructor argument. But
no production path passes a coordinator to the constructor: the server
setattrs it onto the already-built startup engine (server.py), and
Workspace._attach_subagents setattrs it onto every engine a settings
apply composes. The frozen catalog therefore never advertised
spawn_subagent in the real product — advisory/shadow subagents were
unreachable — and it would equally have kept advertising the tool if
the coordinator were later detached. The engine now resolves the
advertised list at message-build time; these tests drive the two real
assembly paths and read the system message the model would actually
receive, so a construction-time freeze cannot pass them.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medagent.agent import AdaptiveConsultationEngine
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.subagents import SubagentCoordinator
from medagent.triage import TriageService
from medagent.workspace import Workspace
from tests.test_agent import ScriptBackend

COMPATIBLE_CONFIG = {
    "MEDAGENT_LLM_BASE_URL": "https://model.invalid/v1",
    "MEDAGENT_LLM_API_KEY": "test-secret",
    "MEDAGENT_LLM_MODEL": "test-model",
}
COMPATIBLE_PAYLOAD = {
    "backend": "compatible",
    "base_url": "https://model.invalid/v1",
    "model": "test-model",
    "api_key": "test-secret",
}


def advertised_tools(engine):
    """(tool names, system content) from the messages actually built
    for the backend — the catalog the model is offered, verbatim."""
    state, _reply = engine.create_session()
    messages = engine._messages(state, "你好")
    content = messages[0]["content"]
    catalog = json.loads(content.rsplit("\n", 1)[1])
    return [tool["name"] for tool in catalog["tools"]], content


def scrubbed_env():
    # Scrub only MEDAGENT_* configuration; keep the rest of the
    # environment (notably proxy settings) exactly as inherited.
    return patch.dict(
        os.environ,
        {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MEDAGENT_")
        },
        clear=True,
    )


class ServerStartupAttachTests(unittest.TestCase):
    """server.py path: runtime builds the engine coordinator-free, the
    server then attaches its coordinator with a plain setattr."""

    def setUp(self):
        self.env = scrubbed_env()
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runtime = build_runtime(
            backend="compatible",
            config=COMPATIBLE_CONFIG,
            session_dir=root / "sessions",
            event_path=root / "events.jsonl",
        )
        self.context = self.runtime.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")

    def tearDown(self):
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()

    def test_setattr_after_construction_controls_the_advertised_catalog(self):
        self.assertIsInstance(self.engine, AdaptiveConsultationEngine)
        self.assertIsNone(self.engine.subagent_coordinator)
        names, _content = advertised_tools(self.engine)
        self.assertNotIn("spawn_subagent", names)
        self.assertIn("record_facts", names)

        self.engine.subagent_coordinator = SubagentCoordinator(
            self.events, ScriptBackend(), mode="advisory"
        )
        names, _content = advertised_tools(self.engine)
        self.assertIn("spawn_subagent", names)

        self.engine.subagent_coordinator = SubagentCoordinator(
            self.events, ScriptBackend(), mode="shadow"
        )
        names, _content = advertised_tools(self.engine)
        self.assertIn("spawn_subagent", names)

        self.engine.subagent_coordinator = SubagentCoordinator(
            self.events, ScriptBackend(), mode="off"
        )
        names, _content = advertised_tools(self.engine)
        self.assertNotIn("spawn_subagent", names)

        self.engine.subagent_coordinator = None
        names, _content = advertised_tools(self.engine)
        self.assertNotIn("spawn_subagent", names)


class WorkspaceApplyAttachTests(unittest.TestCase):
    """Desktop/settings path: Workspace._attach_subagents setattrs the
    workspace's coordinator onto each engine a settings apply builds."""

    def setUp(self):
        self.env = scrubbed_env()
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.runtime = build_runtime(
            backend="demo",
            session_dir=root / "sessions",
            event_path=root / "events.jsonl",
        )
        self.context = self.runtime.start()
        self.events = self.context.services.require("events")
        self.workspace = Workspace(
            self.context.services.require("consultation"), self.events, "demo"
        )

    def tearDown(self):
        self.workspace.close()
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()

    def test_apply_with_advisory_coordinator_advertises_spawn(self):
        self.workspace.subagent_coordinator = SubagentCoordinator(
            self.events, ScriptBackend(), mode="advisory"
        )
        self.workspace.apply(COMPATIBLE_PAYLOAD)
        engine = self.workspace.engine
        self.assertIsInstance(engine, AdaptiveConsultationEngine)
        self.assertIs(engine.subagent_coordinator, self.workspace.subagent_coordinator)
        names, _content = advertised_tools(engine)
        self.assertIn("spawn_subagent", names)

    def test_apply_with_off_or_missing_coordinator_does_not_advertise(self):
        self.workspace.apply(COMPATIBLE_PAYLOAD)
        names, _content = advertised_tools(self.workspace.engine)
        self.assertNotIn("spawn_subagent", names)

        self.workspace.subagent_coordinator = SubagentCoordinator(
            self.events, ScriptBackend(), mode="off"
        )
        self.workspace.apply(COMPATIBLE_PAYLOAD)
        names, _content = advertised_tools(self.workspace.engine)
        self.assertNotIn("spawn_subagent", names)


class PrefixVariantStabilityTests(unittest.TestCase):
    """Lazy resolution must not destabilize the static prefix: each
    variant is byte-stable, and prefix_sha256 always names the variant
    the model is currently being sent (agent.started records it and
    offline evaluators read the attribute)."""

    def _engine(self):
        return AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=ScriptBackend(),
        )

    def test_variants_are_byte_stable_and_sha_tracks_the_live_one(self):
        engine = self._engine()
        names_a, content_a = advertised_tools(engine)
        self.assertNotIn("spawn_subagent", names_a)
        self.assertEqual(
            engine.prefix_sha256,
            hashlib.sha256(content_a.encode()).hexdigest(),
        )
        self.assertEqual(engine.system_prompt, content_a)

        coordinator = SubagentCoordinator(
            EventStore(), ScriptBackend(), mode="advisory"
        )
        engine.subagent_coordinator = coordinator
        names_b, content_b = advertised_tools(engine)
        self.assertIn("spawn_subagent", names_b)
        self.assertNotEqual(content_a, content_b)
        self.assertEqual(
            engine.prefix_sha256,
            hashlib.sha256(content_b.encode()).hexdigest(),
        )

        engine.subagent_coordinator = None
        _names, content_a2 = advertised_tools(engine)
        self.assertEqual(content_a2, content_a)

        engine.subagent_coordinator = coordinator
        _names, content_b2 = advertised_tools(engine)
        self.assertEqual(content_b2, content_b)


if __name__ == "__main__":
    unittest.main()
