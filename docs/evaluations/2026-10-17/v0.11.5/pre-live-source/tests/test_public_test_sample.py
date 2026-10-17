"""Reject missing/skipped targets, name-prefix collisions and mutable task selection."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from select_public_test_cases import selection
from public_test_sample import classify, resolve_ids, run_code, code_for, parse


def instance(failed, passed):
    return {"FAIL_TO_PASS": json.dumps(failed), "PASS_TO_PASS": json.dumps(passed)}


class PublicTestOracleTests(unittest.TestCase):
    def test_selection_uses_the_entire_population_and_is_order_independent(self):
        rows = [
            {"instance_id": f"repo-{n:03d}", "repo": "project/repo"} for n in range(300)
        ]
        a, chosen = selection(rows, "a" * 64, 2026101601, 6)
        b, other = selection(list(reversed(rows)), "a" * 64, 2026101601, 6)
        self.assertEqual(chosen, other)
        self.assertEqual(a, b)
        self.assertEqual(len(a["selected_ids"]), 6)
        with self.assertRaises(ValueError):
            selection(rows[:-1], "a" * 64, 1, 6)
        with self.assertRaises(ValueError):
            selection([rows[0]] * 300, "a" * 64, 1, 6)

    def test_identifier_resolution_never_substitutes_an_unrelated_test(self):
        with self.assertRaises(ValueError):
            resolve_ids(
                instance(["tests/test_a.py::test_a"], []), ["tests/test_a.py::test_ab"]
            )
        mapped = resolve_ids(
            instance(["test_Abs"], ["tests/test_a.py::test_b[x"]),
            [
                "sympy/test.py::test_Abs",
                "sympy/test.py::test_Abs_extra",
                "tests/test_a.py::test_b[x=1]",
                "tests/test_a.py::test_b[x=2]",
            ],
        )
        self.assertEqual(mapped["test_Abs"], ["sympy/test.py::test_Abs"])
        self.assertEqual(len(mapped["tests/test_a.py::test_b[x"]), 2)
        with self.assertRaises(ValueError):
            resolve_ids(instance(["test_a"], ["test_a"]), ["a.py::test_a"])

    def test_reference_needs_every_pass_and_original_needs_every_designated_failure(
        self,
    ):
        base = {
            "runtime_completed": True,
            "pytest_exit_code": 0,
            "test_id_map": {"f": ["a.py::f"], "p": ["a.py::p"]},
            "fail_to_pass": ["f"],
            "pass_to_pass": ["p"],
        }
        self.assertTrue(
            classify({**base, "outcomes": {"a.py::f": "passed", "a.py::p": "passed"}})[
                "passed"
            ]
        )
        for outcomes in [
            {"a.py::f": "passed"},
            {"a.py::f": "passed", "a.py::p": "skipped"},
            {"a.py::f": "failed", "a.py::p": "skipped"},
        ]:
            result = classify({**base, "outcomes": outcomes})
            self.assertFalse(result["passed"])
            self.assertFalse(result["original_bug_reproduced"])
        self.assertTrue(
            classify(
                {
                    **base,
                    "pytest_exit_code": 1,
                    "outcomes": {"a.py::f": "failed", "a.py::p": "passed"},
                }
            )["original_bug_reproduced"]
        )

    def test_long_oracle_program_uses_stdin_and_does_not_hit_argv_limit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / "workspace"
            directory.mkdir()
            result = run_code(
                directory,
                root / "state",
                "padding=" + repr("x" * 200000) + "\nprint(len(padding))\n",
                10000,
            )
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["stdout"].strip(), "200000")

    def test_real_collection_original_failure_and_reference_pass_use_separate_copies(
        self,
    ):
        root = Path(__file__).resolve().parents[1]
        wheel = (
            root
            / ".runtime/public-test-sample/wheels/pytest_mock-3.11.1-py3-none-any.whl"
        )
        if not wheel.exists():
            self.skipTest("Requires the pinned pytest-mock research wheel")
        metadata = {
            **instance(
                ["tests/test_new.py::test_new"],
                ["tests/test_existing.py::test_existing"],
            ),
            "patch": """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1,2 +1,2 @@
 def add(a,b):
-    return a-b
+    return a+b
""",
            "test_patch": """diff --git a/tests/test_new.py b/tests/test_new.py
new file mode 100644
--- /dev/null
+++ b/tests/test_new.py
@@ -0,0 +1,3 @@
+from foo import add
+def test_new():
+    assert add(2,3)==5
""",
        }
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            source = temporary / "source"
            (source / "tests").mkdir(parents=True)
            (source / "foo.py").write_text("def add(a,b):\n    return a-b\n")
            (source / "tests/test_existing.py").write_text(
                "def test_existing():\n    assert 1+1==2\n"
            )
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(source / ".benchmark_dependencies")
            copied = temporary / "collect"
            shutil.copytree(source, copied)
            collected = parse(
                run_code(
                    copied,
                    temporary / "collect-state",
                    code_for(metadata, "collect"),
                    10000,
                )
            )
            self.assertEqual(collected.get("pytest_exit_code"), 0, collected)
            mapping = resolve_ids(metadata, collected["collected"])
            for phase in ["original", "reference"]:
                copied = temporary / phase
                shutil.copytree(source, copied)
                result = classify(
                    parse(
                        run_code(
                            copied,
                            temporary / (phase + "-state"),
                            code_for(metadata, phase, mapping),
                            10000,
                        )
                    )
                )
                self.assertTrue(
                    result.get("original_bug_reproduced")
                    if phase == "original"
                    else result.get("passed"),
                    result,
                )
