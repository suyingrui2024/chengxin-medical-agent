"""v1.3 oral self-care content (design docs/问诊结论单设计文档.md §13.7).

Scope of this package (content only):
- self_care.json gains exactly one group, ``oral_routine``
  (match=``oral``, tier=``routine``, reviewed_by=``engineering-v1``,
  reviewed_at=``2026-10-26``), with generic safe entries only:
  warm-salt-water rinse, chew on the other side, avoid too
  cold / hot / hard / sweet food, sleep with the pillow raised, and
  seek care at once for facial swelling / difficulty opening the
  mouth / fever. No dose, no drug name, no new threshold.
- The seek-care line lives ONLY in the self-care group: §13.7/task
  rule — return_now_if.by_pathway has no ``oral`` entry, so the line
  must not be disguised as an existing oral safety-net entry.
- The oral routing change in the composer is NOT this package, so
  these tests audit outlook through the exact lookup the composer
  uses (``conclusion._self_care_group`` + ``care.get("course")``),
  not through an end-to-end composed sheet for a toothache case.

Outlook audit (design §13.7): the old "13/18 groups empty" account
is stale — a direct read before this change showed all 18 groups
already carried a ``course``. This file audits every group, and
records the one path that still cannot yield an oral-specific
outlook (oral urgent, see below) as a residual instead of
inventing copy for it.
"""

from __future__ import annotations

import json
import re
import unittest
from importlib import resources

from medagent import conclusion as conclusion_module
from medagent.conclusion import _self_care_group

# Dose patterns: number + unit, frequency wording, course wording.
# (Oral entries contain no digits at all; the patterns below are the
# operative guard, not a digit ban — a future editor adding "3 次"
# style wording must trip this test, not slip past a loose check.)
_DOSE_PATTERNS = [
    r"\d+\s*(?:mg|g|ml|毫克|毫升|克|片|粒|滴|包|袋|支)",
    r"每天|每日|每次|每隔|一日\s*\d|一天\s*\d|日服",
    r"疗程|连服|连吃|服用\s*\d|用药\s*\d",
]

# Drug names that must never appear in the oral group's entries.
# (Other groups' existing principle lines such as "不要自行服用抗生素"
# are out of scope here; this list is scoped to the oral group only.)
_DRUG_NAMES = [
    "抗生素",
    "止痛药",
    "布洛芬",
    "对乙酰氨基酚",
    "阿莫西林",
    "甲硝唑",
    "头孢",
    "激素药",
    "消炎药",
]

_ORAL_DO = [
    "用温盐水漱口",
    "换对侧咀嚼",
    "睡觉时把枕头垫高",
    "面部肿胀、张口困难或发热时立即就医",
]
_ORAL_AVOID = [
    "避免过冷、过热、过硬、过甜的食物",
]
_ORAL_SEEK_CARE = "面部肿胀、张口困难或发热时立即就医"

# The 18 groups that predate v1.3; the oral group is the 19th.
_PRE_V13_IDS = [
    "generic_routine",
    "generic_urgent",
    "respiratory_routine",
    "respiratory_urgent",
    "chest_urgent",
    "chest_routine",
    "abdominal_routine",
    "abdominal_urgent",
    "headache_routine",
    "headache_urgent",
    "dizziness_routine",
    "dizziness_urgent",
    "back_routine",
    "back_urgent",
    "urinary_routine",
    "urinary_urgent",
    "skin_routine",
    "skin_urgent",
]


def _load_self_care():
    return json.loads(
        resources.files("medagent.content")
        .joinpath("self_care.json")
        .read_text(encoding="utf-8")
    )


def _oral_group(data):
    matches = [g for g in data["groups"] if g["id"] == "oral_routine"]
    assert len(matches) == 1, f"expected exactly one oral_routine group, got {len(matches)}"
    return matches[0]


