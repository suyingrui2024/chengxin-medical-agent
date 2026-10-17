"""Validate holdout checkers and the independent SDK arm, without real API calls."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib import error, request

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from benchmark_execution import Baseline, BoundedGateway, check_artifacts, seed_project
from execution_benchmark_cases import CASES, code

REFERENCES = {
    "precision-repair": {
        "units.py": code(
            """
            from decimal import Decimal, localcontext
            def convert(value, source, target):
                powers = {'kg':3, 'g':0, 'mg':-3}
                try: number = Decimal(value)
                except Exception: raise ValueError('number')
                if source not in powers or target not in powers or not number.is_finite() or number < 0: raise ValueError('input')
                with localcontext() as ctx:
                    ctx.prec = 100
                    text = format(number * Decimal(10) ** (powers[source]-powers[target]), 'f')
                return (text.rstrip('0').rstrip('.') if '.' in text else text) if number else '0'
        """
        ),
        "cli.py": code(
            """
            import json,sys
            from units import convert
            if __name__ == '__main__':
                data=json.load(sys.stdin)
                print(json.dumps({'result':convert(data['value'],data['from'],data['to'])}))
        """
        ),
        "followup.json": '{"result":"0.00789"}',
    },
    "csv-feature": {
        "aggregate.py": code(
            """
            from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
            def plain(number):
                text=format(number,'f')
                return (text.rstrip('0').rstrip('.') if '.' in text else text) if number else '0'
            def aggregate(rows):
                groups={}
                for row in rows:
                    group=row.get('group','').strip()
                    try: value=Decimal(row['value'])
                    except (KeyError,InvalidOperation): continue
                    if group and value.is_finite(): groups.setdefault(group,[]).append(value)
                output=[]
                with localcontext() as ctx:
                    ctx.prec=100
                    for group,values in sorted(groups.items()):
                        mean=(sum(values)/len(values)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
                        output.append({'group':group,'count':len(values),'min':plain(min(values)),'max':plain(max(values)),'mean':format(mean if mean else Decimal('0.00'),'.2f')})
                return output
        """
        ),
        "cli.py": code(
            """
            import json,sys
            from reader import read_rows
            from aggregate import aggregate
            if __name__ == '__main__': print(json.dumps({'groups':aggregate(read_rows(sys.stdin))}))
        """
        ),
        "followup.json": '{"groups":[{"group":"c","count":2,"min":"3","max":"5","mean":"4.00"}]}',
    },
    "delegated-review": {
        "invoice.py": code(
            """
            from decimal import Decimal, localcontext, ROUND_HALF_UP
            def total(lines):
                with localcontext() as ctx:
                    ctx.prec=100
                    value=Decimal(0)
                    for row in lines:
                        try: price=Decimal(row['price']); quantity=row['quantity']
                        except Exception: raise ValueError('row')
                        if not price.is_finite() or price<0 or type(quantity) is not int or not 0<=quantity<=1000: raise ValueError('row')
                        value+=price*quantity
                    return format(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP),'.2f')
        """
        ),
        "review.json": '{"checks":["round total once","precision context"]}',
        "followup.json": '{"result":"0.03"}',
    },
}


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires actual namespace/SDK process"
)
class BenchmarkCheckerTests(unittest.TestCase):
    def test_hidden_checkers_reject_broken_seeds_accept_references_and_reject_bad_continuation(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            for name, case in CASES.items():
                with self.subTest(case=name):
                    directory, state = Path(raw) / name, Path(raw) / (name + "-state")
                    seed_project(directory, case)
                    self.assertFalse(
                        check_artifacts(directory, state, case["probe"])["passed"]
                    )
                    for filename, content in REFERENCES[name].items():
                        (directory / filename).write_text(content)
                    result = check_artifacts(directory, state, case["probe"])
                    self.assertTrue(result["passed"], result)
                    (directory / "followup.json").write_text("{}")
                    self.assertFalse(
                        check_artifacts(directory, state, case["probe"])["passed"]
                    )

    def test_direct_official_sdk_baseline_reuses_process_and_real_files(self):
        calls = []

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body)
                names = {t.get("name") for t in body.get("tools", [])}
                if not {"bash", "subagent", "web_search", "web_fetch"} <= names:
                    self.send_error(400)
                    return
                step = len(calls)
                if step in {1, 3}:
                    content = {
                        "type": "tool_use",
                        "id": f"tool-{step}",
                        "name": "bash",
                        "input": {
                            "command": "printf '%s' '" + str(step) + "' > result.txt",
                            "description": "Write actual artifact",
                        },
                    }
                else:
                    content = {"type": "text", "text": "Done"}
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"m-{step}",
                            "type": "message",
                            "role": "assistant",
                            "model": "deepseek-flash",
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
                            "stop_reason": "tool_use" if step in {1, 3} else "end_turn"
                        },
                        "usage": {"output_tokens": 5},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                    for e in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                directory, state = Path(raw) / "workspace", Path(raw) / "state"
                directory.mkdir()
                state.mkdir()
                baseline = Baseline(
                    Path(os.path.relpath(directory)),
                    Path(os.path.relpath(state)),
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "fixture-only",
                        "MEDAGENT_LLM_MODEL": "deepseek-flash",
                    },
                )
                try:
                    baseline.receive("ready", 30)
                    process = baseline.process
                    for expected in ["1", "3"]:
                        result = baseline.run(
                            "Create result.txt using bash and finish.", 4
                        )
                        self.assertEqual(result["status"], "completed", result)
                        self.assertEqual(
                            (directory / "result.txt").read_text(), expected
                        )
                        self.assertIs(baseline.process, process)
                finally:
                    baseline.close()
        finally:
            server.shutdown()
            server.server_close()


class BenchmarkGatewayTests(unittest.TestCase):
    def test_authenticated_gateway_persists_calls_and_enforces_turn_and_total_caps(
        self,
    ):
        seen = []

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append(self.headers.get("x-api-key"))
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                journal = Path(raw) / "requests.json"
                gateway = BoundedGateway(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "real-fixture-key",
                    },
                    2,
                    journal,
                )
                threading.Thread(target=gateway.serve_forever, daemon=True).start()

                def send(token=None, max_tokens=8192):
                    return request.urlopen(
                        request.Request(
                            f"http://127.0.0.1:{gateway.server_port}/anthropic/messages",
                            json.dumps({"max_tokens": max_tokens}).encode(),
                            {
                                "Content-Type": "application/json",
                                "x-api-key": token or gateway.token,
                            },
                        ),
                        timeout=5,
                    )

                try:
                    gateway.begin_turn(1)
                    with self.assertRaises(error.HTTPError) as failure:
                        send("bad")
                    self.assertEqual(failure.exception.code, 401)
                    with self.assertRaises(error.HTTPError) as failure:
                        send(max_tokens=999999)
                    self.assertEqual(failure.exception.code, 400)
                    send().close()
                    with self.assertRaises(error.HTTPError) as failure:
                        send()
                    self.assertEqual(failure.exception.code, 429)
                    gateway.begin_turn(8)
                    send().close()
                    with self.assertRaises(error.HTTPError) as failure:
                        send()
                    self.assertEqual(failure.exception.code, 429)
                    gateway.persist()
                    self.assertEqual(
                        json.loads(journal.read_text())["attempted_calls"], 2
                    )
                    self.assertEqual(seen, ["real-fixture-key"] * 2)
                    self.assertNotIn("real-fixture-key", journal.read_text())
                finally:
                    gateway.shutdown()
                    gateway.server_close()
        finally:
            provider.shutdown()
            provider.server_close()
