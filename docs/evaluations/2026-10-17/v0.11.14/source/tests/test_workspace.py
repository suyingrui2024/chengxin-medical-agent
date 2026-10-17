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
        self.assertTrue(self.workspace.probe({"backend": "demo"})["ok"])


if __name__ == "__main__":
    unittest.main()
