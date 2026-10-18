"""Real SDK processes retain their launch deadline across another user turn."""

import os
import json
from pathlib import Path
import tempfile
import time
import unittest

from medagent.execution import ExecutionManager
from tests.test_execution_delivery import Provider, tool, final


def exercise_command_deadline(owner):
    native = os.name == "nt"
    old_seconds, old_sleep, new_sleep = (5, 8, 6) if native else (2, 4, 3)

    def command(seconds, path):
        if native:
            return (
                f"Start-Sleep -Seconds {seconds}; "
                f"'finished' | Set-Content -Encoding UTF8 {path}"
            )
        return f"sleep {seconds}; printf finished > {path}"

    for followup in [False, True]:
        with owner.subTest(independent_followup=followup), tempfile.TemporaryDirectory() as raw:
            responses = [
                tool("bash", command=command(old_sleep, "old-late.txt"),
                     run_in_background=True, description="Original short task deadline"),
                final("Original background command started."),
                tool("bash", command=command(new_sleep, "followup.txt"),
                     description="Independent follow-up outlives the first deadline", timeoutMs=30000),
                final("Independent follow-up completed."),
            ]
            provider = Provider(responses)
            manager = ExecutionManager(root=Path(raw))
            settings = {
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                "MEDAGENT_LLM_API_KEY": "command-deadline-loopback-only",
                "MEDAGENT_LLM_MODEL": "fixture",
            }

            def finish(session):
                limit = time.monotonic() + 30
                while session.status in {"starting", "running", "cancelling"} and time.monotonic() < limit:
                    time.sleep(0.05)
                owner.assertEqual(session.status, "completed", session.error)

            try:
                session = manager.create(settings)
                session.start_turn("Start the fixed synthetic background command.", 2, old_seconds)
                finish(session)
                original = session.turn_id
                process = session.process
                owner.assertTrue(any(job["status"] == "running" for job in session.jobs))
                observe_until = time.monotonic() + old_sleep + 1
                if followup:
                    session.start_turn(
                        "Run the independent follow-up command. Do not extend or restart the original job.",
                        4, 20,
                    )
                    finish(session)
                    owner.assertNotEqual(session.turn_id, original)
                    owner.assertIs(session.process, process)
                    owner.assertTrue(
                        (session.directory / "followup.txt").exists(),
                        json.dumps({"events": list(session.events)[-60:], "jobs": session.jobs}, ensure_ascii=False),
                    )
                    owner.assertEqual(
                        (session.directory / "followup.txt").read_text(encoding="utf-8-sig").strip(),
                        "finished",
                    )
                while time.monotonic() < observe_until:
                    time.sleep(0.05)
                owner.assertFalse((session.directory / "old-late.txt").exists())
                owner.assertEqual(len(provider.requests), 4 if followup else 2)
                owner.assertEqual(session.turns[0]["time_budget_seconds"], old_seconds)
                if followup:
                    owner.assertEqual(session.status, "completed", session.error)
                    owner.assertEqual(session.turns[-1]["time_budget_seconds"], 20)
            finally:
                manager.close()
                provider.shutdown()
                provider.server_close()


@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class CommandDeadlineTests(unittest.TestCase):
    def test_original_command_deadline_survives_an_independent_followup(self):
        exercise_command_deadline(self)
