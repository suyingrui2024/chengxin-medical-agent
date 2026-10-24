import json
import os
import unittest
from unittest.mock import patch

from medagent.backends import (
    CompatibleBackend,
    DshBackend,
    dsh_usage,
    parse_decision,
    sanitize_dsh_events,
)
from medagent.events import EventStore
from medagent.runtime import build_runtime


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, _=None):
        return json.dumps(self.value).encode()


class BackendTests(unittest.TestCase):
    def test_decision_requires_exact_top_level_envelope(self):
        with self.assertRaises(ValueError):
            parse_decision(
                '{"tool":"inspect_case","arguments":{},"hidden_action":"shell"}'
            )
        with self.assertRaises(ValueError):
            parse_decision("[]")
        with self.assertRaises(ValueError):
            parse_decision("not json")

    @patch("medagent.backends._open_request")
    def test_compatible_transport_logs_actual_request_and_response(self, urlopen):
        command = {"tool": "ask_question", "arguments": {"question_id": "onset"}}
        urlopen.return_value = Response(
            {"choices": [{"message": {"content": json.dumps(command)}}]}
        )
        events = EventStore()
        with patch.dict(
            os.environ,
            {
                "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/v1",
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "fixture",
            },
        ):
            backend = CompatibleBackend(events)
            output = backend.decide("s", [{"role": "user", "content": "fixture-only"}])
        self.assertEqual(output, command)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, "https://fixture.invalid/v1/chat/completions")
        self.assertEqual(
            [e.event_type for e in events.for_session("s")],
            ["model.requested", "model.responded"],
        )
        self.assertNotIn(
            "secret-test-value",
            json.dumps([e.to_dict() for e in events.for_session("s")]),
        )

    @patch("medagent.backends._open_request")
    def test_official_deepseek_records_cache_usage_without_reasoning_or_keys(
        self, urlopen
    ):
        command = {"tool": "inspect_case", "arguments": {}}
        urlopen.return_value = Response(
            {
                "model": "fixture",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(command),
                            "reasoning_content": "private-thinking",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "prompt_cache_hit_tokens": 900,
                    "prompt_cache_miss_tokens": 100,
                    "completion_tokens": 20,
                    "untrusted": "secret-test-value",
                },
            }
        )
        events = EventStore()
        backend = CompatibleBackend(
            events,
            {
                "MEDAGENT_LLM_BASE_URL": "https://api.deepseek.com",
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "fixture",
            },
        )
        backend.decide("s", [{"role": "user", "content": "synthetic"}])
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(
            events.for_session("s")[-1].payload["usage"]["prompt_cache_hit_tokens"], 900
        )
        encoded = json.dumps([e.to_dict() for e in events.for_session("s")])
        self.assertNotIn("private-thinking", encoded)
        self.assertNotIn("secret-test-value", encoded)

    def test_missing_config_is_not_silently_demo_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                build_runtime(backend="dsh")
            with self.assertRaises(ValueError):
                build_runtime(backend="compatible")


if __name__ == "__main__":
    unittest.main()


