"""Private desktop sidecar. Parent supplies credentials over stdin, never argv."""
from __future__ import annotations

import json
import sys
import threading
import os

from .runtime import build_runtime
from .server import Handler, MedicalAgentServer


class DesktopHandler(Handler):
    def log_message(self, format, *args):
        # stdout is reserved for the one readiness message.
        return


def main():
    message = json.loads(sys.stdin.readline(8192))
    token = message.get("token")
    if not isinstance(token, str) or len(token) != 64:
        raise ValueError("Invalid desktop startup handshake")
    mode = (
        os.environ.get("MEDAGENT_BACKEND", "compatible")
        if os.environ.get("MEDAGENT_DESKTOP_LIVE") == "1"
        else "demo"
    )
    if mode not in {"demo", "compatible", "dsh"}:
        raise ValueError("Invalid desktop backend")
    runtime = build_runtime(backend=mode)
    context = runtime.start()
    server = MedicalAgentServer(
        ("127.0.0.1", 0),
        DesktopHandler,
        context.services.require("consultation"),
        context.services.require("events"),
        mode,
        access_token=token,
    )

    def watch_parent():
        # EOF also arrives when the Electron parent crashes.
        while sys.stdin.buffer.read(1024):
            pass
        server.shutdown()

    threading.Thread(target=watch_parent, daemon=True).start()
    print(json.dumps({"type": "ready", "port": server.server_port}), flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
