"""Contract clarity and evaluator falsifiability for the expanded public suite."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmarks.execution_task_contract import compliance, prompt_for
from scripts.benchmarks.public.benchmark_public_execution import (
    prompt_for as legacy_prompt,
)
from scripts.benchmarks.public.public_execution_cases import (
    ROOT,
    inventory,
    load_cases,
    seed,
    sha256,
    verify,
)


FIXTURES = ROOT / "benchmarks/swe-lite-dev-expanded"


class PublicContractTests(unittest.TestCase):
    def test_new_regressions_are_allowed_but_appends_and_dependency_injection_are_rejected(
        self,
    ):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            initial = {
                "src/implementation.py": "value = 0\n",
                "tests/test_existing.py": "assert 1 == 1\n",
                "setup.cfg": "[tool]\n",
                ".benchmark_dependencies/library.py": "version = 1\n",
            }
            for name, text in initial.items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            original = inventory(directory)
            (directory / "src/implementation.py").write_text("value = 1\n")
            regression = directory / "regressions/test_new.py"
            regression.parent.mkdir()
            regression.write_text("def test_new(): assert True\n")
            self.assertTrue(all(compliance(directory, original).values()))
            for name, text in [
                (
                    "tests/test_existing.py",
                    initial["tests/test_existing.py"] + "assert 2 == 2\n",
                ),
                ("setup.cfg", "[different]\n"),
                ("tests/test_new.py", "assert True\n"),
                ("regressions/conftest.py", "# injected plugin\n"),
                (".benchmark_dependencies/injected.py", "# new dependency\n"),
                ("other/test.py", "def test_hidden(): pass\n"),
            ]:
                with self.subTest(path=name):
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                    self.assertFalse(all(compliance(directory, original).values()))
                    if name in initial:
                        target.write_text(initial[name])
                    else:
                        target.unlink()
                    self.assertTrue(all(compliance(directory, original).values()))

    def test_additional_tasks_are_disjoint_and_legacy_prompts_remain_frozen(self):
        old_manifest, old = load_cases()
        manifest, expanded = load_cases(FIXTURES)
        self.assertEqual(set(old) & set(expanded), set())
        self.assertEqual(len(expanded), 4)
        self.assertEqual(
            manifest["dataset_parquet_sha256"], old_manifest["dataset_parquet_sha256"]
        )
        historical = json.loads(
            (ROOT / "docs/evaluations/2026-10-16/v0.9.6/public-report.json").read_text()
        )
        for name, case in old.items():
            self.assertEqual(
                sha256(legacy_prompt(case).encode()), historical["prompts_sha256"][name]
            )
        for case in expanded.values():
            prompt = prompt_for(case)
            self.assertIn("保留原断言但追加用例也不允许", prompt)
            self.assertIn("regressions/test_*.py", prompt)
            self.assertTrue(prompt.endswith(case["metadata"]["problem_statement"]))
            self.assertNotIn(case["metadata"]["patch"], prompt)

    @unittest.skipUnless(
        os.environ.get("RUN_DSH_EXECUTION") == "1",
        "requires actual isolation and prepared sources",
    )
    def test_all_additional_original_bugs_fail_and_reference_checks_pass(self):
        _, cases = load_cases(FIXTURES)
        with tempfile.TemporaryDirectory() as raw:
            for name, case in cases.items():
                with self.subTest(case=name):
                    for reference in [False, True]:
                        base = Path(raw) / name / str(reference)
                        seed(base / "workspace", case)
                        result = verify(
                            base / "workspace", base / "state", case, reference
                        )
                        if reference:
                            self.assertTrue(result.get("passed"), result)
                            self.assertEqual(
                                len(result["outcomes"]), len(result["expected"])
                            )
                        else:
                            self.assertTrue(
                                result.get("original_bug_reproduced"), result
                            )


if __name__ == "__main__":
    unittest.main()
