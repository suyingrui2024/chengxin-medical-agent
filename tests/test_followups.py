"""Longitudinal loop (design phase 4): follow-up scheduling at close,
outcome recording, re-triage linkage, emergency exclusion, and the
review-flywheel corpus merge rules."""
from __future__ import annotations

import copy
import unittest

from medagent.consultation import UserInput
from medagent.flywheel import build_candidate, merge_candidates
from medagent.runtime import build_runtime

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "gi_details": ("肚脐周围隐痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}


class FollowUpFlowTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def _answer(self, text, choice=None):
        return self.engine.submit(self.state.session_id, UserInput(text, choice))

    def _drive_to_summary(self, overrides=None):
        answers = {**ANSWERS, **(overrides or {})}
        reply = self.reply
        for _ in range(25):
            if reply.kind == "summary":
                return reply
            if reply.kind == "self_check":
                reply = self._answer("暂时跳过", "skip")
                continue
            text, choice = answers[reply.question_id]
            reply = self._answer(text, choice)
        raise AssertionError(f"did not reach summary; last kind={reply.kind}")

    def test_confirmed_summary_schedules_routine_follow_up(self):
        summary = self._drive_to_summary()
        self.assertFalse(self.state.follow_up)
        reply = self._answer("确认", "confirm_summary")
        self.assertEqual(reply.kind, "closed")
        plan = self.state.follow_up
        self.assertEqual(plan["kind"], "routine_checkin")
        self.assertEqual(plan["status"], "scheduled")
        # allergies answered 不知道 -> unknown fact present -> 48h, else 72h
        self.assertEqual(plan["due_in_hours"], 48)
        self.assertEqual(reply.follow_up["kind"], "routine_checkin")
        kinds = [e.event_type for e in self.events.for_session(self.state.session_id)]
        self.assertIn("followup.scheduled", kinds)

    def test_urgent_trajectory_answer_closes_with_follow_up(self):
        self._answer("我自己", "self")
        self._answer("32岁")
        self._answer("咳嗽发烧两天")
        self._answer("都没有", "no")
        self._answer("两天前开始")
        reply = self._answer("在加重", "worsening")
        self.assertEqual(reply.kind, "urgent")
        self.assertEqual(self.state.follow_up["kind"], "urgent_checkin")
        self.assertEqual(self.state.follow_up["due_in_hours"], 24)

    def test_emergency_close_has_no_follow_up(self):
        self._answer("我自己", "self")
        reply = self._answer("他现在叫不醒")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.follow_up, {})

    def test_outcome_recording_and_retriage_linkage(self):
        self._drive_to_summary()
        self._answer("确认", "confirm_summary")
        result = self.engine.record_follow_up_outcome(
            self.state.session_id, "recovered", "已痊愈"
        )
        self.assertFalse(result["requires_retriage"])
        self.assertEqual(self.state.follow_up["status"], "completed")
        with self.assertRaises(ValueError):
            self.engine.record_follow_up_outcome(self.state.session_id, "recovered")

    def test_worsened_outcome_creates_linked_retriage_session(self):
        self._drive_to_summary()
        self._answer("确认", "confirm_summary")
        result = self.engine.record_follow_up_outcome(
            self.state.session_id, "worsened", "又烧起来了"
        )
        self.assertTrue(result["requires_retriage"])
        new_state = self.engine.state(result["new_session_id"])
        self.assertEqual(
            new_state.runtime_snapshot["follow_up_of"], self.state.session_id
        )
        self.assertEqual(
            self.state.follow_up["retriage_session_id"], result["new_session_id"]
        )
        kinds = [e.event_type for e in self.events.for_session(self.state.session_id)]
        self.assertIn("followup.outcome_recorded", kinds)
        self.assertIn("followup.retriage_session_created", kinds)

    def test_invalid_outcome_rejected(self):
        self._drive_to_summary()
        self._answer("确认", "confirm_summary")
        with self.assertRaises(ValueError):
            self.engine.record_follow_up_outcome(self.state.session_id, "fine")

    def test_outcome_without_plan_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.record_follow_up_outcome(self.state.session_id, "recovered")


class FlywheelTests(unittest.TestCase):
    def test_candidate_requires_reviewer_and_flag_for_positive(self):
        with self.assertRaises(ValueError):
            build_candidate(
                sample_id="x1", text="……", expect_trigger=True,
                red_flag_id="seizure", reviewer="",
            )
        with self.assertRaises(ValueError):
            build_candidate(
                sample_id="x2", text="……", expect_trigger=True, reviewer="李医生"
            )
        candidate = build_candidate(
            sample_id="x3", text="老人突然说不出话，嘴也歪了",
            expect_trigger=True, red_flag_id="stroke_like",
            reviewer="李医生", note="真实病例脱敏",
        )
        self.assertEqual(candidate["review_status"], "clinician_reviewed")

    def test_merge_dedups_rejects_unreviewed_and_is_pure(self):
        corpus = {
            "samples": [
                {"id": "s1", "red_flag_id": "", "category": "benign",
                 "text": "胃有点不舒服", "expect_trigger": False},
            ]
        }
        original = copy.deepcopy(corpus)
        candidates = [
            build_candidate(
                sample_id="c1", text="胃 有点 不舒服", expect_trigger=False,
                reviewer="李医生",
            ),
            build_candidate(
                sample_id="c2", text="突然人事不省", expect_trigger=True,
                red_flag_id="unresponsive", reviewer="李医生",
            ),
            {"id": "c3", "text": "未审样本", "expect_trigger": False,
             "red_flag_id": "", "category": "x", "review_status": "unreviewed"},
        ]
        merged, report = merge_candidates(corpus, candidates)
        self.assertEqual(report["added"], ["c2"])
        self.assertEqual(report["duplicates"], ["c1"])
        self.assertEqual(report["rejected"], ["c3"])
        self.assertEqual(len(merged["samples"]), 2)
        self.assertEqual(corpus, original)


if __name__ == "__main__":
    unittest.main()
