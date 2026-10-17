"""Real SDK delivery checks, bounded repair, cancellation and cold observations."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import shutil
import threading
import time
import unittest

from medagent.execution import ExecutionManager
from medagent.execution_observations import validate_observation


def tool(name, **arguments):
    return {"type": "tool_use", "name": name, "input": arguments}


def final(text="本机确定模型结束当前步骤。"):
    return {"type": "text", "text": text}


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Expected bounded execution transition did not occur")


class Provider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, responses):
        self.responses, self.requests = responses, []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.requests.append(body)
                index = len(self.server.requests)
                content = dict(
                    self.server.responses(index, body)
                    if callable(self.server.responses)
                    else self.server.responses[index - 1]
                    if index <= len(self.server.responses)
                    else final("Unexpected additional model request")
                )
                if content["type"] == "tool_use":
                    content["id"] = f"delivery-{index}"
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"fixture-{index}",
                            "model": "fixture",
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
                            "stop_reason": "tool_use"
                            if content["type"] == "tool_use"
                            else "end_turn"
                        },
                        "usage": {"output_tokens": 10},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


@unittest.skipUnless(
    os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK"
)
class DeliveryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = ExecutionManager(root=Path(self.temporary.name))
        self.provider = None

    def tearDown(self):
        self.manager.close()
        if self.provider:
            self.provider.shutdown()
            self.provider.server_close()
        self.temporary.cleanup()

    def session(self, responses):
        self.provider = Provider(responses)
        self.settings = {
            "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{self.provider.server_port}/anthropic",
            "MEDAGENT_LLM_API_KEY": "local-delivery-fixture",
            "MEDAGENT_LLM_MODEL": "fixture",
        }
        return self.manager.create(self.settings)

    def finish(self, session):
        wait_until(
            lambda: session.snapshot()["status"] in {"completed", "failed", "cancelled"}
        )
        return session.snapshot()

    def observations(self, session):
        return [
            value for value in session.observations if value["tool"] == "delivery_check"
        ]

    def test_failed_check_reaches_model_then_one_repair_retests_same_command_and_restores(
        self,
    ):
        session = self.session(
            [
                tool(
                    "bash",
                    command="printf 'assert 2 + 2 == 5\\n' > check.py",
                    description="Create incorrect expectation",
                ),
                tool("delivery_check", label="新增测试", command="python3 check.py"),
                final("提前声称完成"),
                tool(
                    "bash",
                    command="printf 'assert 2 + 2 == 4\\n' > check.py",
                    description="Repair expectation from actual arithmetic",
                ),
                tool("delivery_check", label="新增测试", command="python3 check.py"),
                final("修正并重新运行了同一检查。"),
            ]
        )
        session.start_turn("合成检查：创建、检查并修正一个错误预期。", 8, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
        self.assertEqual(len(self.provider.requests), 6)
        self.assertIn("AssertionError", json.dumps(self.provider.requests[2]))
        self.assertIn(
            "交付检查纠错", json.dumps(self.provider.requests[3], ensure_ascii=False)
        )
        self.assertEqual(
            sum(
                event["type"] == "turn.check_repairing" for event in snapshot["events"]
            ),
            1,
        )
        values = self.observations(session)
        self.assertEqual([value["exit_code"] for value in values], [1, 0])
        self.assertEqual(values[0]["check_key"], values[1]["check_key"])
        self.assertEqual(values[0]["turn_id"], values[1]["turn_id"])
        for value in values:
            self.assertEqual(validate_observation(value), value)
            self.assertEqual(value["check_turn_id"], snapshot["turn_id"])
        for changes in [
            {"check_required": 0},
            {"check_key": "x" * 64},
            {"check_label": {"untrusted": "text"}},
            {"check_turn_id": "00000000-0000-4000-8000-000000000000"},
            {"tool": "bash"},
        ]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ValueError):
                    validate_observation({**values[0], **changes})
        identifier = session.id
        self.manager.close()
        self.manager = ExecutionManager(root=Path(self.temporary.name))
        restored = self.manager.open(identifier, {})
        self.assertEqual(self.observations(restored), values)
        self.assertIsNone(restored.process)

    def test_unresolved_check_is_not_completion_and_repair_does_not_loop(self):
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="失败检查",
                    command="python3 -c 'raise AssertionError(123)'",
                ),
                final("已完成"),
                final("未修正检查"),
            ]
        )
        session.start_turn("合成未解决检查", 8, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertEqual(len(self.provider.requests), 3)
        self.assertEqual(
            sum(
                event["type"] == "turn.check_repairing" for event in snapshot["events"]
            ),
            1,
        )
        session.start_turn("另一个独立任务：只回复已收到。", 2, 15)
        self.assertEqual(self.finish(session)["status"], "completed")
        self.assertEqual(len(self.provider.requests), 4)

    def test_no_repair_renews_an_exhausted_model_budget(self):
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="失败检查",
                    command="python3 -c 'raise AssertionError(123)'",
                ),
                final("提前完成"),
            ]
        )
        session.start_turn("合成严格额度", 2, 15)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertEqual(len(self.provider.requests), 2)
        self.assertFalse(
            any(event["type"] == "turn.check_repairing" for event in snapshot["events"])
        )

    def test_a_different_passing_command_cannot_erase_an_unresolved_check(self):
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="原检查",
                    command="python3 -c 'raise AssertionError(123)'",
                ),
                tool("delivery_check", label="其它命令", command="python3 -c 'pass'"),
                final("提前完成"),
                final("仍未解决原检查"),
            ]
        )
        session.start_turn("合成防止无关检查替换", 8, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertEqual([v["exit_code"] for v in self.observations(session)], [1, 0])
        self.assertEqual(len(self.provider.requests), 4)

    def test_child_check_uses_shared_budget_and_retains_child_lineage(self):
        roots, children = [], []

        def responses(index, body):
            if "ROOT_DELIVERY" in json.dumps(body.get("messages", [])):
                roots.append(body)
                return (
                    tool(
                        "subagent",
                        description="独立执行交付检查",
                        prompt="CHILD_DELIVERY: run the delivery_check tool with label 子任务检查 and command python3 -c 'assert 2 + 2 == 4', then report the actual result.",
                        run_in_background=False,
                    )
                    if len(roots) == 1
                    else final("收到子任务实际检查结果")
                )
            children.append(body)
            return (
                tool(
                    "delivery_check",
                    label="子任务检查",
                    command="python3 -c 'assert 2 + 2 == 4'",
                )
                if len(children) == 1
                else final("子任务检查命令退出0")
            )

        session = self.session(responses)
        session.start_turn("ROOT_DELIVERY 委托子任务实际执行检查。", 8, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
        self.assertEqual(len(roots), 2)
        self.assertEqual(len(children), 2)
        self.assertEqual(len(self.provider.requests), 4)
        values = self.observations(session)
        self.assertEqual(len(values), 1)
        self.assertNotEqual(values[0]["session_id"], session.id)
        self.assertEqual(values[0]["check_turn_id"], snapshot["turn_id"])
        self.assertEqual(values[0]["exit_code"], 0)

    def test_inspection_only_records_failure_without_authorizing_repair(self):
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="仅检查",
                    command="python3 -c 'raise AssertionError(123)'",
                    required=False,
                ),
                final("已按要求检查，结果未通过，未修改文件。"),
            ]
        )
        session.start_turn("只检查并报告，不修改任何文件。", 8, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(len(self.provider.requests), 2)
        self.assertFalse(self.observations(session)[0]["check_required"])
        self.assertEqual(self.observations(session)[0]["exit_code"], 1)

    def test_a_required_command_cannot_be_downgraded_with_the_optional_flag(self):
        command = "python3 -c 'raise AssertionError(123)'"
        session = self.session(
            [
                tool("delivery_check", label="必需检查", command=command),
                tool("delivery_check", label="试图降级", command=command, required=False),
                final("提前完成"),
            ]
        )
        session.start_turn("合成不可降级的交付检查。", 3, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertTrue(
            all(value["check_required"] for value in self.observations(session))
        )

    def missing_budget_manager(self, first_only=False, second_only=False):
        source = Path(self.temporary.name) / "source"
        source.mkdir()
        original = Path(__file__).resolve().parents[1]
        shutil.copytree(original / "integrations", source / "integrations")
        (source / "node_modules").symlink_to(
            original / "node_modules", target_is_directory=True
        )
        plugin = source / "integrations/dsh/execution-delivery.mjs"
        text = plugin.read_text()
        needle = 'process.env.DEEPSEEK_BASE_URL + "/budget"'
        self.assertEqual(text.count(needle), 1)
        if first_only or second_only:
            text = text.replace(
                'export const name = "chengxin-execution-delivery";',
                'let fixtureBudgetAttempts = 0;\nexport const name = "chengxin-execution-delivery";',
            )
            attempt = 2 if second_only else 1
            replacement = f'process.env.DEEPSEEK_BASE_URL + (++fixtureBudgetAttempts === {attempt} ? "/missing-budget" : "/budget")'
        else:
            replacement = 'process.env.DEEPSEEK_BASE_URL + "/missing-budget"'
        plugin.write_text(text.replace(needle, replacement))
        self.manager.close()
        self.manager = ExecutionManager(
            root=Path(self.temporary.name) / "workspaces", source_root=source
        )

    def test_missing_check_result_cannot_silently_pass_the_completion_gate(self):
        self.missing_budget_manager()
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="必需检查",
                    command="printf executed > check-executed.txt",
                ),
                final("提前声称完成"),
                final("无法取得有效检查结果"),
            ]
        )
        session.start_turn("合成检查结果缺失；未执行不能算作通过。", 6, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertFalse((session.directory / "check-executed.txt").exists())
        self.assertEqual(len(self.provider.requests), 3)

    def test_missing_optional_result_stays_a_report_without_authorizing_repair(self):
        self.missing_budget_manager()
        session = self.session(
            [
                tool(
                    "delivery_check",
                    label="仅检查报告",
                    command="printf executed > check-executed.txt",
                    required=False,
                ),
                final("检查未能执行，报告该错误。"),
            ]
        )
        session.start_turn("合成只检查不修改的报告任务。", 6, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "completed")
        self.assertFalse((session.directory / "check-executed.txt").exists())
        self.assertEqual(len(self.provider.requests), 2)
        self.assertFalse(
            any(event["type"] == "turn.check_repairing" for event in snapshot["events"])
        )

    def test_a_missing_retry_cannot_reuse_an_earlier_passing_receipt(self):
        self.missing_budget_manager(second_only=True)
        command = "printf executed > check-executed.txt"
        session = self.session(
            [
                tool("delivery_check", label="首次实际通过", command=command),
                tool("delivery_check", label="重试结果缺失", command=command),
                final("不能借用上次成功"),
                final("未能取得重试结果"),
            ]
        )
        session.start_turn("合成同命令通过后再检查，缺失的新结果不能沿用旧通过。", 6, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "delivery_checks_failed")
        self.assertEqual(self.observations(session)[0]["exit_code"], 0)
        self.assertEqual(len(self.provider.requests), 4)

    def test_retry_of_the_same_command_resolves_a_previously_missing_receipt(self):
        self.missing_budget_manager(first_only=True)
        command = "printf executed > check-executed.txt"
        session = self.session(
            [
                tool("delivery_check", label="必需检查", command=command),
                final("提前声称完成"),
                tool("delivery_check", label="同命令重试", command=command),
                final("检查已实际执行通过。"),
            ]
        )
        session.start_turn("合成检查结果缺失后允许同命令真实重试。", 6, 30)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "completed", snapshot.get("error"))
        self.assertEqual(
            (session.directory / "check-executed.txt").read_text(), "executed"
        )
        self.assertEqual(len(self.provider.requests), 4)
        self.assertEqual(self.observations(session)[-1]["exit_code"], 0)

    def test_check_timeout_stops_its_job_instead_of_leaving_a_late_write(self):
        command = "python3 -c \"import time;from pathlib import Path;time.sleep(2);Path('late-check.txt').write_text('late')\""
        session = self.session(
            [
                tool("delivery_check", label="短时限", command=command, timeout_ms=150),
                final("不能完成该检查"),
            ]
        )
        session.start_turn("合成检查超时", 2, 15)
        snapshot = self.finish(session)
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(self.observations(session)[0]["state"], "timed_out")
        time.sleep(2.1)
        self.assertFalse((session.directory / "late-check.txt").exists())

    def test_user_cancel_interrupts_a_check_and_same_runtime_accepts_next_task(self):
        command = "python3 -c \"import time;from pathlib import Path;Path('entered.txt').write_text('yes');time.sleep(2);Path('late-check.txt').write_text('late')\""
        session = self.session(
            [tool("delivery_check", label="可停止检查", command=command), final("下一轮正常结束")]
        )
        session.start_turn("合成检查取消", 4, 15)
        wait_until(lambda: (session.directory / "entered.txt").exists())
        pid = session.process.pid
        session.cancel()
        self.assertEqual(self.finish(session)["status"], "cancelled")
        session.start_turn("继续独立任务", 2, 15)
        self.assertEqual(self.finish(session)["status"], "completed")
        self.assertEqual(session.process.pid, pid)
        time.sleep(2.1)
        self.assertFalse((session.directory / "late-check.txt").exists())
