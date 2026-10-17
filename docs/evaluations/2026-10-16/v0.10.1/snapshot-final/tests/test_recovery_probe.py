"""The live recovery probe must alter a request limit, never a provider response."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_execution import BoundedGateway
from evaluate_recovery import FirstOutputLimitHandler


class RecoveryProbeTests(unittest.TestCase):
    def test_only_first_authenticated_limit_changes_and_responses_are_unchanged(self):
        observed = []
        provider_body = (
            b'event: message_delta\ndata: {"delta":{"stop_reason":"max_tokens"}}\n\n'
        )

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                observed.append(
                    json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(provider_body)))
                self.end_headers()
                self.wfile.write(provider_body)

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                gateway = BoundedGateway(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "probe-fixture",
                    },
                    3,
                    Path(raw) / "requests.json",
                )
                gateway.first_output_limit, gateway.limit_applied = 32, False
                gateway.RequestHandlerClass = FirstOutputLimitHandler
                threading.Thread(target=gateway.serve_forever, daemon=True).start()
                message = {
                    "model": "fixture",
                    "max_tokens": 8192,
                    "stream": True,
                    "messages": [{"role": "user", "content": "original task"}],
                }

                def send(token):
                    return request.urlopen(
                        request.Request(
                            f"http://127.0.0.1:{gateway.server_port}/anthropic/messages",
                            json.dumps(message).encode(),
                            {"Content-Type": "application/json", "x-api-key": token},
                        ),
                        timeout=5,
                    )

                try:
                    with self.assertRaises(error.HTTPError) as failure:
                        send("wrong")
                    self.assertEqual(failure.exception.code, 401)
                    self.assertFalse(gateway.limit_applied)
                    for _ in range(2):
                        with send(gateway.token) as response:
                            self.assertEqual(response.read(), provider_body)
                    self.assertEqual(observed[0], {**message, "max_tokens": 32})
                    self.assertEqual(observed[1], message)
                    self.assertEqual(gateway.metrics()["attempted_calls"], 2)
                finally:
                    gateway.shutdown()
                    gateway.server_close()
        finally:
            provider.shutdown()
            provider.server_close()
