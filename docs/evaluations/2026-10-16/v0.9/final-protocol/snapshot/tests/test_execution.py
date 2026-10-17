import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from medagent.execution import (
    ExecutionManager,
    ExecutionError,
    ExecutionBusy,
    safe_file,
)


TOKEN_LIMIT_PROBE = """
import glob,json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
runtime_env=None
for entry in glob.glob('/proc/[0-9]*/environ'):
 try: values=dict(part.split(b'=',1) for part in open(entry,'rb').read().split(b'\\0') if b'=' in part)
 except OSError: continue
 if b'DEEPSEEK_API_KEY' in values and b'DEEPSEEK_BASE_URL' in values:
  runtime_env=values;break
assert runtime_env is not None
request=Request(runtime_env[b'DEEPSEEK_BASE_URL'].decode()+'/messages',data=json.dumps({'model':'deepseek-flash','max_tokens':999999,'messages':[]}).encode(),headers={'Content-Type':'application/json','x-api-key':runtime_env[b'DEEPSEEK_API_KEY'].decode()})
try:urlopen(request,timeout=5)
except HTTPError as error:assert error.code==400,error.code
else:raise AssertionError('gateway allowed excessive output tokens')
Path('token-limit-check.txt').write_text('blocked')
"""


class WorkspaceFileTests(unittest.TestCase):
    def test_artifact_listing_never_follows_directory_or_file_links(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw) / "owned")
            session = manager.create(
                {
                    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
                    "MEDAGENT_LLM_API_KEY": "fixture",
                    "MEDAGENT_LLM_MODEL": "fixture",
                }
            )
            outside = Path(raw) / "outside"
            outside.mkdir()
            (outside / "private.txt").write_text("not an artifact")
            (session.directory / "link-dir").symlink_to(
                outside, target_is_directory=True
            )
            (session.directory / "link-file").symlink_to(outside / "private.txt")
            (session.directory / "actual.py").write_text("print(1)")
            self.assertEqual(
                [file["path"] for file in session.artifacts()], ["actual.py"]
            )
            manager.close()

    def test_text_files_cannot_escape_through_paths_symlinks_or_special_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "report.txt").write_text("实际结果：1250")
            self.assertEqual(safe_file(root, "report.txt")["text"], "实际结果：1250")
            (root / "escape").symlink_to("/etc/passwd")
            (root / "outside").symlink_to("/etc", target_is_directory=True)
            os.mkfifo(root / "pipe")
            for path in [
                "../.env",
                "/etc/passwd",
                "escape",
                "outside/passwd",
                "pipe",
                "./report.txt",
                "report.txt/../report.txt",
            ]:
                with self.subTest(path=path), self.assertRaises(ExecutionError):
                    safe_file(root, path)

    def test_creating_workspace_requires_model_config_but_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            with self.assertRaises(ExecutionError):
                manager.create({})
            session = manager.create(
                {
                    "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
                    "MEDAGENT_LLM_API_KEY": "fixture-only",
                    "MEDAGENT_LLM_MODEL": "fixture",
                }
            )
            self.assertIsNone(session.process)
            self.assertEqual(session.snapshot()["status"], "created")
            manager.close()
            with self.assertRaises(ExecutionError):
                session.start_turn("不可在已关闭工作区运行")


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1",
    "set RUN_DSH_EXECUTION=1 for the real isolated full DSH runtime",
)
class FullDshExecutionTests(unittest.TestCase):
    def test_unauthed_development_server_cannot_expose_execution(self):
        from medagent.runtime import build_runtime
        from medagent.server import Handler, MedicalAgentServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError

        runtime = build_runtime(backend="demo")
        context = runtime.start()
        server = MedicalAgentServer(
            ("127.0.0.1", 0),
            Handler,
            context.services.require("consultation"),
            context.services.require("events"),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for method in ["GET", "POST"]:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/api/execution",
                    method=method,
                    data=b"{}" if method == "POST" else None,
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request)
                self.assertEqual(error.exception.code, 403)
            self.assertFalse(server.execution.sessions)
        finally:
            server.shutdown()
            server.server_close()
            runtime.stop()

    def test_budget_failures_are_not_reported_as_success_and_auxiliary_search_shares_budget(
        self,
    ):
        observed = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(body)
                if any(
                    tool.get("type", "").startswith("web_search_")
                    for tool in body.get("tools", [])
                ):
                    data = json.dumps(
                        {
                            "content": [
                                {
                                    "type": "web_search_tool_result",
                                    "content": [
                                        {
                                            "type": "web_search_result",
                                            "url": "https://example.org/reference",
                                            "title": "Reference source",
                                            "page_age": "2026-10-16",
                                        }
                                    ],
                                },
                                {
                                    "type": "text",
                                    "text": "untrusted provider prose",
                                    "citations": [
                                        {
                                            "url": "https://example.org/reference",
                                            "cited_text": "Source excerpt",
                                        }
                                    ],
                                },
                            ]
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                tool = {
                    "name": "web_search",
                    "input": {"queries": ["reference source"]},
                }
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "budget-model",
                            "type": "message",
                            "role": "assistant",
                            "model": "deepseek-flash",
                            "content": [],
                            "usage": {"input_tokens": 20, "output_tokens": 1},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "search-call",
                            "name": tool["name"],
                            "input": {},
                        },
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(tool["input"]),
                        },
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use"},
                        "usage": {"output_tokens": 20},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    "event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n"
                    for item in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                manager = ExecutionManager(root=Path(raw))
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "budget-fixture-only",
                        "MEDAGENT_LLM_MODEL": "deepseek-flash",
                    }
                )
                try:
                    session.start_turn("Search the reference and report its URL.", 2)
                    snapshot = self.wait_terminal(session)
                    self.assertEqual(snapshot["status"], "failed")
                    self.assertEqual(snapshot["error"], "model_call_budget_exhausted")
                    self.assertEqual(len(observed), 2)
                    self.assertIn("web_search_", json.dumps(observed[1]))
                    self.assertIn("https://example.org/reference", json.dumps(snapshot))
                    self.assertNotIn("untrusted provider prose", json.dumps(snapshot))
                finally:
                    manager.close()
        finally:
            model.shutdown()
            model.server_close()

    def test_real_shell_repairs_code_executes_it_delegates_and_keeps_session(self):
        requests, root_calls, child_calls = [], [0], [0]
        owner = self
        key = "fixture-real-provider-key-kept-outside-runtime"

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                owner.assertEqual(self.headers["x-api-key"], key)
                names = {item.get("name") for item in body.get("tools", [])}
                owner.assertTrue(
                    {"bash", "subagent", "web_search", "web_fetch"} <= names,
                    str(sorted(names)),
                )
                is_child = "ROOT_FIXTURE" not in json.dumps(body.get("messages", []))
                if is_child:
                    step = child_calls[0]
                    child_calls[0] += 1
                    command = (
                        {
                            "name": "bash",
                            "input": {
                                "command": "python3 -c \"from pathlib import Path; assert Path('result.txt').read_text().strip()=='1250'; Path('review.txt').write_text('verified')\"",
                                "description": "Verify result independently and save review",
                            },
                        }
                        if step == 0
                        else None
                    )
                    final = "Independent verification complete"
                else:
                    step = root_calls[0]
                    root_calls[0] += 1
                    if step == 0:
                        source = (
                            "import os\nfrom pathlib import Path\nassert not Path('/home/suyingrui/Desktop/medical-agent/.env').exists()\nassert not os.environ.get('UNRELATED_SECRET')\nassert not os.environ.get('DEEPSEEK_API_KEY') == "
                            + repr(key)
                            + "\n"
                            + TOKEN_LIMIT_PROBE
                            + "\nprint(1/0)\n"
                        )
                        command = {
                            "name": "bash",
                            "input": {
                                "command": "python3 - <<'PY'\nfrom pathlib import Path\nPath('calc.py').write_text("
                                + repr(source)
                                + ")\nPY\npython3 calc.py",
                                "description": "Create script and expose calculation failure",
                            },
                        }
                    elif step == 1:
                        owner.assertIn("ZeroDivisionError", json.dumps(body))
                        command = {
                            "name": "bash",
                            "input": {
                                "command": "python3 - <<'PY'\nfrom pathlib import Path\nPath('calc.py').write_text('print(1250)\\n')\nPY\npython3 calc.py > result.txt",
                                "description": "Repair script and execute corrected calculation",
                            },
                        }
                    elif step == 2:
                        command = {
                            "name": "subagent",
                            "input": {
                                "description": "Independently verify generated result",
                                "prompt": "CHILD_VERIFY: read result.txt, assert its value is 1250, save review.txt with verified.",
                                "run_in_background": False,
                            },
                        }
                    elif step == 4:
                        owner.assertIn("FOLLOW_UP", json.dumps(body))
                        command = {
                            "name": "bash",
                            "input": {
                                "command": "test -f review.txt && printf 'continued' > notes.txt",
                                "description": "Continue existing session and preserve prior artifacts",
                            },
                        }
                    else:
                        command = None
                    final = "完成 calc.py、result.txt 和独立复核。" if step < 4 else "接续任务完成。"
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "fixture-" + str(len(requests)),
                            "type": "message",
                            "role": "assistant",
                            "model": "deepseek-flash",
                            "content": [],
                            "usage": {"input_tokens": 20, "output_tokens": 1},
                        },
                    }
                ]
                if command:
                    events += [
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {
                                "type": "tool_use",
                                "id": "call-" + str(len(requests)),
                                "name": command["name"],
                                "input": {},
                            },
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": json.dumps(command["input"]),
                            },
                        },
                    ]
                else:
                    events += [
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": ""},
                        },
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": final},
                        },
                    ]
                events += [
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "tool_use" if command else "end_turn"},
                        "usage": {"output_tokens": 30},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    "event: " + item["type"] + "\ndata: " + json.dumps(item) + "\n\n"
                    for item in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="execution-test-") as raw:
                manager = ExecutionManager(root=Path(raw))
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": key,
                        "MEDAGENT_LLM_MODEL": "deepseek-flash",
                    }
                )
                try:
                    session.start_turn(
                        "ROOT_FIXTURE: create, run, repair, delegate verification and inspect results.",
                        12,
                    )
                    with self.assertRaises(ExecutionBusy):
                        session.start_turn("Must not race this turn")
                    snapshot = self.wait_terminal(session)
                    self.assertEqual(snapshot["status"], "completed", snapshot["error"])
                    self.assertEqual(
                        (session.directory / "result.txt").read_text().strip(), "1250"
                    )
                    self.assertEqual(
                        (session.directory / "review.txt").read_text(), "verified"
                    )
                    self.assertEqual(
                        (session.directory / "token-limit-check.txt").read_text(),
                        "blocked",
                    )
                    self.assertEqual(child_calls[0], 2)
                    self.assertEqual(root_calls[0], 4)
                    serialized = json.dumps(snapshot, ensure_ascii=False)
                    self.assertNotIn(key, serialized)
                    self.assertIn("subagent.started", serialized)
                    process = session.process
                    session.start_turn(
                        "FOLLOW_UP: preserve prior files and create notes.txt.", 6
                    )
                    self.assertEqual(self.wait_terminal(session)["status"], "completed")
                    self.assertIs(session.process, process)
                    self.assertEqual(
                        (session.directory / "notes.txt").read_text(), "continued"
                    )
                    self.assertTrue(process.poll() is None)
                finally:
                    manager.close()
                self.assertIsNotNone(process.poll())
        finally:
            model.shutdown()
            model.server_close()

    def wait_terminal(self, session, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = session.snapshot()
            if snapshot["status"] not in {"starting", "running", "cancelling"}:
                return snapshot
            time.sleep(0.05)
        self.fail(
            "Isolated runtime did not settle: "
            + json.dumps(snapshot, ensure_ascii=False)[-4000:]
        )
