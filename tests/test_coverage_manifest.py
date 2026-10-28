"""Is the testing finished? This file is the only thing that answers it.

Everything else proves that what we wrote works. This proves that we
wrote enough — and that the recorded gaps are still the real gaps.

The manifest is generated, so the assertions below are mostly freshness
checks. That is the point: adding a patient-facing capability without
regenerating turns this red, instead of quietly shipping an untested
card.
"""

import json
import unittest
from importlib import resources

from medagent.extensions.builtin import load_registry

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "coverage-manifest.json"
PACKET = ROOT / "docs" / "review-packets" / "explicit-calculation-v1" / "packet.json"


class CoverageManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.items = {item["id"]: item for item in load_registry().descriptions()}
        self.levels = {
            level: set(ids) for level, ids in self.manifest["levels"].items()
        }

    def test_manifest_lists_only_registered_capabilities(self):
        unknown = set().union(*self.levels.values()) - set(self.items)
        self.assertEqual(unknown, set(), "矩阵里有已不存在的能力")

    def test_every_capability_is_executed_by_a_test(self):
        missing = set(self.items) - self.levels["engine"]
        self.assertEqual(
            missing, set(), f"这些能力没有任何自动化测试真正执行过：{sorted(missing)[:5]}"
        )

    def test_every_patient_facing_capability_is_rendered_in_a_browser(self):
        patient = {i for i, item in self.items.items() if item.get("patient_form")}
        missing = patient - self.levels["browser"]
        self.assertEqual(
            missing, set(), f"这些患者可用能力没有浏览器测试：{sorted(missing)[:5]}"
        )

    def test_review_level_matches_the_current_packet(self):
        expected = {
            entry["id"]
            for entry in json.loads(PACKET.read_text(encoding="utf-8"))["items"]
        }
        self.assertEqual(self.levels["review"], expected)

    def test_recorded_gaps_are_still_the_real_gaps(self):
        for level, ids in self.levels.items():
            actual = set(self.items) - ids
            self.assertEqual(
                set(self.manifest["gaps"][level]),
                actual,
                f"{level} 的缺口清单与实际不符，需重新生成矩阵",
            )

    def test_counts_match_the_lists(self):
        for level, ids in self.levels.items():
            self.assertEqual(self.manifest["counts"][level], len(ids))

    def test_there_are_no_engine_gaps(self):
        # The one gap that is never acceptable: a capability nobody ran.
        self.assertEqual(self.manifest["gaps"]["engine"], [])


if __name__ == "__main__":
    unittest.main()
