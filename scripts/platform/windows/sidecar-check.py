"""Probe real desktop authentication/startup/shutdown without an external model."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import queue
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request


def main():
    with tempfile.TemporaryDirectory(prefix="chengxin-sidecar-") as scratch:
        env = {**os.environ, "MEDAGENT_EXECUTION_ROOT": scratch}
        env.pop("MEDAGENT_DESKTOP_LIVE", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-u",
                "-X",
                "utf8",
                "-c",
                "import sys;sys.path.insert(0,sys.argv[1]);from medagent.desktop_service import main;main()",
                str(Path(__file__).resolve().parents[3] / "src"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=env,
        )
        try:
            token = "a" * 64
            process.stdin.write(json.dumps({"token": token}) + "\n")
            process.stdin.flush()
            lines = queue.Queue()
            threading.Thread(
                target=lambda: lines.put(process.stdout.readline()), daemon=True
            ).start()
            ready = json.loads(lines.get(timeout=15))
            assert ready["type"] == "ready"
            url = f"http://127.0.0.1:{ready['port']}/api/health"
            # Bypass inherited HTTP proxies for loopback traffic.
            from urllib.request import build_opener, ProxyHandler

            opener = build_opener(ProxyHandler({}))
            try:
                opener.open(url, timeout=5)
            except HTTPError as error:
                assert error.code == 401
            else:
                raise AssertionError("Unauthenticated desktop service was accessible")
            with opener.open(
                Request(url, headers={"X-MedAgent-Token": token}), timeout=5
            ) as response:
                health = json.load(response)
            assert health["status"] == "ok" and health["backend"] == "demo"
            process.stdin.close()
            assert process.wait(timeout=10) == 0
            print(
                json.dumps(
                    {
                        "sidecar": "passed",
                        "unauthenticated_status": 401,
                        "backend": "demo",
                        "model_calls": 0,
                        "shutdown_exit": 0,
                    }
                )
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
