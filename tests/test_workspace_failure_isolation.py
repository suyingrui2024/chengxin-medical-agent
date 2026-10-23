"""Failure isolation for workspace bookkeeping (supervisor follow-ups).

- _compose stops the replaced runtime host AFTER the new composition
  is installed; a throwing stop() used to propagate out of apply(),
  leaving backend/settings pointing at the old composition while the
  new engine already served — an inconsistent workspace. Teardown
  failure must now be recorded and the apply must succeed.
- _persist_follow_up_drafts only caught OSError; a coordinator draft
  carrying an unserializable value fails json.dumps with TypeError,
  which crashed the offline batch script (no outer catch there) and
  bypassed the fail-open contract. Any persist failure must now leave
  the in-memory copy authoritative and an event trace.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from medagent.runtime import build_runtime
from medagent.workspace import Workspace


class WorkspaceFailureIsolationTests(unittest.TestCase):
    def setUp(self):
        # Scrub only MEDAGENT_* configuration; keep the rest of the
        # environment (notably proxy settings) exactly as inherited.
        self.env = patch.dict(
            os.environ,
            {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("MEDAGENT_")
            },
            clear=True,
        )
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.session_dir = root / "sessions"
        self.runtime = build_runtime(
            backend="demo",
            session_dir=self.session_dir,
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

    def _workspace_events(self, event_type):
        return [
            event
            for event in self.events.for_session("workspace")
            if event.event_type == event_type
        ]

    def test_apply_survives_replaced_host_stop_failure(self):
        self.workspace.apply({"backend": "demo"})
        replaced = self.workspace._current_host
        self.assertIsNotNone(replaced)
        with patch.object(
            replaced, "stop", side_effect=RuntimeError("teardown boom")
        ):
            result = self.workspace.apply({"backend": "demo"})
        # The apply completed and the workspace describes the NEW
        # composition consistently: settings, live engine, host list.
        self.assertEqual(result["backend"], "demo")
        self.assertEqual(self.workspace.backend, "demo")
        self.assertIs(self.workspace.engine, self.workspace.engines[-1])
        self.assertEqual(len(self.workspace.hosts), 1)
        self.assertIsNot(self.workspace.hosts[0], replaced)
        failures = self._workspace_events("composition.host_stop_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["error_type"], "RuntimeError")

    def test_unserializable_draft_stays_in_memory_and_records_failure(self):
        result = {
            "session_id": "s-unserializable",
            "draft_message": "回访草稿",
            "blob": object(),  # json.dumps raises TypeError on this
        }
        stored = self.workspace.store_follow_up_drafts([result])
        self.assertEqual(stored, 1)
        # The store did not raise and the in-memory copy is readable.
        self.assertEqual(
            self.workspace.follow_up_drafts["s-unserializable"][
                "draft_message"
            ],
            "回访草稿",
        )
        # Nothing half-written reached disk.
        self.assertFalse((self.session_dir / "follow_up_drafts.json").exists())
        failures = self._workspace_events("followup.drafts_persist_failed")
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["error_type"], "TypeError")


if __name__ == "__main__":
    unittest.main()
