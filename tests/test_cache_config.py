from pathlib import Path
import tempfile
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.backends import dsh_usage
from medagent.consultation import UserInput
from medagent.env_config import read_env, for_backend
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class CaptureBackend:
    name = "test"

    def decide(self, sid, messages):
        self.messages = messages
        return {
            "tool": "ask_question",
            "arguments": {"question_id": "emergency_screen"},
        }


class CacheConfigTests(unittest.TestCase):
    def test_static_prefix_matches_across_patients_and_omits_patient_identifiers(self):
        content = ContentRepository()
        backend = CaptureBackend()
        engine = AdaptiveConsultationEngine(
            content=content,
            triage=TriageService(),
            self_checks=SelfCheckService(content.registry),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        first, reply = engine.create_session()
        engine.submit(
            first.session_id,
            UserInput("我自己32岁咳嗽两天", expected_action_id=reply.action_id),
        )
        a = backend.messages
        second, reply = engine.create_session()
        engine.submit(
            second.session_id, UserInput("给孩子咨询腹痛", expected_action_id=reply.action_id)
        )
        b = backend.messages
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertIn("record_facts", a[0]["content"])
        self.assertNotIn(first.session_id, a[0]["content"])
        self.assertNotIn("我自己32岁咳嗽两天", a[0]["content"])
        self.assertNotIn("fact_schema", a[1]["content"])

    def test_env_is_literal_and_never_executes_shell_substitutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                'MEDAGENT_LLM_BASE_URL=https://api.deepseek.com\nMEDAGENT_LLM_MODEL=fixture\nMEDAGENT_LLM_API_KEY="$(do-not-execute)`literal`"\n'
            )
            settings = read_env(path)
            self.assertEqual(
                settings["MEDAGENT_LLM_API_KEY"], "$(do-not-execute)`literal`"
            )
            self.assertEqual(
                for_backend(settings, "dsh")["MEDAGENT_LLM_BASE_URL"],
                "https://api.deepseek.com/anthropic",
            )
            self.assertEqual(
                settings["MEDAGENT_LLM_BASE_URL"], "https://api.deepseek.com"
            )
            self.assertEqual(settings["MEDAGENT_DSH_REVIEW_MODE"], "shadow")

    def test_env_rejects_an_unknown_background_review_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "MEDAGENT_LLM_BASE_URL=https://api.deepseek.com\n"
                "MEDAGENT_LLM_MODEL=fixture\n"
                "MEDAGENT_LLM_API_KEY=fixture-key\n"
                "MEDAGENT_DSH_REVIEW_MODE=unbounded\n"
            )
            with self.assertRaisesRegex(ValueError, "MEDAGENT_DSH_REVIEW_MODE"):
                read_env(path)

    def test_dsh_counts_disjoint_cache_tokens_without_double_counting(self):
        usage = dsh_usage(
            [
                {
                    "type": "assistant/message",
                    "data": {
                        "usage": {
                            "inputTokens": 100,
                            "cacheReadTokens": 900,
                            "cacheWriteTokens": 50,
                            "outputTokens": 10,
                        }
                    },
                },
                {"type": "unrelated", "data": {"usage": {"inputTokens": 99}}},
            ]
        )
        self.assertEqual(usage["prompt_tokens"], 1050)
        self.assertEqual(usage["prompt_cache_miss_tokens"], 150)
        self.assertEqual(usage["prompt_cache_hit_tokens"], 900)


if __name__ == "__main__":
    unittest.main()
