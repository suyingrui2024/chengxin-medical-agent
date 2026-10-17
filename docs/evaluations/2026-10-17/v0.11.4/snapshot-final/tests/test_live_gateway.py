import http.client
import importlib.util
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import MagicMock, patch


@unittest.skipUnless(os.environ.get("RUN_DSH_INTEGRATION") == "1", "local HTTP fixture")
class LiveGatewayTests(unittest.TestCase):
    def test_gateway_auth_budget_failure_injection_and_credential_redaction(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "live_gateway", root / "scripts/test_live_desktop.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        settings = {
            "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
            "MEDAGENT_LLM_API_KEY": "upstream-test-only-secret",
        }
        gateway = module.EvaluationGateway(settings, 1, "temporary-local-token")
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()

        def call(path, token="temporary-local-token"):
            client = http.client.HTTPConnection(
                "127.0.0.1", gateway.server_port, timeout=3
            )
            client.request(
                "POST",
                path,
                b"{}",
                {"x-api-key": token, "Content-Type": "application/json"},
            )
            response = client.getresponse()
            status, body = response.status, response.read()
            client.close()
            return status, body

        try:
            self.assertEqual(call("/anthropic/v1/messages", "wrong")[0], 401)
            self.assertEqual(call("/another-host/messages")[0], 404)
            self.assertEqual(call("/__evaluation/fail-next")[0], 200)
            self.assertEqual(call("/anthropic/v1/messages")[0], 503)
            self.assertEqual(gateway.metrics()["attempted_calls"], 0)
            upstream = MagicMock()
            upstream.__enter__.return_value = upstream
            upstream.read.return_value = b'{"echo":"upstream-test-only-secret"}'
            upstream.status = 200
            upstream.headers = {"Content-Type": "application/json"}
            with patch.object(
                module.request, "urlopen", return_value=upstream
            ) as forward:
                status, body = call("/anthropic/v1/messages")
                self.assertEqual(status, 200)
                self.assertNotIn(b"upstream-test-only-secret", body)
                self.assertEqual(json.loads(body)["echo"], "[REDACTED]")
                self.assertEqual(
                    forward.call_args.args[0].get_header("X-api-key"),
                    "upstream-test-only-secret",
                )
                self.assertEqual(call("/anthropic/v1/messages")[0], 429)
                self.assertEqual(forward.call_count, 1)
            self.assertEqual(gateway.metrics()["injected_failures"], 1)
            self.assertNotIn("upstream-test-only-secret", json.dumps(gateway.metrics()))
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
