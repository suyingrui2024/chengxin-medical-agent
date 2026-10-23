"""Workspace recomposition must not drop the startup composition's
persistence wiring (audit: changing settings in the desktop app built a
fresh engine with session_store=None, so new sessions silently stopped
reaching disk; replacement runtime hosts also accumulated unstopped).

These tests compose the workspace the way the desktop sidecar does —
an initial runtime with an explicit session_dir and event_path — then
apply settings and verify the replacement engines keep persisting.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Phase
from medagent.runtime import build_runtime
from medagent.workspace import Workspace

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("女", "female"),
    "pregnancy_status": ("没有怀孕，月经没有推迟", "not_pregnant"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "gi_details": ("肚脐周围隐痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}


def close_session(engine):
    state, reply = engine.create_session()
    for _ in range(25):
        if reply.kind == "summary":
            break
        if reply.kind == "self_check":
            reply = engine.submit(state.session_id, UserInput("暂时跳过", "skip"))
            continue
        text, choice = ANSWERS[reply.question_id]
        reply = engine.submit(state.session_id, UserInput(text, choice))
    assert reply.kind == "summary", reply.kind
    engine.submit(state.session_id, UserInput("确认", "confirm_summary"))
    return state


class WorkspaceCompositionTests(unittest.TestCase):
    def setUp(self):
        # Scrub only MEDAGENT_* configuration; keep the rest of the
        # environment (notably proxy settings) exactly as the process
        # inherited it.
        scrubbed = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("MEDAGENT_")
        }
        self.env = patch.dict(os.environ, scrubbed, clear=True)
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session_dir = root / "sessions"
        self.event_path = root / "events.jsonl"
        self.runtime = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
            event_path=self.event_path,
        )
        self.context = self.runtime.start()
        self.workspace = Workspace(
            self.context.services.require("consultation"),
            self.context.services.require("events"),
            "demo",
        )

    def tearDown(self):
        self.workspace.close()
        self.runtime.stop()
        self.tmp.cleanup()
        self.env.stop()

    def test_startup_persistence_coordinates_are_captured(self):
        self.assertEqual(self.workspace.session_dir, self.session_dir)
        self.assertEqual(self.workspace.event_path, self.event_path)

    def test_apply_keeps_session_store_on_the_new_engine(self):
        self.workspace.apply({"backend": "demo"})
        engine = self.workspace.engine
        self.assertIsNotNone(engine.session_store)
        self.assertEqual(engine.session_store.root, self.session_dir)
        self.assertEqual(
            self.workspace.public_settings()["storage"], "session_store"
        )

    def test_session_closed_after_apply_survives_restart(self):
        self.workspace.apply({"backend": "demo"})
        state = close_session(self.workspace.engine)
        self.assertEqual(state.phase, Phase.CLOSED)
        self.assertTrue((self.session_dir / f"{state.session_id}.json").exists())

        fresh = build_runtime(backend="demo", session_dir=self.session_dir)
        context = fresh.start()
        try:
            restored = context.services.require("consultation").state(
                state.session_id
            )
            self.assertEqual(restored.phase, Phase.CLOSED)
        finally:
            fresh.stop()

    def test_replaced_hosts_are_stopped_and_removed(self):
        self.workspace.apply({"backend": "demo"})
        first_host = self.workspace.hosts[0]
        first_context = first_host.context
        self.workspace.apply({"backend": "demo"})
        self.workspace.apply({"backend": "demo"})
        self.assertEqual(len(self.workspace.hosts), 1)
        self.assertIsNot(self.workspace.hosts[0], first_host)
        # stop() unregisters the replaced host's services; its engine is
        # still reachable, but the composition itself is torn down.
        with self.assertRaises(RuntimeError):
            first_context.services.require("consultation")
        # Engines are retained on purpose: earlier sessions stay reachable.
        self.assertEqual(len(self.workspace.engines), 4)

    def test_compose_attaches_subagent_coordinator(self):
        sentinel = object()
        self.workspace.subagent_coordinator = sentinel
        self.workspace.apply(
            {
                "backend": "compatible",
                "base_url": "https://model.invalid/v1",
                "model": "test-model",
                "api_key": "test-secret",
            }
        )
        engine = self.workspace.engine
        self.assertIsInstance(engine, AdaptiveConsultationEngine)
        self.assertIs(engine.subagent_coordinator, sentinel)

    def test_public_settings_report_subagent_web(self):
        with patch.dict(os.environ, {"MEDAGENT_SUBAGENT_WEB": "on"}):
            self.assertTrue(self.workspace.public_settings()["subagent_web"])
        with patch.dict(os.environ, {"MEDAGENT_SUBAGENT_WEB": "off"}):
            self.assertFalse(self.workspace.public_settings()["subagent_web"])
        self.assertFalse(self.workspace.public_settings()["subagent_web"])

    def test_session_creation_log_is_bounded(self):
        for index in range(140):
            self.workspace.remember_session_creation(
                f"req-{index}", {"session_id": f"s-{index}"}
            )
        self.assertIsNone(self.workspace.session_creation("req-0"))
        self.assertIsNotNone(self.workspace.session_creation("req-139"))
        self.assertIsNone(self.workspace.session_creation(""))


if __name__ == "__main__":
    unittest.main()
