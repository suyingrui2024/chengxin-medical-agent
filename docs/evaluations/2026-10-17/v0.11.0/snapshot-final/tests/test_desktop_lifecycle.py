import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from time import monotonic

from medagent.backends import DshBackend
from medagent.events import EventStore


@unittest.skipUnless(os.name == "posix" and shutil.which("node"), "requires POSIX and Node")
class DesktopLifecycleTests(unittest.TestCase):
    def test_shutdown_interrupts_an_active_model_and_its_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge = root / "integrations/dsh/bridge.mjs"
            bridge.parent.mkdir(parents=True)
            package = root / "node_modules/@deepseek-ai/dsh-sdk-client/package.json"
            package.parent.mkdir(parents=True)
            package.write_text("{}")
            bridge.write_text('''
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";
const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
writeFileSync(new URL("./started.json", import.meta.url), JSON.stringify({ parent: process.pid, child: child.pid }));
setInterval(() => {}, 1000);
''')
            backend = DshBackend(EventStore(), root, {
                "MEDAGENT_LLM_BASE_URL": "http://127.0.0.1:1",
                "MEDAGENT_LLM_API_KEY": "fixture", "MEDAGENT_LLM_MODEL": "fixture",
            })
            failures = []

            def decide():
                try:
                    backend.decide("shutdown-fixture", [])
                except Exception as exc:
                    failures.append(exc)

            thread = threading.Thread(target=decide, daemon=True)
            thread.start()
            try:
                marker = bridge.parent / "started.json"
                deadline = monotonic() + 5
                while not marker.exists() and monotonic() < deadline:
                    threading.Event().wait(0.01)
                self.assertTrue(marker.exists(), "fixture did not start")
                pids = json.loads(marker.read_text())
                backend.close()
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive(), "active model call remained blocked")
                self.assertTrue(failures)
                for pid in pids.values():
                    status = Path(f"/proc/{pid}/status")

                    def stopped():
                        try:
                            state = status.read_text().split("State:")[1].strip()[0]
                            # Exited tasks can briefly be dead or waiting to be reaped.
                            return state in {"X", "Z"}
                        except (FileNotFoundError, ProcessLookupError):
                            return True

                    deadline = monotonic() + 1
                    while not stopped() and monotonic() < deadline:
                        threading.Event().wait(0.01)
                    self.assertTrue(stopped(), f"model process {pid} is still running")
            finally:
                backend.close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
