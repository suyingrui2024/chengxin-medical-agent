"""Priority inputs must not wait for model I/O or accept its late proposal."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import CompatibleBackend, DshBackend
from medagent.consultation import UserInput, ConsultationBusyError
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.server import Handler, MedicalAgentServer
from medagent.decision_wait import DecisionWaiter


def wait_for(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Expected bounded state transition did not occur")


def late_proposal():
    return {
        "tool": "record_facts",
        "arguments": {
            "facts": [
                {
                    "key": "age_years",
                    "value": 99,
                    "assertion": "present",
                    "evidence": "99岁",
                }
            ]
        },
    }


def engine_for(backend, events=None):
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=events or EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )


class DelayedBackend:
    name = "local-delayed-fixture"

    def __init__(self, fail=False):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.fail = fail
        self.calls = 0

    def decide(self, session_id, messages):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("Fixture was not released")
        if self.fail:
            raise RuntimeError("Synthetic late failure")
        return late_proposal()


class InterruptTests(unittest.TestCase):
    def exercise(self, text, choice=None, correction=False, fail=False):
        backend = DelayedBackend(fail)
        engine = engine_for(backend)
        state, opening = engine.create_session()
        replies = []
        thread = threading.Thread(
            target=lambda: replies.append(
                engine.submit(state.session_id, UserInput("我99岁，咳嗽"))
            )
        )
        thread.start()
        try:
            self.assertTrue(backend.entered.wait(2))
            with self.assertRaises(ConsultationBusyError):
                engine.submit(state.session_id, UserInput("普通补充"))
            with self.assertRaises(ConsultationBusyError):
                engine.correct_fact(state.session_id, "age_years", "40岁", "obsolete")
            started = time.monotonic()
            reply = (
                engine.correct_fact(state.session_id, "invalid", text, "obsolete")
                if correction
                else engine.submit(
                    state.session_id, UserInput(text, choice, "obsolete")
                )
            )
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(reply.kind, "emergency")
            thread.join(1)
            self.assertFalse(
                thread.is_alive(),
                "Original HTTP/engine wait must also unblock before provider release",
            )
            self.assertEqual(replies[0].action_id, reply.action_id)
            self.assertEqual(replies[0].kind, "emergency")
            self.assertNotIn("age_years", state.facts)
            self.assertNotIn("emergency_screen", state.facts)
            self.assertIsNone(state.pending_input)
            revision = state.revision
            backend.release.set()
            wait_for(
                lambda: any(
                    e.event_type == "agent.decision_discarded"
                    for e in engine.events.for_session(state.session_id)
                )
            )
            self.assertEqual(state.revision, revision)
            self.assertNotIn("age_years", state.facts)
            self.assertEqual(backend.calls, 1)
            self.assertNotIn("普通补充", [entry["content"] for entry in state.transcript])
            self.assertEqual(
                len(
                    [
                        e
                        for e in engine.events.for_session(state.session_id)
                        if e.event_type == "agent.decision_discarded"
                    ]
                ),
                1,
            )
        finally:
            backend.release.set()
            thread.join(2)

    def test_raw_emergency_preempts_and_late_valid_facts_are_discarded(self):
        self.exercise("现在严重呼吸困难")

    def test_late_failure_cannot_replace_emergency_with_retry(self):
        self.exercise("现在严重呼吸困难", fail=True)

    def test_priority_correction_precedes_field_and_version_checks(self):
        self.exercise("现在叫不醒", correction=True)

    def test_explicit_help_records_request_origin_without_inventing_symptoms(self):
        self.exercise("请先显示求助信息", "request_emergency_help")

    def test_repeated_help_request_is_idempotent(self):
        engine = engine_for(DelayedBackend())
        state, _ = engine.create_session()
        first = engine.submit(
            state.session_id, UserInput("我需要帮助", "request_emergency_help")
        )
        count = len(engine.events.for_session(state.session_id))
        second = engine.submit(
            state.session_id, UserInput("我需要帮助", "request_emergency_help")
        )
        self.assertEqual(first.action_id, second.action_id)
        self.assertEqual(len(engine.events.for_session(state.session_id)), count)

    def test_new_information_in_another_help_request_is_retained(self):
        engine = engine_for(DelayedBackend())
        state, _ = engine.create_session()
        engine.submit(state.session_id, UserInput("我需要帮助", "request_emergency_help"))
        engine.submit(state.session_id, UserInput("身边现在有人协助", "request_emergency_help"))
        self.assertIn("身边现在有人协助", [item["content"] for item in state.transcript])
        self.assertFalse(state.facts)

    def test_facts_committed_before_the_wait_survive_but_late_corrections_do_not(self):
        backend = DelayedBackend()
        original_decide = backend.decide

        def decide(session_id, messages):
            if not backend.calls:
                backend.calls += 1
                return {
                    "tool": "record_facts",
                    "arguments": {
                        "facts": [
                            {
                                "key": "age_years",
                                "value": 32,
                                "assertion": "present",
                                "evidence": "32岁",
                            }
                        ]
                    },
                }
            value = original_decide(session_id, messages)
            value["arguments"]["facts"][0]["correction"] = True
            return value

        backend.decide = decide
        engine = engine_for(backend)
        state, _ = engine.create_session()
        thread = threading.Thread(
            target=lambda: engine.submit(state.session_id, UserInput("我32岁，家人99岁"))
        )
        thread.start()
        try:
            self.assertTrue(backend.entered.wait(2))
            self.assertEqual(state.get("age_years"), 32)
            engine.submit(state.session_id, UserInput("现在严重呼吸困难"))
            backend.release.set()
            thread.join(2)
            wait_for(
                lambda: any(
                    e.event_type == "agent.decision_discarded"
                    for e in engine.events.for_session(state.session_id)
                )
            )
            self.assertEqual(state.get("age_years"), 32)
            self.assertEqual(backend.calls, 2)
        finally:
            backend.release.set()
            thread.join(2)

    def test_worker_capacity_is_bounded_without_blocking_priority_or_other_sessions(
        self,
    ):
        backend = DelayedBackend()
        engine = engine_for(backend)
        engine._decision_waiter = DecisionWaiter(maximum=1)
        first, _ = engine.create_session()
        second, _ = engine.create_session()
        thread = threading.Thread(
            target=lambda: engine.submit(first.session_id, UserInput("我99岁"))
        )
        thread.start()
        try:
            self.assertTrue(backend.entered.wait(2))
            reply = engine.submit(second.session_id, UserInput("另一个会话的描述"))
            self.assertEqual(reply.kind, "service_error")
            self.assertEqual(second.pending_input, "另一个会话的描述")
            self.assertEqual(backend.calls, 1)
            reply = engine.submit(first.session_id, UserInput("现在不能呼吸"))
            self.assertEqual(reply.kind, "emergency")
            self.assertNotEqual(second.risk.level.value, "emergency")
            backend.release.set()
            wait_for(
                lambda: any(
                    e.event_type == "agent.decision_discarded"
                    for e in engine.events.for_session(first.session_id)
                )
            )
        finally:
            backend.release.set()
            thread.join(2)


class HttpInterruptTests(unittest.TestCase):
    def exercise(self, mode):
        entered, release = threading.Event(), threading.Event()
        requests = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                requests.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                entered.set()
                release.wait(8)
                if mode == "compatible":
                    data = json.dumps(
                        {
                            "choices": [
                                {"message": {"content": json.dumps(late_proposal())}}
                            ]
                        }
                    ).encode()
                    mime = "application/json"
                else:
                    events = [
                        {
                            "type": "message_start",
                            "message": {
                                "id": "interrupt-fixture",
                                "model": "fixture",
                                "usage": {"input_tokens": 10, "output_tokens": 1},
                            },
                        },
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "text_delta",
                                "text": json.dumps(late_proposal()),
                            },
                        },
                        {"type": "content_block_stop", "index": 0},
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 10},
                        },
                        {"type": "message_stop"},
                    ]
                    data = "".join(
                        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
                    ).encode()
                    mime = "text/event-stream"
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        events = EventStore()
        settings = {
            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic"
            if mode == "dsh"
            else f"http://127.0.0.1:{provider.server_port}/v1",
            "MEDAGENT_LLM_API_KEY": "interrupt-local-fixture",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        backend = (
            DshBackend(events, Path(__file__).resolve().parents[1], settings)
            if mode == "dsh"
            else CompatibleBackend(events, settings)
        )
        engine = engine_for(backend, events)
        state, opening = engine.create_session()
        server = MedicalAgentServer(("127.0.0.1", 0), Handler, engine, events, mode)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        root = f"http://127.0.0.1:{server.server_port}"

        def message(payload):
            return json.load(
                urlopen(
                    Request(
                        root + f"/api/sessions/{state.session_id}/messages",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=5,
                )
            )

        replies = []
        errors = []

        def ordinary():
            try:
                replies.append(
                    message(
                        {"text": "我99岁，咳嗽", "expected_action_id": opening.action_id}
                    )
                )
            except Exception as error:
                errors.append(type(error).__name__)

        thread = threading.Thread(target=ordinary)
        temporary_roots = []

        def owned_temporary(*args, **kwargs):
            value = TemporaryDirectory(*args, **kwargs)
            temporary_roots.append(Path(value.name))
            return value

        tracker = patch(
            "medagent.backends.TemporaryDirectory", side_effect=owned_temporary
        )
        tracker.start()
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            if mode == "dsh":
                self.assertEqual(len(temporary_roots), 1)
                self.assertTrue(
                    any(
                        p.name.startswith("medical-dsh-")
                        for p in temporary_roots[0].iterdir()
                    )
                )
            # A coherent snapshot and a duplicate rejection must also avoid the model wait.
            snapshot = json.load(
                urlopen(root + f"/api/sessions/{state.session_id}/snapshot", timeout=1)
            )
            self.assertEqual(snapshot["state"]["revision"], 1)
            with self.assertRaises(HTTPError) as duplicate:
                message({"text": "普通补充"})
            self.assertEqual(duplicate.exception.code, 409)
            self.assertEqual(
                json.load(duplicate.exception)["error"]["code"], "consultation_busy"
            )
            started = time.monotonic()
            urgent = message({"text": "现在严重呼吸困难", "expected_action_id": "obsolete"})
            self.assertLess(time.monotonic() - started, 0.75)
            self.assertEqual(urgent["reply"]["kind"], "emergency")
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertFalse(errors, errors)
            self.assertEqual(
                replies[0]["reply"]["action_id"], urgent["reply"]["action_id"]
            )
            self.assertEqual(replies[0]["state"], urgent["state"])
            release.set()
            wait_for(
                lambda: any(
                    e.event_type == "agent.decision_discarded"
                    for e in events.for_session(state.session_id)
                )
            )
            if mode == "dsh":
                wait_for(lambda: not backend._children)
                wait_for(lambda: all(not p.exists() for p in temporary_roots))
            self.assertEqual(len(requests), 1)
            self.assertNotIn("age_years", state.facts)
            self.assertEqual(state.closed_reason, "emergency_route")
        finally:
            release.set()
            thread.join(5)
            server.shutdown()
            server.server_close()
            if hasattr(backend, "close"):
                backend.close()
            provider.shutdown()
            provider.server_close()
            tracker.stop()

    def test_compatible_http_snapshot_and_priority_input_preempt_socket_wait(self):
        self.exercise("compatible")

    @unittest.skipUnless(
        os.environ.get("RUN_DSH_INTEGRATION") == "1", "requires actual DSH subprocess"
    )
    def test_real_dsh_http_priority_input_cancels_bridge_and_discards_proposal(self):
        self.exercise("dsh")
