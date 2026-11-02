"""Scripted model backends and request builders shared by the agent tests.

These used to live in `test_agent.py`, which turned that file into an undeclared
support module: 42 test files imported `fact`, `cmd` or `ScriptBackend` from it,
so editing the agent tests could break unrelated suites and the "helper" had no
home. They are fixtures, not tests, so they live here.

`fact` returns a *dict* (the wire shape), not a `Fact` dataclass — that is what
the tool-call payloads expect.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation.agent import AdaptiveConsultationEngine
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService

def fact(key, value, evidence, assertion="present", correction=False):
    return dict(
        key=key,
        value=value,
        assertion=assertion,
        evidence=evidence,
        correction=correction,
    )


def cmd(tool, **args):
    return dict(tool=tool, arguments=args)


class ScriptBackend:
    name = "scripted-test-only"

    def __init__(self):
        self.commands = []
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages.copy())
        if not self.commands:
            raise RuntimeError("No scripted command")
        return self.commands.pop(0)


def make_engine():
    backend = ScriptBackend()
    events = EventStore()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events,
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    return engine, backend, events


def record(engine, state, text, facts, turn="t-record"):
    return engine._execute(state, cmd("record_facts", facts=facts), text, turn)


def tool(name, **arguments):
    return {"type": "tool_use", "name": name, "input": arguments}


def final(text="本机确定模型结束当前步骤。"):
    return {"type": "text", "text": text}


class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, responses, json_reply=None):
        self.responses, self.requests = responses, []
        self.json_reply = json_reply

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.requests.append(body)
                if self.server.json_reply is not None:
                    reply = self.server.json_reply(body)
                    if reply is not None:
                        data = json.dumps(reply).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        try:
                            self.wfile.write(data)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                        return
                index = len(self.server.requests)
                content = dict(
                    self.server.responses(index, body)
                    if callable(self.server.responses)
                    else self.server.responses[index - 1]
                    if index <= len(self.server.responses)
                    else final("Unexpected additional model request")
                )
                if content["type"] == "tool_use":
                    content["id"] = f"delivery-{index}"
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"fixture-{index}",
                            "model": "fixture",
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": content,
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "tool_use"
                            if content["type"] == "tool_use"
                            else "end_turn"
                        },
                        "usage": {"output_tokens": 10},
                    },
                    {"type": "message_stop"},
                ]
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
