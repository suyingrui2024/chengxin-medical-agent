"""Real official SDK on native Windows: commands, delegation, checks and resume."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_execution_delivery import Provider, tool, final
from medagent.execution import ExecutionManager


@unittest.skipUnless(os.name == "nt", "requires native Windows ProcessContainer")
class WindowsExecutionTests(unittest.TestCase):
    def test_original_child_grant_survives_a_foreground_followup(self):
        from tests.test_execution_ownership import exercise_child_ownership
        exercise_child_ownership(self)

    def test_original_command_deadline_survives_an_independent_followup(self):
        from tests.test_execution_command_deadline import exercise_command_deadline
        exercise_command_deadline(self)

    def test_explicit_inputs_reject_mutation_and_allow_generated_outputs(self):
        for mutation in [False, True]:
            with (
                self.subTest(input_mutation=mutation),
                tempfile.TemporaryDirectory(prefix="chengxin-inputs-") as raw,
            ):
                update = (
                    "p.write_text('bad');"
                    if mutation
                    else "Path('report.txt').write_text('generated');"
                )
                command = (
                    "python.exe -c \"from pathlib import Path;"
                    "p=Path('source.txt');tested=p.read_text();"
                    + update
                    + "assert tested=='good'\""
                )
                provider = Provider(
                    [
                        tool(
                            "bash",
                            command=(
                                "python.exe -c \"from pathlib import Path;"
                                "Path('source.txt').write_text('good')\""
                            ),
                            description="Prepare synthetic source input",
                        ),
                        tool(
                            "delivery_check",
                            label="核对不变输入",
                            command=command,
                            inputs=["SOURCE.TXT"],
                        ),
                        final("检查命令已结束，保留真实文件版本状态。"),
                        final("输入变化尚未复验，不能报告完成。"),
                    ]
                )
                manager = ExecutionManager(root=Path(raw))
                settings = {
                    "MEDAGENT_LLM_BASE_URL": (
                        f"http://127.0.0.1:{provider.server_port}/anthropic"
                    ),
                    "MEDAGENT_LLM_API_KEY": "native-inputs-loopback-only",
                    "MEDAGENT_LLM_MODEL": "fixture",
                }
                try:
                    session = manager.create(settings)
                    session.start_turn(
                        "合成输入版本检查：按工具计划声明inputs，生成输出不属于检查输入。",
                        6,
                        60,
                    )
                    deadline = time.monotonic() + 65
                    while (
                        session.status not in {"completed", "failed", "cancelled"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    self.assertEqual(
                        session.status,
                        "failed" if mutation else "completed",
                        session.error,
                    )
                    checks = [
                        entry
                        for entry in session.observations
                        if entry["tool"] == "delivery_check"
                    ]
                    self.assertEqual(len(checks), 1)
                    self.assertEqual(checks[0]["exit_code"], 0)
                    self.assertEqual(
                        checks[0].get("check_inputs"),
                        [] if mutation else ["SOURCE.TXT"],
                    )
                    self.assertEqual(
                        (session.directory / "source.txt").read_text(),
                        "bad" if mutation else "good",
                    )
                    if mutation:
                        self.assertEqual(session.error, "delivery_checks_failed")
                    else:
                        self.assertEqual(
                            (session.directory / "report.txt").read_text(),
                            "generated",
                        )
                    self.assertEqual(len(provider.requests), 4 if mutation else 3)
                    identity = session.id
                    manager.close()
                    manager = ExecutionManager(root=Path(raw))
                    restored = manager.open(identity, {})
                    self.assertEqual(
                        [
                            item.get("check_inputs")
                            for item in restored.observations
                            if item["tool"] == "delivery_check"
                        ],
                        [[] if mutation else ["SOURCE.TXT"]],
                    )
                    self.assertIsNone(restored.process)
                finally:
                    manager.close()
                    provider.shutdown()
                    provider.server_close()

    def test_native_code_delegation_delivery_and_same_session_continuation(self):
        root_calls, child_calls = [0], [0]

        def respond(_index, body):
            text = json.dumps(body.get("messages", []))
            if "WINDOWS_ROOT_FIXTURE" not in text:
                step = child_calls[0]
                child_calls[0] += 1
                return (
                    tool(
                        "bash",
                        command="python.exe -c \"from pathlib import Path; print('WINDOWS_CHILD_CWD='+str(Path.cwd())); assert Path('result.txt').read_text(encoding='utf-8-sig').strip()=='1250'; Path('review.txt').write_text('verified')\"",
                        description="Verify output independently",
                    )
                    if step == 0
                    else final("Independent Windows verification complete")
                )
            step = root_calls[0]
            root_calls[0] += 1
            if step == 0:
                self.assertTrue(
                    {"bash", "subagent", "web_search", "web_fetch", "medical_calculate"}
                    <= {item.get("name") for item in body.get("tools", [])}
                )
                return tool(
                    "bash",
                    command="[Console]::WriteLine('WINDOWS_PROVIDER_CWD='+$PWD.ProviderPath); 'print(1250)' | Set-Content -Encoding UTF8 calc.py; python.exe calc.py | Set-Content -Encoding UTF8 result.txt; npm.cmd --version | Set-Content -Encoding UTF8 npm-version.txt",
                    description="Write and run Python on Windows",
                    timeoutMs=30000,
                )
            if step == 1:
                return tool(
                    "subagent",
                    description="Independent result verification",
                    prompt="WINDOWS_CHILD_VERIFY: independently check result.txt contains 1250, write review.txt with verified",
                    run_in_background=False,
                )
            if step == 2:
                return tool(
                    "delivery_check",
                    label="Verify actual Windows artifacts",
                    command="python.exe -c \"from pathlib import Path; assert Path('result.txt').read_text(encoding='utf-8-sig').strip()=='1250'; assert Path('review.txt').read_text()=='verified'\"",
                )
            if step == 3:
                return tool(
                    "medical_calculate",
                    capability_id="calculate.units",
                    input={"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                    evidence="1.25 kg",
                )
            if step == 5:
                return tool(
                    "bash",
                    command="if (-not (Test-Path -LiteralPath review.txt)) { throw 'missing previous result' }; 'continued' | Set-Content -Encoding UTF8 notes.txt",
                    description="Continue the original Windows session",
                    timeoutMs=30000,
                )
            return final("Native Windows task complete")

        provider = Provider(respond)
        with tempfile.TemporaryDirectory(prefix="chengxin-execution-") as raw:
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create(
                    {
                        "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                        "MEDAGENT_LLM_API_KEY": "native-windows-loopback-only",
                        "MEDAGENT_LLM_MODEL": "fixture",
                    }
                )
                self.assertEqual(
                    session.snapshot()["isolation"], "windows-processcontainer"
                )

                def finish():
                    deadline = time.monotonic() + 90
                    while (
                        session.status not in {"completed", "failed", "cancelled"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.05)
                    self.assertEqual(
                        session.status,
                        "completed",
                        json.dumps(
                            {"error": session.error, "events": list(session.events)},
                            ensure_ascii=False,
                        ),
                    )

                session.start_turn(
                    "WINDOWS_ROOT_FIXTURE: create and run calc.py, delegate verification, and run a delivery check. 请把1.25 kg换算成g。",
                    12,
                )
                finish()
                self.assertTrue(
                    (session.directory / "review.txt").is_file(),
                    json.dumps(
                        {
                            "workspace": str(session.directory),
                            "workspace_files": [
                                p.name for p in session.directory.iterdir()
                            ],
                            "state_files": [
                                str(p.relative_to(session.state_directory))
                                for p in session.state_directory.rglob("review.txt")
                            ],
                            "events": list(session.events),
                        },
                        ensure_ascii=False,
                    ),
                )
                self.assertEqual(
                    (session.directory / "review.txt").read_text(), "verified"
                )
                self.assertRegex(
                    (session.directory / "npm-version.txt")
                    .read_text(encoding="utf-8-sig")
                    .strip(),
                    r"^\d+\.\d+\.\d+$",
                )
                self.assertTrue(
                    any(
                        item["tool"] == "delivery_check" and item.get("exit_code") == 0
                        for item in session.observations
                    )
                )
                self.assertEqual(len(session.calculations), 1)
                self.assertEqual(session.calculations[0]["data"]["value"], "1250")
                self.assertEqual(session.calculations[0]["input_status"], "unconfirmed")
                session.start_turn(
                    "WINDOWS_ROOT_FIXTURE FOLLOW_UP: retain the original artifacts and write notes.txt",
                    4,
                )
                finish()
                self.assertEqual(
                    (session.directory / "notes.txt")
                    .read_text(encoding="utf-8-sig")
                    .strip(),
                    "continued",
                )
                self.assertGreaterEqual(child_calls[0], 2)
                self.assertNotIn(
                    "native-windows-loopback-only", json.dumps(session.snapshot())
                )
            finally:
                manager.close()
                provider.shutdown()
                provider.server_close()


if __name__ == "__main__":
    unittest.main()
