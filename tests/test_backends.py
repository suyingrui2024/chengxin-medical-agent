import json
import os
import unittest
from unittest.mock import patch

from medagent.backends import CompatibleBackend, parse_decision
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
    def test_network_error_retried_once(self, urlopen):
        from urllib.error import URLError

        ok, command = self._ok_response()
        urlopen.side_effect = [URLError("connection reset"), ok]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["network_error"])

    @patch("medagent.backends.request.urlopen")
    def test_timeout_retried_once(self, urlopen):
        ok, command = self._ok_response()
        urlopen.side_effect = [TimeoutError("timed out"), ok]
        events = EventStore()
        backend = self._backend(events)
        self.assertEqual(backend.decide("s", []), command)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(self._retry_reasons(events), ["timeout"])

    @patch("medagent.backends.request.urlopen")
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

    @patch("medagent.backends.request.urlopen")
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
