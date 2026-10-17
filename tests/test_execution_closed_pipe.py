"""Inspecting a failed worker must retain its diagnostic during pipe shutdown."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from medagent.execution import ExecutionManager
from medagent.processes import popen, terminate, release


class ClosedPipeTests(unittest.TestCase):
    def test_snapshot_during_closed_stdin_retains_startup_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            child = popen(
                [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
                        "MEDAGENT_LLM_MODEL": "fixture",
                        "MEDAGENT_LLM_API_KEY": "fixture-only",
                    }
                )
                session.process = child
                session.status = "failed"
                session.error = "original SDK startup failure"
                session.ready.set()
                child.stdin.close()
                self.assertIsNone(child.poll())
                snapshot = session.snapshot()
                self.assertEqual(snapshot["status"], "failed")
                self.assertEqual(snapshot["error"], "original SDK startup failure")
            finally:
                terminate(child)
                child.wait(timeout=10)
                release(child)
                manager.close()


if __name__ == "__main__":
    unittest.main()
