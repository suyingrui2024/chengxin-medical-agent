"""Real registered computation, evidence rejection, SDK errors and cold receipts."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from medagent.execution import ExecutionManager, ExecutionError
from medagent.execution_capabilities import CapabilityError, dispatch, freeze_binding
from medagent.extensions.builtin import load_registry
from medagent.presentation import calculation_presentation


def payload(
    text="请把1.25 kg换算成g", quote="1.25 kg", value="1.25", before="kg", after="g"
):
    turn = str(uuid4())
    return {
        "binding": freeze_binding(),
        "operation": "execute",
        "workspace_id": str(uuid4()),
        "turn_id": turn,
        "sources": [{"id": turn, "text": text}],
        "request": {
            "capability_id": "calculate.units",
            "input": {"value": value, "from_unit": before, "to_unit": after},
            "evidence": quote,
        },
    }


class RegisteredCalculationTests(unittest.TestCase):
    def test_legacy_workspace_does_not_gain_calculation_permissions(self):
        config = {
            "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
            "MEDAGENT_LLM_API_KEY": "fixture",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(config)
            identity = session.id
            manager.close()
            path = Path(raw) / ".index" / (identity + ".json")
            record = json.loads(path.read_text())
            record.pop("capability_binding")
            record.pop("calculations")
            path.write_text(json.dumps(record))
            manager = ExecutionManager(root=Path(raw))
            try:
                restored = manager.open(identity, {})
                self.assertIsNone(restored.capability_binding)
                self.assertEqual(restored.calculations, [])
                self.assertIsNone(restored.process)
            finally:
                manager.close()

    def test_old_receipt_stays_readable_when_implementation_unavailable_and_corrupt_presentation_is_rejected(
        self,
    ):
        config = {
            "MEDAGENT_LLM_BASE_URL": "https://fixture.invalid/anthropic",
            "MEDAGENT_LLM_API_KEY": "fixture",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        with tempfile.TemporaryDirectory() as raw:
            manager = ExecutionManager(root=Path(raw))
            session = manager.create(config)
            request = payload()
            request["workspace_id"] = session.id
            result = dispatch(request)
            session.turns = [
                {
                    "id": request["turn_id"],
                    "text": request["sources"][0]["text"],
                    "status": "completed",
                    "max_calls": 6,
                }
            ]
            session.turn_count = 1
            session.calculations = [result]
            session.persist(strict=True)
            identity = session.id
            manager.close()
            path = Path(raw) / ".index" / (identity + ".json")
            record = json.loads(path.read_text())
            record["capability_binding"]["implementation_sha256"] = "0" * 64
            record["calculations"][0]["implementation_sha256"] = "0" * 64
            record["calculations"][0]["presentation"] = calculation_presentation(
                record["calculations"][0]
            )
            path.write_text(json.dumps(record))
            manager = ExecutionManager(root=Path(raw))
            try:
                restored = manager.open(identity, {})
                self.assertEqual(restored.calculations[0]["data"]["value"], "1250")
                self.assertIsNone(restored.process)
                with self.assertRaises(CapabilityError) as error:
                    dispatch(
                        {"binding": restored.capability_binding, "operation": "catalog"}
                    )
                self.assertEqual(error.exception.code, "capability_version_unavailable")
            finally:
                manager.close()
            record = json.loads(path.read_text())
            record["calculations"][0]["presentation"]["blocks"][0]["data"][
                "value"
            ] = "9999"
            path.write_text(json.dumps(record))
            manager = ExecutionManager(root=Path(raw))
            try:
                with self.assertRaises(ExecutionError):
                    manager.open(identity, {})
                self.assertNotIn(identity, manager.sessions)
            finally:
                manager.close()

    def test_decimal_result_has_host_provenance_and_unconfirmed_status(self):
        request = payload()
        result = dispatch(request)
        self.assertEqual(result["data"]["value"], "1250")
        self.assertEqual(result["input_status"], "unconfirmed")
        self.assertEqual(result["quality"], "not_assessed")
        self.assertEqual(result["evidence"]["source_turn_id"], request["turn_id"])
        self.assertEqual(result["evidence"]["scope"], "quantity_and_source_unit_only")
        block = result["presentation"]["blocks"][0]
        self.assertIn(result["execution_id"], block["id"])
        self.assertEqual(block["data"]["provenance"]["evidence"]["quote"], "1.25 kg")
        for unit, alias, value, after, expected in [
            ("kg", "公斤", "0.125", "g", "125"),
            ("mg", "毫克", "0.000000000001", "kg", "0.000000000000000001"),
            ("L", "升", "999999999999.999999999999", "mL", "999999999999999.999999999"),
        ]:
            quote = value + alias
            self.assertEqual(
                dispatch(payload(quote, quote, value, unit, after))["data"]["value"],
                expected,
            )

    def test_forged_omitted_and_different_quantity_or_unit_cannot_pass(self):
        for changes in [
            {"evidence": "2 kg"},
            {"input": {"value": "2", "from_unit": "kg", "to_unit": "g"}},
            {"input": {"value": "1.25", "from_unit": "g", "to_unit": "mg"}},
            {"evidence": ""},
        ]:
            request = payload()
            request["request"].update(changes)
            with self.assertRaises(CapabilityError):
                dispatch(request)
        request = payload()
        request["request"]["quality"] = "verified"
        with self.assertRaisesRegex(CapabilityError, "三个字段"):
            dispatch(request)
        request = payload()
        request["request"]["input"]["module"] = "os"
        with self.assertRaisesRegex(CapabilityError, "契约"):
            dispatch(request)

    def test_trimming_sign_exponent_ranges_or_density_does_not_launder_source(self):
        for text, quote, value in [
            ("-1.25 kg", "1.25 kg", "1.25"),
            ("− 1.25 kg", "1.25 kg", "1.25"),
            ("1e3kg", "3kg", "3"),
            ("1e+3kg", "3kg", "3"),
            ("1,000kg", "000kg", "000"),
            ("1-2kg", "2kg", "2"),
            ("1至2kg", "2kg", "2"),
            ("1kg～2kg", "1kg", "1"),
            ("1.25 kg/mL", "1.25 kg", "1.25"),
            ("1 000kg", "000kg", "000"),
            ("1e 3kg", "3kg", "3"),
            ("1. 25kg", "25kg", "25"),
            ("1，000kg", "000kg", "000"),
        ]:
            with self.subTest(text=text), self.assertRaises(CapabilityError):
                dispatch(payload(text, quote, value))

    def test_cross_dimension_requires_explicit_density_and_is_rejected(self):
        with self.assertRaises(CapabilityError) as error:
            dispatch(payload(after="mL"))
        self.assertEqual(error.exception.code, "calculation_rejected")
        self.assertIn("密度", str(error.exception))

    def test_frozen_binding_disabled_capability_and_version_change(self):
        registry = load_registry()
        old = freeze_binding(registry)
        disabled = registry.with_enabled("calculate.units", False)
        self.assertEqual(
            dispatch({"binding": freeze_binding(disabled), "operation": "catalog"})[
                "capabilities"
            ],
            [],
        )
        self.assertEqual(
            len(dispatch({"binding": old, "operation": "catalog"})["capabilities"]), 1
        )
        for field in ["registry_sha256", "implementation_sha256"]:
            request = payload()
            request["binding"][field] = "0" * 64
            with self.assertRaises(CapabilityError) as error:
                dispatch(request)
            self.assertEqual(error.exception.code, "capability_version_unavailable")
        with self.assertRaises(CapabilityError) as error:
            dispatch({"binding": None, "operation": "catalog"})
        self.assertEqual(error.exception.code, "capability_binding_missing")


def model_reply(handler, index, tool=None):
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"cap-{index}",
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
                "id": f"call-{index}",
                "name": tool["name"],
                "input": {},
            }
            if tool
            else {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(tool["input"]),
            }
            if tool
            else {"type": "text_delta", "text": "计算完成，输入仍待核对。"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
            "usage": {"output_tokens": 20},
        },
        {"type": "message_stop"},
    ]
    data = "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
        for event in events
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "Requires real isolated DSH"
)
class CapabilitySdkTests(unittest.TestCase):
    def test_sdk_observes_rejection_repairs_calculation_delegates_and_restores_receipts(
        self,
    ):
        owner = self
        observed, root_step, child_step = [], [0], [0]
        quote = "1.25 kg"

        def calculate(after="g"):
            return {
                "name": "medical_calculate",
                "input": {
                    "capability_id": "calculate.units",
                    "evidence": quote,
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": after},
                },
            }

        class Model(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                observed.append(body)
                names = {item.get("name") for item in body.get("tools", [])}
                owner.assertTrue(
                    {"medical_capabilities", "medical_calculate", "bash", "subagent"}
                    <= names
                )
                is_child = "ROOT_CAPABILITY" not in json.dumps(body["messages"])
                if is_child:
                    step = child_step[0]
                    child_step[0] += 1
                    tool = calculate() if step == 0 else None
                else:
                    step = root_step[0]
                    root_step[0] += 1
                    if step == 0:
                        tool = {"name": "medical_capabilities", "input": {}}
                    elif step == 1:
                        owner.assertIn("calculate.units", json.dumps(body["messages"]))
                        tool = calculate("mL")
                    elif step == 2:
                        owner.assertIn(
                            "calculation_rejected", json.dumps(body["messages"])
                        )
                        tool = calculate()
                    elif step == 3:
                        owner.assertIn("1250", json.dumps(body["messages"]))
                        tool = {
                            "name": "subagent",
                            "input": {
                                "description": "核对注册计算",
                                "prompt": "CHILD_CAPABILITY 请对用户的1.25 kg调用注册换算成g；不执行脚本。",
                                "run_in_background": False,
                            },
                        }
                    else:
                        tool = None
                model_reply(self, len(observed), tool)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                config = {
                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{server.server_port}/anthropic",
                    "MEDAGENT_LLM_API_KEY": "fixture-host-secret",
                    "MEDAGENT_LLM_MODEL": "deepseek-flash",
                }
                manager = ExecutionManager(root=Path(raw))
                session = manager.create(config)
                identity = session.id
                try:
                    session.start_turn("ROOT_CAPABILITY 请把1.25 kg换算成g，并委托核对。", 12)
                    deadline = time.monotonic() + 40
                    while (
                        session.status in {"starting", "running", "cancelling"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    snapshot = session.snapshot()
                    self.assertEqual(snapshot["status"], "completed", snapshot["error"])
                    self.assertEqual(root_step[0], 5)
                    self.assertEqual(child_step[0], 2)
                    self.assertEqual(len(snapshot["calculations"]), 2)
                    self.assertTrue(
                        all(
                            r["data"]["value"] == "1250"
                            for r in snapshot["calculations"]
                        )
                    )
                    self.assertEqual(
                        len(
                            [
                                e
                                for e in snapshot["events"]
                                if e["type"] == "capability.failed"
                            ]
                        ),
                        1,
                    )
                    self.assertIn("subagent.started", json.dumps(snapshot))
                finally:
                    manager.close()
                count = len(observed)
                manager = ExecutionManager(root=Path(raw))
                try:
                    restored = manager.open(identity, {})
                    self.assertEqual(len(restored.calculations), 2)
                    self.assertIsNone(restored.process)
                    self.assertEqual(len(observed), count)
                    self.assertTrue(
                        all(
                            e.get("restored")
                            for e in restored.events
                            if e["type"] == "capability.completed"
                        )
                    )
                    self.assertNotIn(
                        config["MEDAGENT_LLM_API_KEY"],
                        (Path(raw) / ".index" / (identity + ".json")).read_text(),
                    )
                finally:
                    manager.close()
        finally:
            server.shutdown()
            server.server_close()
