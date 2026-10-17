"""Real official SDK on native Windows: commands, delegation, checks and resume."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_execution_delivery import Provider, tool, final
from medagent.execution import ExecutionManager


@unittest.skipUnless(os.name == "nt", "requires native Windows ProcessContainer")
class WindowsExecutionTests(unittest.TestCase):
    def test_native_code_delegation_delivery_and_same_session_continuation(self):
        root_calls, child_calls = [0], [0]

        def respond(_index, body):
            text = json.dumps(body.get("messages", []))
            if "WINDOWS_ROOT_FIXTURE" not in text:
                step = child_calls[0]
                child_calls[0] += 1
                return (
                    tool(
                        "bash",
                        command="python.exe -c \"from pathlib import Path; assert Path('result.txt').read_text(encoding='utf-8-sig').strip()=='1250'; Path('review.txt').write_text('verified')\"",
                        description="Verify output independently",
                    )
                    if step == 0
                    else final("Independent Windows verification complete")
                )
            step = root_calls[0]
            root_calls[0] += 1
            if step == 0:
                return tool(
                    "bash",
                    command="'print(1250)' | Set-Content -Encoding UTF8 calc.py; python.exe calc.py | Set-Content -Encoding UTF8 result.txt",
                    description="Write and run Python on Windows",
                )
            if step == 1:
                return tool(
                    "subagent",
                    description="Independent result verification",
                    prompt="WINDOWS_CHILD_VERIFY: independently check result.txt contains 1250, write review.txt with verified",
                    run_in_background=False,
                )
            if step == 2:
                return tool(
                    "delivery_check",
                    label="Verify actual Windows artifacts",
                    command="python.exe -c \"from pathlib import Path; assert Path('result.txt').read_text(encoding='utf-8-sig').strip()=='1250'; assert Path('review.txt').read_text()=='verified'\"",
                )
            if step == 4:
                return tool(
                    "bash",
                    command="if (-not (Test-Path review.txt)) { throw 'missing previous result' }; 'continued' | Set-Content -Encoding UTF8 notes.txt",
                    description="Continue the original Windows session",
                )
            return final("Native Windows task complete")

        provider = Provider(respond)
        with tempfile.TemporaryDirectory(prefix="chengxin-execution-") as raw:
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "native-windows-loopback-only",
                        "MEDAGENT_LLM_MODEL": "fixture",
                    }
                )

                def finish():
                    deadline = time.monotonic() + 90
                    while (
                        session.status not in {"completed", "failed", "cancelled"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    self.assertEqual(
                        session.status,
                        "completed",
                        json.dumps(
                            {"error": session.error, "events": list(session.events)},
                            ensure_ascii=False,
                        ),
                    )

                session.start_turn(
                    "WINDOWS_ROOT_FIXTURE: create and run calc.py, delegate verification, and run a delivery check",
                    12,
                )
                finish()
                self.assertEqual(
                    (session.directory / "review.txt").read_text(), "verified"
                )
                self.assertTrue(
                    any(
                        item["tool"] == "delivery_check" and item.get("exit_code") == 0
                        for item in session.observations
                    )
                )
                session.start_turn(
                    "WINDOWS_ROOT_FIXTURE FOLLOW_UP: retain the original artifacts and write notes.txt",
                    4,
                )
                finish()
                self.assertEqual(
                    (session.directory / "notes.txt")
                    .read_text(encoding="utf-8-sig")
                    .strip(),
                    "continued",
                )
                self.assertGreaterEqual(child_calls[0], 2)
                self.assertNotIn(
                    "native-windows-loopback-only", json.dumps(session.snapshot())
                )
            finally:
                manager.close()
                provider.shutdown()
                provider.server_close()


if __name__ == "__main__":
    unittest.main()
