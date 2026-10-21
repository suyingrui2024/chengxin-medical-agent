import json
import os
import unittest
from unittest.mock import patch

from medagent.runtime import build_runtime
from medagent.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.runtime = build_runtime(backend="demo")
        ctx = self.runtime.start()
        self.workspace = Workspace(ctx.services.require("consultation"), ctx.services.require("events"), "demo")

    def tearDown(self):
        self.workspace.close()
        self.runtime.stop()
        self.env.stop()

    def config(self, **overrides):
        return {"backend": "compatible", "base_url": "https://model.invalid/v1", "model": "test-model", "api_key": "test-secret", **overrides}

    def test_switch_keeps_existing_sessions_and_does_not_mutate_environment(self):
        old, _ = self.workspace.engine.create_session()
        self.workspace.apply(self.config())
        self.assertEqual(self.workspace.engine_for(old.session_id).state(old.session_id).session_id, old.session_id)
        self.assertNotIn("MEDAGENT_LLM_API_KEY", os.environ)
        self.assertNotIn("test-secret", json.dumps(self.workspace.public_settings()))
        self.assertTrue(self.workspace.public_settings()["has_api_key"])

    def test_invalid_configuration_leaves_previous_engine_in_place(self):
        engine = self.workspace.engine
        with self.assertRaises(ValueError):
            self.workspace.apply(self.config(base_url="file:///tmp/example"))
        self.assertIs(self.workspace.engine, engine)
        self.assertEqual(self.workspace.backend, "demo")

    def test_remote_http_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.workspace.apply(self.config(base_url="http://model.example/v1"))

    def test_changed_endpoint_cannot_silently_reuse_a_previous_key(self):
        self.workspace.apply(self.config())
        with self.assertRaises(ValueError):
            self.workspace.apply(self.config(base_url="https://other.invalid/v1", api_key=""))
        self.assertEqual(self.workspace.settings["MEDAGENT_LLM_BASE_URL"], "https://model.invalid/v1")

    def test_same_endpoint_can_keep_existing_key_without_reading_it_back(self):
        self.workspace.apply(self.config())
        result = self.workspace.apply(self.config(api_key="", model="another-model"))
        self.assertEqual(self.workspace.settings["MEDAGENT_LLM_API_KEY"], "test-secret")
        self.assertNotIn("test-secret", json.dumps(result))

    def test_history_is_read_from_real_sessions(self):
        self.assertEqual(self.workspace.sessions(), [])
        created, _ = self.workspace.engine.create_session()
        history = self.workspace.sessions()
        self.assertEqual(history[0]["session_id"], created.session_id)
        self.assertEqual(history[0]["fact_count"], 0)

    def test_demo_connection_check_does_not_invoke_a_model(self):
        result = self.workspace.probe({"backend": "demo"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["capabilities"]["consultation"]["status"], "offline")
        self.assertEqual(result["capabilities"]["vision"]["status"], "offline")

    def test_runtime_configuration_carries_a_separate_vision_model(self):
        result = self.workspace.apply(
            self.config(vision_model="deepseek-vision-fixture")
        )
        self.assertEqual(result["vision_model"], "deepseek-vision-fixture")
        self.assertNotIn("test-secret", json.dumps(result))

    def test_returning_to_demo_clears_the_current_process_configuration(self):
        self.workspace.apply(self.config(vision_model="vision-fixture"))
        result = self.workspace.apply({"backend": "demo"})
        self.assertFalse(result["has_api_key"])
        self.assertEqual(result["base_url"], "")
        self.assertEqual(result["model"], "")
        self.assertEqual(result["vision_model"], "")

    @patch("medagent.workspace.CompatibleBackend")
    def test_connection_probe_reports_text_dsh_and_vision_separately(self, backend):
        backend.return_value.decide.return_value = {
            "tool": "inspect_case",
            "arguments": {},
        }
        result = self.workspace.probe(
            self.config(vision_model="vision-fixture")
        )
        self.assertEqual(result["capabilities"]["consultation"]["status"], "verified")
        self.assertEqual(result["capabilities"]["dsh"]["status"], "not_selected")
        self.assertEqual(result["capabilities"]["vision"]["status"], "configured")
        messages = backend.return_value.decide.call_args.args[1]
        self.assertEqual(messages[1]["content"], "Connection test. No patient data.")


if __name__ == "__main__":
    unittest.main()
