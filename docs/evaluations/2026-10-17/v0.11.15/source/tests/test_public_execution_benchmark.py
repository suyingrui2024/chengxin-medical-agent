"""Check public benchmark falsifiability without contacting an external model."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from public_execution_cases import (
    ROOT,
    frozen_source_check,
    inventory,
    load_cases,
    seed,
    verify,
)


class PublicFixtureTests(unittest.TestCase):
    def test_public_data_integrity_and_complete_test_id_mappings(self):
        manifest, cases = load_cases()
        self.assertEqual(manifest["split"], "dev")
        self.assertFalse(manifest["official_score"])
        self.assertEqual(len(cases), 4)
        for case in cases.values():
            official = json.loads(case["metadata"]["FAIL_TO_PASS"]) + json.loads(
                case["metadata"]["PASS_TO_PASS"]
            )
            for old, full in case["provenance"]["test_id_map"].items():
                self.assertIn(old, official)
                self.assertTrue(full.startswith(old))

    def test_frozen_tests_and_runner_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "project"
            (directory / "src").mkdir(parents=True)
            (directory / "tests").mkdir()
            (directory / "src/implementation.py").write_text(
                "# initial implementation\n"
            )
            (directory / "tests/test_example.py").write_text("assert 1 == 1\n")
            original = inventory(directory)
            (directory / "src/implementation.py").write_text(
                "# changed implementation\n"
            )
            self.assertTrue(all(frozen_source_check(directory, original).values()))
            (directory / "tests/test_example.py").write_text("# empty tests\n")
            self.assertFalse(
                frozen_source_check(directory, original)[
                    "upstream_tests_and_config_preserved"
                ]
            )
            (directory / "sitecustomize.py").write_text("# test injection\n")
            self.assertFalse(
                frozen_source_check(directory, original)["no_new_test_control_files"]
            )

    @unittest.skipUnless(
        os.environ.get("RUN_DSH_EXECUTION") == "1",
        "requires actual filesystem namespace",
    )
    def test_all_original_defects_fail_and_all_reference_tests_pass(self):
        _, cases = load_cases()
        if any(
            not (ROOT / case["provenance"]["source_cache"]).is_dir()
            for case in cases.values()
        ):
            self.skipTest(
                "Prepare pinned public sources with scripts/prepare_public_execution.py"
            )
        with tempfile.TemporaryDirectory() as raw:
            for name, case in cases.items():
                with self.subTest(case=name):
                    for reference in [False, True]:
                        root = Path(raw) / name / str(reference)
                        seed(root / "workspace", case)
                        result = verify(
                            root / "workspace", root / "state", case, reference
                        )
                        if reference:
                            self.assertTrue(result.get("passed"), result)
                            self.assertEqual(
                                len(result["outcomes"]), len(result["expected"])
                            )
                        else:
                            self.assertFalse(result.get("passed"))
                            self.assertTrue(
                                result.get("original_bug_reproduced"), result
                            )


if __name__ == "__main__":
    unittest.main()