class NewModelParameterTests(unittest.TestCase):
    """2026-10-23: V4-generation parameter surface (thinking effort,
    DSH bridge config alignment, empty-content retry)."""

    def _compatible(self, events, **extra):
        env = {
            "MEDAGENT_LLM_BASE_URL": "https://api.deepseek.com",
            "MEDAGENT_LLM_API_KEY": "secret-test-value",
            "MEDAGENT_LLM_MODEL": "deepseek-flash",
        }
        env.update(extra)
        with patch.dict(os.environ, env):
            return CompatibleBackend(events)

    @patch("medagent.backends._open_request")
    def test_reasoning_effort_sent_when_thinking_enabled(self, urlopen):
        command = {"tool": "inspect_case", "arguments": {}}
        urlopen.return_value = Response(
            {"choices": [{"message": {"content": json.dumps(command)}}]}
        )
        backend = self._compatible(
            EventStore(),
            MEDAGENT_LLM_THINKING="enabled",
            MEDAGENT_LLM_REASONING_EFFORT="high",
        )
        self.assertEqual(backend.decide("s", []), command)
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_reasoning_effort_validated(self):
        with self.assertRaises(ValueError):
            self._compatible(EventStore(), MEDAGENT_LLM_REASONING_EFFORT="ultra")

    @patch("medagent.backends._open_request")
    def test_empty_content_retried_once(self, urlopen):
        command = {"tool": "inspect_case", "arguments": {}}
        urlopen.side_effect = [
            Response({"choices": [{"message": {"content": ""}}]}),
            Response({"choices": [{"message": {"content": json.dumps(command)}}]}),
        ]
        events = EventStore()
        backend = self._compatible(events)
        self.assertEqual(backend.decide("s", []), command)
        types = [e.event_type for e in events.for_session("s")]
        self.assertEqual(types, ["model.requested", "model.retry", "model.responded"])

    def test_dsh_backend_carries_generation_settings(self):
        import tempfile
        from pathlib import Path

        from medagent.backends import DshBackend

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "integrations" / "dsh").mkdir(parents=True)
            (root / "integrations" / "dsh" / "bridge.mjs").write_text("")
            pkg = root / "node_modules" / "@deepseek-ai" / "dsh-sdk-client"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text("{}")
            backend = DshBackend(
                EventStore(),
                root,
                config={
                    "MEDAGENT_LLM_BASE_URL": "https://api.deepseek.com/anthropic",
                    "MEDAGENT_LLM_API_KEY": "secret-test-value",
                    "MEDAGENT_LLM_MODEL": "deepseek-flash",
                    "MEDAGENT_LLM_MAX_TOKENS": "4096",
                    "MEDAGENT_LLM_THINKING": "enabled",
                    "MEDAGENT_LLM_REASONING_EFFORT": "low",
                },
            )
            self.assertEqual(backend.max_tokens, 4096)
            self.assertEqual(backend.thinking, "enabled")
            self.assertEqual(backend.reasoning_effort, "low")


class BridgeGenerationMappingTests(unittest.TestCase):
    """The bridge maps the envelope to SDK options defensively."""

    def test_envelope_mapping_rules(self):
        # Mirrors bridge.mjs main(): enabled thinking defaults to 'high'
        # (the API's own default tier); disabled always means 'off'.
        def map_effort(thinking, effort):
            if thinking != "enabled":
                return "off"
            return effort if effort in {"low", "high", "max"} else "high"

        self.assertEqual(map_effort("disabled", "max"), "off")
        self.assertEqual(map_effort("enabled", None), "high")
        self.assertEqual(map_effort("enabled", "low"), "low")


