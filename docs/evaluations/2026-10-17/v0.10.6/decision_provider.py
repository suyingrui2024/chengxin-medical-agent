"""Replay saved round-2 decisions with the final runtime and synthetic fault boundary."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def decisions(row):
    root = row["snapshot"]["id"]
    groups = {}
    seen = set()
    for envelope in row["snapshot"]["events"]:
        notification = envelope.get("notification", {})
        params = notification.get("params", {})
        event = params.get("event", {})
        if (
            notification.get("method") != "session.event"
            or params.get("sessionId") != root
        ):
            continue
        kind = event.get("type")
        identity = (kind, event.get("seq"), event.get("time"))
        if identity in seen or kind not in {"assistant/message", "tool/call"}:
            continue
        seen.add(identity)
        data = event["data"]
        group = groups.setdefault(
            (data["turn"], data["step"]), {"text": [], "tools": []}
        )
        if kind == "assistant/message":
            group["text"] += [
                value
                for value in data.get("message", {}).get("content", [])
                if value.get("type") == "text"
            ]
        else:
            arguments = data["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            group["tools"].append(
                {
                    "type": "tool_use",
                    "id": data["callId"],
                    "name": data["name"],
                    "input": arguments,
                }
            )
    return [group["text"] + group["tools"] for _, group in sorted(groups.items())]


class LocalProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, frames):
        self.frames, self.requests = frames, []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.requests.append(body)
                index = len(self.server.requests) - 1
                content = (
                    self.server.frames[index]
                    if index < len(self.server.frames)
                    else [{"type": "text", "text": "Unexpected replay request"}]
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"replay-{index}",
                            "model": "saved-decisions",
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        },
                    }
                ]
                for position, block in enumerate(content):
                    events.extend(
                        [
                            {
                                "type": "content_block_start",
                                "index": position,
                                "content_block": block,
                            },
                            {"type": "content_block_stop", "index": position},
                        ]
                    )
                events.extend(
                    [
                        {
                            "type": "message_delta",
                            "delta": {
                                "stop_reason": "tool_use"
                                if any(block["type"] == "tool_use" for block in content)
                                else "end_turn"
                            },
                            "usage": {"output_tokens": 10},
                        },
                        {"type": "message_stop"},
                    ]
                )
                data = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()
