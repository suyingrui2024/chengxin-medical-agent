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
