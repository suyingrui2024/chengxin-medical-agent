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
        # stdout is reserved for the parent control protocol.
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
        # Only the Electron parent owns this pipe. No renderer/HTTP endpoint can
        # nominate a directory. EOF also arrives when the parent crashes.
        while True:
            line = sys.stdin.readline(8192)
            if not line:
                break
            if not line.endswith("\n"):
                break
            request_id = None
            try:
                command = json.loads(line)
                if not isinstance(command, dict):
                    raise ValueError()
                request_id = command.get("request_id")
                if (
                    set(command) != {"type", "request_id", "directory"}
                    or command["type"] != "open_project"
                    or not isinstance(request_id, str)
                    or not 1 <= len(request_id) <= 64
                ):
                    raise ValueError()
                with server.workspace.lock:
                    settings = dict(server.workspace.settings)
                    registry = server.workspace.engine.content.registry
                snapshot = server.execution.attach_project(
                    command["directory"], settings, registry
                ).snapshot()
                result = {
                    "type": "project_opened",
                    "request_id": request_id,
                    "snapshot": snapshot,
                }
            except (ValueError, OSError):
                result = {
                    "type": "project_opened",
                    "request_id": request_id,
                    "error": "项目无法打开。请核对目录、模型配置及其他窗口的占用情况。",
                }
            print(json.dumps(result, ensure_ascii=False), flush=True)
        server.shutdown()

    print(json.dumps({"type": "ready", "port": server.server_port}), flush=True)
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
        runtime.stop()


if __name__ == "__main__":
    main()
