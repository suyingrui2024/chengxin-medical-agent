import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from unittest.mock import patch

from medagent.runtime import build_runtime
from medagent.server import Handler, MedicalAgentServer


@unittest.skipUnless(
    os.environ.get("RUN_DSH_INTEGRATION") == "1",
    "set RUN_DSH_INTEGRATION=1 to launch real DSH",
)
class DshHttpIntegration(unittest.TestCase):
    def test_patient_api_through_real_dsh_to_validated_multi_fact_update(self):
        requests = []
        proposals = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "consult_for",
                            "value": "self",
                            "assertion": "present",
                            "evidence": "我自己",
                        },
                        {
                            "key": "age_years",
                            "value": 32,
                            "assertion": "present",
                            "evidence": "32岁",
                        },
                        {
                            "key": "chief_complaint",
                            "value": "咳嗽",
                            "assertion": "present",
                            "evidence": "咳嗽",
                        },
                        {
                            "key": "onset",
                            "value": "两天",
                            "assertion": "present",
                            "evidence": "两天",
                        },
                    ]
                },
            },
            {
                "tool": "run_capability",
                "arguments": {
                    "id": "calculate.units",
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                    "evidence": "请换算1.25kg为g",
                },
            },
            {"tool": "ask_question", "arguments": {"question_id": "emergency_screen"}},
        ]

        class ModelHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(body)
                self.server.test_case.assertFalse(body.get("tools"))
                proposal = proposals.pop(0)
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "fixture",
                            "model": "deepseek-v4-flash",
                            "usage": {"input_tokens": 10, "output_tokens": 1},
                        },
                    },
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "text_delta",
                            "text": json.dumps(proposal, ensure_ascii=False),
                        },
                    },
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 20},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        model.test_case = self
        thread = threading.Thread(target=model.serve_forever, daemon=True)
        thread.start()
        runtime = None
        http = None
        try:
            env = {
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{model.server_port}/anthropic",
                "MEDAGENT_LLM_API_KEY": "fixture-only",
                "MEDAGENT_LLM_MODEL": "deepseek-v4-flash",
            }
            with patch.dict(os.environ, env):
                runtime = build_runtime(backend="dsh")
                ctx = runtime.start()
                http = MedicalAgentServer(
                    ("127.0.0.1", 0),
                    Handler,
                    ctx.services.require("consultation"),
                    ctx.services.require("events"),
                    "dsh",
                )
                threading.Thread(target=http.serve_forever, daemon=True).start()
                base = f"http://127.0.0.1:{http.server_port}"

                def post(path, data):
                    req = Request(
                        base + path,
                        json.dumps(data).encode(),
                        {"Content-Type": "application/json"},
                    )
                    with urlopen(req, timeout=70) as result:
                        return json.load(result)

                created = post("/api/sessions", {})
                sid = created["session_id"]
                proposed = post(
                    f"/api/sessions/{sid}/messages",
                    {
                        "text": "我自己32岁，咳嗽两天，请换算1.25kg为g",
                        "expected_action_id": created["reply"]["action_id"],
                    },
                )
                self.assertEqual(proposed["reply"]["kind"], "calculation_input")
                self.assertEqual(proposed["state"]["calculations"], [])
                self.assertEqual(len(requests), 2)
                computed = post(
                    f"/api/sessions/{sid}/messages",
                    {
                        "text": "核对换算参数",
                        "choice_value": "confirm_calculation",
                        "expected_action_id": proposed["reply"]["action_id"],
                        "calculation": {
                            "proposal_id": proposed["reply"]["calculation_input"][
                                "proposal_id"
                            ],
                            "input": {
                                "value": "1.25",
                                "from_unit": "kg",
                                "to_unit": "g",
                            },
                        },
                    },
                )
                self.assertEqual(computed["reply"]["kind"], "calculation_result")
                self.assertEqual(
                    len(requests), 2, "parameter confirmation must not invoke DSH"
                )
                result = post(
                    f"/api/sessions/{sid}/messages",
                    {
                        "text": "继续问诊",
                        "choice_value": "resume_after_calculation",
                        "expected_action_id": computed["reply"]["action_id"],
                        "calculation": {
                            "execution_id": computed["reply"]["calculation_result"][
                                "execution_id"
                            ]
                        },
                    },
                )
                self.assertEqual(result["reply"]["question_id"], "emergency_screen")
                self.assertEqual(result["state"]["facts"]["age_years"]["value"], 32)
                self.assertEqual(result["state"]["facts"]["onset"]["evidence"], "两天")
                self.assertEqual(len(requests), 3)
                self.assertEqual(
                    result["state"]["calculations"][0]["data"]["value"], "1250"
                )
                self.assertEqual(
                    result["state"]["calculations"][0]["input_status"], "confirmed"
                )
                self.assertEqual(
                    len(result["state"]["facts"]),
                    4,
                    "calculation must remain separate from clinical facts",
                )
                self.assertIn(
                    "1250",
                    json.dumps(requests[2], ensure_ascii=False),
                    "DSH must observe the actual capability result",
                )
                emergency = post(
                    f"/api/sessions/{sid}/messages",
                    {
                        "text": "现在叫不醒",
                        "expected_action_id": result["reply"]["action_id"],
                    },
                )
                self.assertEqual(emergency["reply"]["kind"], "emergency")
                self.assertEqual(len(requests), 3, "urgent input must bypass model")
                events = ctx.services.require("events").for_session(sid)
                responses = [e for e in events if e.event_type == "model.responded"]
                self.assertEqual(len(responses), 3)
                self.assertTrue(responses[0].payload["runtime_events"])
        finally:
            if http:
                http.shutdown()
                http.server_close()
            if runtime:
                runtime.stop()
            model.shutdown()
            model.server_close()


if __name__ == "__main__":
    unittest.main()