class OralGroupSchemaTests(unittest.TestCase):
    def test_oral_routine_metadata(self):
        oral = _oral_group(_load_self_care())
        self.assertEqual(oral["match"], "oral")
        self.assertEqual(oral["tier"], "routine")
        self.assertEqual(oral["reviewed_by"], "engineering-v1")
        self.assertEqual(oral["reviewed_at"], "2026-10-26")
        for field in ("do", "avoid", "measure"):
            self.assertIsInstance(oral[field], list)
            for item in oral[field]:
                self.assertIsInstance(item, str)
                self.assertTrue(item.strip())

    def test_oral_entries_are_the_generic_safe_items_only(self):
        oral = _oral_group(_load_self_care())
        self.assertEqual(oral["do"], _ORAL_DO)
        self.assertEqual(oral["avoid"], _ORAL_AVOID)
        self.assertEqual(oral["measure"], [])

    def test_oral_entries_carry_no_dose(self):
        oral = _oral_group(_load_self_care())
        entries = " ".join(oral["do"] + oral["avoid"] + oral["measure"])
        for pattern in _DOSE_PATTERNS:
            self.assertNotRegex(
                entries, pattern, f"dose pattern {pattern!r} in oral entries"
            )

    def test_oral_entries_name_no_drug(self):
        oral = _oral_group(_load_self_care())
        entries = " ".join(oral["do"] + oral["avoid"] + oral["measure"])
        for drug in _DRUG_NAMES:
            self.assertNotIn(drug, entries, f"drug name {drug!r} in oral entries")

    def test_oral_entries_pass_banned_phrase_lint(self):
        oral = _oral_group(_load_self_care())
        entries = " ".join(oral["do"] + oral["avoid"] + oral["measure"])
        for phrase in conclusion_module.BANNED_PHRASES:
            self.assertNotIn(phrase, entries)

    def test_seek_care_line_is_self_care_only_not_safety_net(self):
        # §13.7: the swelling / mouth-opening / fever line is a
        # self-care-group entry only. by_pathway has no oral entry,
        # so it must not appear as (or as if it were) an oral
        # safety-net item, and must not leak into the generic net.
        data = _load_self_care()
        oral = _oral_group(data)
        self.assertIn(_ORAL_SEEK_CARE, oral["do"])
        self.assertNotIn("oral", data["return_now_if"]["by_pathway"])
        for items in data["return_now_if"]["by_pathway"].values():
            self.assertNotIn(_ORAL_SEEK_CARE, items)
        self.assertNotIn(_ORAL_SEEK_CARE, conclusion_module.RETURN_NOW_IF)

    def test_pre_v13_groups_still_present_and_version_bumped(self):
        # Content convention (cf. 1.0 -> 1.1 on group additions,
        # 1.1 -> 1.2 on course additions): adding a group bumps the
        # minor version, here 1.2 -> 1.3. The version pin lives here
        # so a future content release updates it alongside the
        # content, not silently.
        data = _load_self_care()
        self.assertEqual(data["version"], "1.3")
        ids = [g["id"] for g in data["groups"]]
        self.assertEqual(len(ids), len(_PRE_V13_IDS) + 1)
        for group_id in _PRE_V13_IDS:
            self.assertIn(group_id, ids)
        self.assertIn("oral_routine", ids)


class OutlookAuditTests(unittest.TestCase):
    def test_every_group_has_a_course(self):
        # Old account ("13/18 groups empty") is stale: this audits
        # every group as it stands now, oral included.
        for group in _load_self_care()["groups"]:
            course = group.get("course")
            self.assertIsInstance(course, str, f"{group['id']} has no course")
            self.assertTrue(course.strip(), f"{group['id']} has an empty course")

    def test_no_duplicate_match_tier_pair(self):
        pairs = [
            (g["match"], g["tier"]) for g in _load_self_care()["groups"]
        ]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_rendering_chain_retrieves_each_group_course(self):
        # The composer's outlook is exactly
        # ``care = _self_care_group(sheet_group, tier)`` then
        # ``sheet["outlook"] = care.get("course")``. Auditing through
        # that same lookup proves the rendering chain can really
        # reach each group's course — not just that the field
        # exists in the JSON.
        for group in _load_self_care()["groups"]:
            care = _self_care_group(group["match"], group["tier"])
            self.assertIsNotNone(
                care, f"no retrievable group for {group['match']}/{group['tier']}"
            )
            self.assertEqual(care["id"], group["id"])
            self.assertEqual(care.get("course"), group["course"])
            self.assertTrue(care["course"].strip())

    def test_oral_routine_course_is_retrievable(self):
        care = _self_care_group("oral", "routine")
        self.assertIsNotNone(care)
        self.assertEqual(care["id"], "oral_routine")
        self.assertEqual(
            care["course"],
            "这类口腔不适多数会在数天内逐渐缓解或出现明确变化，观察期间重点看面部肿胀、张口困难或发热有没有新出现或加重。",
        )

    def test_oral_urgent_falls_back_to_generic_residual(self):
        # RESIDUAL (v1.3 out of scope, §13.7): no dedicated oral
        # urgent group exists, so the lookup falls back to
        # generic_urgent. Outlook is therefore retrievable but
        # generic, not oral-specific. This test documents that
        # fallback deliberately — it must not be "fixed" here by
        # inventing an oral urgent course.
        data = _load_self_care()
        self.assertFalse(
            any(
                g["match"] == "oral" and g["tier"] == "urgent"
                for g in data["groups"]
            )
        )
        care = _self_care_group("oral", "urgent")
        self.assertIsNotNone(care)
        self.assertEqual(care["id"], "generic_urgent")
        self.assertTrue(care["course"].strip())


if __name__ == "__main__":
    unittest.main()