class RequestEventMetadataTests(unittest.TestCase):
    """model.requested events carry request metadata only — never the
    prompt text (it used to persist the full messages every turn)."""

    MESSAGES = [
        {"role": "system", "content": "静态前缀-不应落盘"},
        {"role": "user", "content": "患者原话-不应落盘，发烧两天"},
    ]

    def _assert_metadata_only(self, events, backend_name, model):
        (requested,) = [
            e for e in events.for_session("s") if e.event_type == "model.requested"
        ]
        payload = requested.payload
        self.assertEqual(payload["backend"], backend_name)
        self.assertEqual(payload["model"], model)
        self.assertEqual(payload["message_count"], len(self.MESSAGES))
        self.assertEqual(
            payload["message_chars"], [len(m["content"]) for m in self.MESSAGES]
        )
        self.assertEqual(
            payload["total_chars"],
            sum(len(m["content"]) for m in self.MESSAGES),
        )
        canonical = json.dumps(
            self.MESSAGES, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        import hashlib

        self.assertEqual(
            payload["content_sha256"],
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn("messages", payload)
        self.assertNotIn("payload", payload)
        encoded = json.dumps([e.to_dict() for e in events.for_session("s")])
        self.assertNotIn("不应落盘", encoded)

    @patch("medagent.backends._open_request")
    def test_compatible_request_event_is_metadata_only(self, urlopen):
        command = {"tool": "inspect_case", "arguments": {}}
        urlopen.return_value = Response(
            {"choices": [{"message": {"content": json.dumps(command)}}]}
        )
        events = EventStore()
        backend = CompatibleBackend(
            events,
            {
                "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/v1",
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "fixture-model",
            },
        )
        backend.decide("s", self.MESSAGES)
        self._assert_metadata_only(events, "compatible", "fixture-model")

    def test_dsh_request_event_is_metadata_only(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch as mock_patch

        from medagent.backends import DshBackend

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "integrations" / "dsh").mkdir(parents=True)
            (root / "integrations" / "dsh" / "bridge.mjs").write_text("")
            pkg = root / "node_modules" / "@deepseek-ai" / "dsh-sdk-client"
            pkg.mkdir(parents=True)
            (pkg / "package.json").write_text("{}")
            events = EventStore()
            backend = DshBackend(
                events,
                root,
                config={
                    "MEDAGENT_LLM_BASE_URL": "https://api.deepseek.com/anthropic",
                    "MEDAGENT_LLM_API_KEY": "secret-test-value",
                    "MEDAGENT_LLM_MODEL": "deepseek-flash",
                },
            )
            command = {"tool": "inspect_case", "arguments": {}}
            with mock_patch.object(
                DshBackend, "_bridge_decision", return_value=command
            ):
                self.assertEqual(backend.decide("s", self.MESSAGES), command)
            self._assert_metadata_only(events, "dsh", "deepseek-flash")


class TransientRetryTests(unittest.TestCase):
    """One bounded retry on transient transport failures (HTTP 429/5xx,
    timeouts, connection errors); deterministic errors surface at once."""

    def _backend(self, events, **extra):
        config = {
            "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/v1",
            "MEDAGENT_LLM_API_KEY": "secret-test-value",
            "MEDAGENT_LLM_MODEL": "fixture",
            "MEDAGENT_LLM_RETRY_BACKOFF_SECONDS": "0",
        }
        config.update(extra)
        return CompatibleBackend(events, config)

    def _ok_response(self):
        command = {"tool": "inspect_case", "arguments": {}}
        return Response({"choices": [{"message": {"content": json.dumps(command)}}]}), command

    def _retry_reasons(self, events):
        return [
            e.payload["reason"]
            for e in events.for_session("s")
            if e.event_type == "model.retry"
        ]

    @patch("medagent.backends._open_request")
    def test_http_429_retried_once(self, urlopen):
        from urllib.error import HTTPError

        ok, command = self._ok_response()
        urlopen.side_effect = [
            HTTPError("https://fixture.invalid/v1", 429, "rate limited", {}, None),
            ok,
        ]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["http_429"])

    @patch("medagent.backends._open_request")
    def test_http_5xx_retried_once(self, urlopen):
        from urllib.error import HTTPError

        ok, command = self._ok_response()
        urlopen.side_effect = [
            HTTPError("https://fixture.invalid/v1", 503, "unavailable", {}, None),
            ok,
        ]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["http_5xx"])

    @patch("medagent.backends._open_request")
    def test_network_error_retried_once(self, urlopen):
        from urllib.error import URLError

        ok, command = self._ok_response()
        urlopen.side_effect = [URLError("connection reset"), ok]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["network_error"])

    @patch("medagent.backends._open_request")
    def test_timeout_retried_once(self, urlopen):
        ok, command = self._ok_response()
        urlopen.side_effect = [TimeoutError("timed out"), ok]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["timeout"])

    @patch("medagent.backends._open_request")
    def test_http_4xx_not_retried(self, urlopen):
        from urllib.error import HTTPError

        urlopen.side_effect = HTTPError(
            "https://fixture.invalid/v1", 400, "bad request", {}, None
        )
        events = EventStore()
        backend = self._backend(events)
        with self.assertRaises(HTTPError):
            backend.decide("s", [])
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(self._retry_reasons(events), [])

    @patch("medagent.backends._open_request")
    def test_transient_failure_bounded_at_two_attempts(self, urlopen):
        from urllib.error import HTTPError

        urlopen.side_effect = HTTPError(
            "https://fixture.invalid/v1", 500, "server error", {}, None
        )
        events = EventStore()
        backend = self._backend(events)
        with self.assertRaises(HTTPError):
            backend.decide("s", [])
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["http_5xx"])

    def test_backoff_default_and_validation(self):
        backend = self._backend(EventStore())
        self.assertEqual(backend.retry_backoff_seconds, 0.0)
        default = CompatibleBackend(
            EventStore(),
            {
                "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/v1",
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "fixture",
            },
        )
        self.assertEqual(default.retry_backoff_seconds, 0.5)
        with self.assertRaises(ValueError):
            self._backend(EventStore(), MEDAGENT_LLM_RETRY_BACKOFF_SECONDS="99")

