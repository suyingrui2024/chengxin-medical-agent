"""Exercise both actual SDK arms and reject protected-test tampering without paid calls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_public_test_sample import evaluate
from public_test_sample import inventory, ROOT


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual isolated SDK"
)
class PublicTestPairsTests(unittest.TestCase):
    def test_both_arms_use_independent_original_tests_and_report_tampering(self):
        calls = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body)
                step = len(calls)
                if step % 2:
                    content = {
                        "type": "tool_use",
                        "id": f"tool-{step}",
                        "name": "bash",
                        "input": {
                            "command": "python3 - <<'PY'\nfrom pathlib import Path\nPath('foo.py').write_text('def add(a,b):\\n    return a+b\\n')\nPath('tests/test_existing.py').write_text('raise RuntimeError(\"tampered test\")\\n')\nPY",
                            "description": "Fixture implementation and deliberate protected-test violation",
                        },
                    }
                else:
                    content = {"type": "text", "text": "Fixture completed"}
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"m-{step}",
                            "type": "message",
                            "role": "assistant",
                            "model": "fixture",
                            "content": [],
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
                            "stop_reason": "tool_use" if step % 2 else "end_turn"
                        },
                        "usage": {"output_tokens": 5},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
                    for event in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        wheel = (
            ROOT
            / ".runtime/public-test-sample/wheels/pytest_mock-3.11.1-py3-none-any.whl"
        )
        if not wheel.is_file():
            self.skipTest("Requires frozen pytest-mock wheel")
        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                source = root / "source"
                (source / "tests").mkdir(parents=True)
                (source / "foo.py").write_text("def add(a,b):\n    return a-b\n")
                (source / "tests/test_existing.py").write_text(
                    "def test_existing():\n    assert 1+1==2\n"
                )
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(source / ".benchmark_dependencies")
                environment = {
                    "prepared_source": str(source),
                    "source_sha256": inventory(source),
                }
                instance = {
                    "FAIL_TO_PASS": '["tests/test_hidden.py::test_add"]',
                    "PASS_TO_PASS": '["tests/test_existing.py::test_existing"]',
                    "test_patch": "diff --git a/tests/test_hidden.py b/tests/test_hidden.py\nnew file mode 100644\n--- /dev/null\n+++ b/tests/test_hidden.py\n@@ -0,0 +1,3 @@\n+from foo import add\n+def test_add():\n+    assert add(2,3)==5\n",
                }
                mapping = {
                    name: [name]
                    for key in ["FAIL_TO_PASS", "PASS_TO_PASS"]
                    for name in json.loads(instance[key])
                }
                settings = {
                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                    "MEDAGENT_LLM_API_KEY": "fixture-only",
                    "MEDAGENT_LLM_MODEL": "fixture",
                }
                for arm in ["product", "official-sdk"]:
                    with self.subTest(arm=arm):
                        result = evaluate(
                            arm,
                            instance,
                            environment,
                            mapping,
                            settings,
                            root / arm,
                            "Fix add; respect existing test files.",
                        )
                        self.assertTrue(result["runtime_completed"], result)
                        self.assertTrue(result["source_correct"], result)
                        self.assertFalse(
                            result["checks"]["upstream_tests_and_config_preserved"],
                            result,
                        )
                        self.assertFalse(result["source_and_contract_passed"], result)
                        self.assertFalse(result["end_to_end_completed"], result)
                        self.assertEqual(result["gateway"]["attempted_calls"], 2)
                self.assertNotIn("test_hidden", json.dumps(calls))
                self.assertEqual(inventory(source), environment["source_sha256"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
