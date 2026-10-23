"""Session persistence: closed consultations (and their follow-up
plans) must survive a process restart; in-progress ones must not be
auto-restored. The engine boundary is fail-open."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medagent.consultation import UserInput
from medagent.runtime import build_runtime
from medagent.session_store import SessionStore

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


def _engine_with_store(root: Path):
    host = build_runtime(session_dir=root)
    context = host.start()
    return host, context.services.require("consultation")


def _drive_to_closed(engine):
    state, reply = engine.create_session()
    for _ in range(30):
        if reply.kind in {"summary", "emergency", "urgent", "closed"}:
            break
        if reply.kind == "self_check":
            reply = engine.submit(state.session_id, UserInput("暂时跳过", "skip"))
            continue
        text, choice = ANSWERS[reply.question_id]
        reply = engine.submit(state.session_id, UserInput(text, choice))
    assert reply.kind == "summary", reply.kind
    reply = engine.submit(state.session_id, UserInput("确认", "confirm_summary"))
    assert reply.kind == "closed", reply.kind
    return state


class SessionPersistenceTests(unittest.TestCase):
    def test_closed_session_and_follow_up_survive_restart_and_take_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            host, engine = _engine_with_store(root)
            try:
                state = _drive_to_closed(engine)
                session_id = state.session_id
                self.assertTrue(state.follow_up)
                self.assertTrue((root / f"{session_id}.json").exists())
            finally:
                host.stop()

            # A brand-new engine over the same store stands in for a
            # process restart.
            host2, engine2 = _engine_with_store(root)
            try:
                restored = engine2.state(session_id)
                self.assertEqual(restored.phase.value, "closed")
                self.assertTrue(restored.follow_up)
                self.assertEqual(restored.get("chief_complaint"), "腹痛两小时")
                self.assertTrue(restored.conclusion)
                # The longitudinal loop closes across the restart: the
                # due plan can still record an outcome, and worsened
                # spawns a linked re-triage session carrying the prior.
                result = engine2.record_follow_up_outcome(
                    session_id, "worsened", "又烧起来了"
                )
                self.assertTrue(result["requires_retriage"])
                new_state = engine2.state(result["new_session_id"])
                prior = new_state.runtime_snapshot["prior_case"]
                self.assertEqual(prior["chief_complaint"], "腹痛两小时")
                # The outcome itself was written back durably.
                store = SessionStore(root)
                reloaded = {s.session_id: s for s in store.load_closed()}
                self.assertEqual(
                    reloaded[session_id].follow_up["outcome"], "worsened"
                )
            finally:
                host2.stop()

    def test_in_progress_session_is_not_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            host, engine = _engine_with_store(root)
            try:
                state, _reply = engine.create_session()
                session_id = state.session_id
                self.assertTrue((root / f"{session_id}.json").exists())
            finally:
                host.stop()
            host2, engine2 = _engine_with_store(root)
            try:
                with self.assertRaises(KeyError):
                    engine2.state(session_id)
            finally:
                host2.stop()

    def test_corrupt_files_are_skipped_and_delete_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sessions"
            store = SessionStore(root)
            (root / "broken.json").write_text("{not json", encoding="utf-8")
            (root / "foreign.json").write_text(
                '{"schema": "other/v9", "state": {}}', encoding="utf-8"
            )
            self.assertEqual(store.load_closed(), [])
            self.assertEqual(store.skipped_corrupt, 2)
            host, engine = _engine_with_store(root)
            try:
                state = _drive_to_closed(engine)
                engine.delete_session(state.session_id)
                self.assertFalse((root / f"{state.session_id}.json").exists())
            finally:
                host.stop()


if __name__ == "__main__":
    unittest.main()


class DesktopRuntimeWiringTests(unittest.TestCase):
    def test_desktop_runtime_persists_sessions_and_events(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from medagent import desktop_service

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(
                desktop_service, "default_patient_data_root", lambda: root
            ):
                runtime = desktop_service._build_desktop_runtime("demo")
            context = runtime.start()
            engine = context.services.require("consultation")
            events = context.services.require("events")
            self.assertIsNotNone(engine.session_store)
            self.assertEqual(events._path, root / "events.jsonl")