class DshEnvelopeGuardTests(unittest.TestCase):
    """P1-F-D1: DSH envelope/events type guards.

    A bad envelope shape raises ValueError (the repo's validation
    idiom, not AttributeError); a bad *auxiliary* events field must
    not drag down an otherwise valid proposal — it is dropped and
    traced on the model.responded event instead.
    """

    COMMAND = {"tool": "ask_question", "arguments": {"question_id": "onset"}}
    GOOD_EVENT = {
        "type": "assistant/message",
        "data": {
            "usage": {
                "inputTokens": 100,
                "cacheReadTokens": 20,
                "cacheWriteTokens": 5,
                "outputTokens": 7,
            }
        },
    }

    class _FakeChild:
        def __init__(self, stdout):
            self._stdout = stdout
            self.returncode = 0

        def communicate(self, data=None, timeout=None):
            return (self._stdout, "")

    def _backend(self, events, tmp):
        from pathlib import Path

        root = Path(tmp)
        (root / "integrations" / "dsh").mkdir(parents=True)
        (root / "integrations" / "dsh" / "bridge.mjs").write_text("")
        pkg = root / "node_modules" / "@deepseek-ai" / "dsh-sdk-client"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text("{}")
        return DshBackend(
            events,
            root,
            config={
                "MEDAGENT_LLM_BASE_URL": "https://api.deepseek.com/anthropic",
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "deepseek-flash",
            },
        )

    def _run_bridge(self, backend, envelope_stdout):
        import tempfile

        with tempfile.TemporaryDirectory() as scratch, patch(
            "medagent.processes.popen",
            return_value=self._FakeChild(envelope_stdout),
        ), patch("medagent.processes.release", return_value=None):
            return backend._bridge_decision("s", [], None, 0.0, scratch)

    def _responded(self, events):
        (responded,) = [
            e for e in events.for_session("s") if e.event_type == "model.responded"
        ]
        return responded.payload

    def test_bad_envelope_shape_raises_value_error(self):
        import tempfile

        events = EventStore()
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(events, tmp)
            with self.assertRaisesRegex(ValueError, "must be an object"):
                self._run_bridge(backend, "[]")

    def test_bad_events_field_does_not_drag_valid_proposal(self):
        import tempfile

        events = EventStore()
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(events, tmp)
            stdout = json.dumps(
                {
                    "final_response": json.dumps(self.COMMAND),
                    "events": "oops",
                }
            )
            self.assertEqual(self._run_bridge(backend, stdout), self.COMMAND)
        payload = self._responded(events)
        self.assertEqual(payload["runtime_events"], [])
        self.assertEqual(payload["usage"], {})
        self.assertEqual(
            payload["dropped_runtime_events"],
            [{"index": None, "reason": "events_not_list", "type": "str"}],
        )

    def test_bad_events_elements_dropped_with_trace_and_usage_kept(self):
        import tempfile

        events = EventStore()
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(events, tmp)
            stdout = json.dumps(
                {
                    "final_response": json.dumps(self.COMMAND),
                    "events": [self.GOOD_EVENT, 123, {"type": "x", "data": "bad"}],
                }
            )
            self.assertEqual(self._run_bridge(backend, stdout), self.COMMAND)
        payload = self._responded(events)
        self.assertEqual(payload["runtime_events"], [self.GOOD_EVENT])
        self.assertEqual(payload["usage"]["prompt_tokens"], 125)
        self.assertEqual(payload["usage"]["completion_tokens"], 7)
        self.assertEqual(
            payload["dropped_runtime_events"],
            [
                {"index": 1, "reason": "event_not_object", "type": "int"},
                {"index": 2, "reason": "event_data_not_object", "type": "str"},
            ],
        )

    def test_normal_envelope_passes_without_drop_trace(self):
        import tempfile

        events = EventStore()
        with tempfile.TemporaryDirectory() as tmp:
            backend = self._backend(events, tmp)
            stdout = json.dumps(
                {
                    "final_response": json.dumps(self.COMMAND),
                    "events": [self.GOOD_EVENT],
                }
            )
            self.assertEqual(self._run_bridge(backend, stdout), self.COMMAND)
        payload = self._responded(events)
        self.assertEqual(payload["runtime_events"], [self.GOOD_EVENT])
        self.assertEqual(payload["usage"]["prompt_tokens"], 125)
        self.assertNotIn("dropped_runtime_events", payload)

    def test_dsh_usage_guards_shape_but_skips_bad_elements(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            dsh_usage("oops")
        usage = dsh_usage([123, self.GOOD_EVENT, {"type": "x", "data": "bad"}])
        self.assertEqual(usage["prompt_tokens"], 125)
        events, dropped = sanitize_dsh_events("oops")
        self.assertEqual(events, [])
        self.assertEqual(len(dropped), 1)


class TransportSecurityTests(unittest.TestCase):
    """P0-E-D1 (no redirect may carry the Bearer key) and P1-E-D2
    (no plaintext non-loopback endpoint). Redirect tests run against
    real local servers, not the mocked transport seam, so they prove
    the opener actually refuses to follow."""

    def _backend(self, base_url):
        return CompatibleBackend(
            EventStore(),
            {
                "MEDAGENT_LLM_BASE_URL": base_url,
                "MEDAGENT_LLM_API_KEY": "secret-test-value",
                "MEDAGENT_LLM_MODEL": "fixture",
                "MEDAGENT_LLM_RETRY_BACKOFF_SECONDS": "0",
            },
        )

    def _run_servers(self, origin_status, origin_location):
        """Origin server answers every POST with (status, Location);
        target server records any hit. Returns (origin, target, hits).
        origin_location may be a callable taking the target port, so a
        cross-origin Location can name the target before it exists."""

        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        hits = []

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                hits.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "auth": self.headers.get("Authorization"),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        class Origin(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(origin_status)
                if Origin.location is not None:
                    self.send_header("Location", Origin.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        if callable(origin_location):
            Origin.location = origin_location(target.server_port)
        else:
            Origin.location = origin_location
        origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        for server in (target, origin):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(target.shutdown)
        self.addCleanup(target.server_close)
        self.addCleanup(origin.shutdown)
        self.addCleanup(origin.server_close)
        return origin, target, hits

    def test_redirect_never_followed_and_key_never_leaves_origin(self):
        from urllib.error import HTTPError

        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                origin, target, hits = self._run_servers(
                    code,
                    lambda port: (
                        f"http://127.0.0.1:{port}/v1/chat/completions"
                    ),
                )
                events = EventStore()
                backend = CompatibleBackend(
                    events,
                    {
                        "MEDAGENT_LLM_BASE_URL": (
                            f"http://127.0.0.1:{origin.server_port}/v1"
                        ),
                        "MEDAGENT_LLM_API_KEY": "secret-test-value",
                        "MEDAGENT_LLM_MODEL": "fixture",
                        "MEDAGENT_LLM_RETRY_BACKOFF_SECONDS": "0",
                    },
                )
                with self.assertRaises(HTTPError) as caught:
                    backend.decide("s", [{"role": "user", "content": "x"}])
                self.assertEqual(caught.exception.code, code)
                # The redirect target is never contacted at all, so it
                # cannot receive the Authorization header — the
                # strongest form of "no cross-origin key forwarding".
                self.assertEqual(hits, [])
                self.assertEqual(
                    [e.event_type for e in events.for_session("s")],
                    ["model.requested"],
                )
                self.assertNotIn(
                    "secret-test-value",
                    json.dumps([e.to_dict() for e in events.for_session("s")]),
                )

    def test_same_origin_redirect_is_not_followed_either(self):
        """Chosen scheme is disable-all, not same-origin-follow: an API
        endpoint has no legitimate redirect, so even a same-host
        Location fails explicitly rather than being retried elsewhere."""
        from urllib.error import HTTPError

        origin, target, hits = self._run_servers(302, "/elsewhere")
        backend = self._backend(f"http://127.0.0.1:{origin.server_port}/v1")
        with self.assertRaises(HTTPError) as caught:
            backend.decide("s", [{"role": "user", "content": "x"}])
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual(hits, [])

    def test_plain_200_still_flows_through_the_no_redirect_opener(self):
        """Guards the wiring itself: the mocked tests cannot tell a
        broken opener from a working one, this real-server round trip can."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        command = {"tool": "inspect_case", "arguments": {}}
        seen_auth = []

        class Model(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                seen_auth.append(self.headers.get("Authorization"))
                body = json.dumps(
                    {"choices": [{"message": {"content": json.dumps(command)}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            backend = self._backend(f"http://127.0.0.1:{server.server_port}/v1")
            self.assertEqual(backend.decide("s", []), command)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen_auth, ["Bearer secret-test-value"])

    def test_http_non_loopback_construction_rejected(self):
        for url in (
            "http://provider.invalid/v1",
            "http://192.168.1.10/v1",
            "http://10.0.0.5:8080/v1",
            # 127.0.0.2 is loopback address space but NOT the pinned
            # stub host: fail closed rather than allow the whole /8.
            "http://127.0.0.2/v1",
            "provider.invalid/v1",
            "ftp://provider.invalid/v1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self._backend(url)

    def test_https_and_loopback_construction_allowed(self):
        for url in (
            "https://provider.invalid/v1",
            "https://api.deepseek.com",
            "http://127.0.0.1:9/v1",
            "http://localhost:9/v1",
            "http://[::1]:9/v1",
        ):
            with self.subTest(url=url):
                backend = self._backend(url)
                self.assertEqual(backend.base_url, url.rstrip("/"))

    def test_build_runtime_rejects_plaintext_endpoint_up_front(self):
        config = {
            "MEDAGENT_LLM_BASE_URL": "http://provider.invalid/v1",
            "MEDAGENT_LLM_API_KEY": "secret-test-value",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        with self.assertRaises(ValueError):
            build_runtime(backend="compatible", config=config)
        # https and loopback still build (no engine required here).
        build_runtime(
            backend="compatible",
            config={**config, "MEDAGENT_LLM_BASE_URL": "https://provider.invalid/v1"},
        )
        build_runtime(
            backend="compatible",
            config={**config, "MEDAGENT_LLM_BASE_URL": "http://127.0.0.1:9/v1"},
        )
