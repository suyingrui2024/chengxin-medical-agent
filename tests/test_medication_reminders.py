"""Medication reminder plans (design §6.3): merge baseline + consultation
medications, never invent a schedule, never suggest a dose, never judge
interactions."""
from __future__ import annotations

import re
import unittest

from medagent.medication_reminders import build_medication_reminders

FORBIDDEN_IN_NOTES = ("剂量", "mg", "毫克", "相互作用", "配伍", "禁忌")


class MedicationReminderTests(unittest.TestCase):
    def test_merge_dedupes_baseline_first(self):
        plan = build_medication_reminders(
            [{"name": "氨氯地平", "purpose": "降压"},
             {"name": "二甲双胍", "purpose": "降糖"}],
            ["二甲双胍", "布洛芬"],
        )
        self.assertEqual(
            [item["medication_name"] for item in plan],
            ["氨氯地平", "二甲双胍", "布洛芬"],
        )
        self.assertEqual(plan[0]["purpose"], "降压")
        self.assertEqual(plan[0]["source"], "baseline")
        self.assertEqual(plan[2]["source"], "consultation")

    def test_no_set_time_means_pending_not_invented(self):
        plan = build_medication_reminders([{"name": "氨氯地平"}])
        self.assertEqual(plan[0]["scheduled_times"], [])
        self.assertEqual(plan[0]["status"], "pending_setup")
        self.assertNotRegex(plan[0]["note"], r"\d{1,2}:\d{2}")

    def test_patient_set_times_are_kept_and_invalid_times_dropped(self):
        plan = build_medication_reminders(
            [{"name": "氨氯地平"}, {"name": "二甲双胍"}],
            scheduled_times={
                "氨氯地平": ["08:00", "20:00"],
                "二甲双胍": ["25:00", "早上"],
            },
        )
        self.assertEqual(plan[0]["scheduled_times"], ["08:00", "20:00"])
        self.assertEqual(plan[0]["status"], "scheduled")
        self.assertEqual(plan[1]["scheduled_times"], [])
        self.assertEqual(plan[1]["status"], "pending_setup")

    def test_notes_carry_no_dose_or_interaction_language(self):
        plan = build_medication_reminders(
            [{"name": "氨氯地平"}, {"name": "二甲双胍"}],
            scheduled_times={"氨氯地平": ["08:00"]},
        )
        for item in plan:
            for word in FORBIDDEN_IN_NOTES:
                self.assertNotIn(word, item["note"])
            self.assertNotRegex(item["note"], r"\d+\s*(mg|g|片|粒)")

    def test_denial_consultation_answer_adds_nothing(self):
        plan = build_medication_reminders(
            [{"name": "氨氯地平"}], consultation_medications="没有"
        )
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["medication_name"], "氨氯地平")

    def test_bare_string_consultation_answer_recovers_one_name(self):
        plan = build_medication_reminders([], consultation_medications="吃了布洛芬")
        self.assertEqual([item["medication_name"] for item in plan], ["布洛芬"])

    def test_blank_and_duplicate_names_dropped(self):
        plan = build_medication_reminders(
            ["  ", {"name": "布洛芬"}, "布洛芬", "布洛芬 "]
        )
        self.assertEqual(len(plan), 1)


if __name__ == "__main__":
    unittest.main()
