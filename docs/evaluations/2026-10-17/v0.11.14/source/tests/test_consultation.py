import unittest

from medagent.consultation import StaleActionError, UserInput
from medagent.domain import Assertion, ObservationQuality, Phase, RiskLevel, Source
from medagent.runtime import build_runtime


class ConsultationTestCase(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def answer(self, text, choice=None):
        self.reply = self.engine.submit(self.state.session_id, UserInput(text, choice))
        return self.reply

    def establish_adult_complaint(self, complaint="咳嗽发烧两天"):
        self.answer("我自己", "self")
        self.answer("32岁")
        self.answer(complaint)

    def test_session_starts_with_one_question_and_logs_visible_reply(self):
        self.assertEqual(self.reply.question_id, "consult_for")
        self.assertEqual(self.state.active_question, "consult_for")
        kinds = [
            event.event_type for event in self.events.for_session(self.state.session_id)
        ]
        self.assertEqual(kinds, ["session.created", "turn.agent_reply"])

    def test_invalid_age_is_not_recorded_as_a_fact(self):
        self.answer("我自己", "self")
        reply = self.answer("很多岁")
        self.assertEqual(reply.kind, "clarification")
        self.assertNotIn("age_years", self.state.facts)
        self.assertEqual(self.state.active_question, "age")

    def test_raw_emergency_signal_interrupts_normal_flow(self):
        self.answer("我自己", "self")
        reply = self.answer("他现在叫不醒")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertEqual(self.state.risk.level, RiskLevel.EMERGENCY)
        self.assertIn("120", reply.message)

    def test_negated_emergency_phrase_does_not_trigger_text_gate(self):
        self.establish_adult_complaint("咳嗽，但没有严重呼吸困难")
        self.assertNotEqual(self.reply.kind, "emergency")

    def test_confirmed_emergency_screen_routes_to_emergency(self):
        self.establish_adult_complaint()
        self.assertEqual(self.reply.question_id, "emergency_screen")
        reply = self.answer("有任一项", "yes")
        self.assertEqual(reply.kind, "emergency")
        self.assertEqual(reply.summary["care_route"], "立即急救")

    def test_unknown_is_distinct_from_absent(self):
        self.establish_adult_complaint()
        self.answer("不确定", "unknown")
        fact = self.state.facts["emergency_screen"]
        self.assertEqual(fact.assertion, Assertion.UNKNOWN)
        self.assertIsNone(fact.value)

    def test_caregiver_answers_are_tagged_with_caregiver_source(self):
        self.answer("孩子", "child")
        self.answer("8个月")
        self.answer("发烧")
        self.assertEqual(self.state.facts["age_years"].source, Source.CAREGIVER)
        self.assertEqual(self.state.facts["chief_complaint"].source, Source.CAREGIVER)

    def test_respiratory_path_offers_self_check_before_background(self):
        self.establish_adult_complaint()
        answers = [
            ("都没有", "no"),
            ("两天前逐渐出现", None),
            ("变化不大", "stable"),
            ("5分", None),
            ("喉咙痛", None),
            ("没有", None),
            ("干咳，没有咳血", None),
            ("能正常喝水", None),
        ]
        for text, choice in answers:
            reply = self.answer(text, choice)
        self.assertEqual(reply.kind, "self_check")
        self.assertEqual(self.state.active_task, "axillary_temperature")
        self.assertNotIn("medications_taken", self.state.facts)

    def test_skipped_self_check_is_unknown_and_not_done(self):
        self.test_respiratory_path_offers_self_check_before_background()
        reply = self.answer("暂时跳过", "skip")
        fact = self.state.facts["temperature_c"]
        self.assertEqual(fact.assertion, Assertion.UNKNOWN)
        self.assertEqual(fact.quality, ObservationQuality.NOT_DONE)
        self.assertEqual(reply.question_id, "medications_taken")

    def test_high_measured_temperature_changes_route(self):
        self.test_respiratory_path_offers_self_check_before_background()
        self.answer("40.1℃")
        self.assertEqual(self.state.risk.level, RiskLevel.URGENT)
        self.assertIn("今天", self.engine._finish(self.state).summary["care_route"])

    def test_fact_history_preserves_superseded_values(self):
        self.answer("我自己", "self")
        first = self.state.facts["consult_for"]
        from medagent.interpreter import Interpretation

        self.engine._record_fact(
            self.state,
            "consult_for",
            Interpretation("child", Assertion.PRESENT, Source.CAREGIVER),
            "correction-turn",
        )
        latest = self.state.facts["consult_for"]
        self.assertEqual(latest.supersedes, first.fact_id)
        self.assertEqual(
            len([item for item in self.state.history if item.key == "consult_for"]), 2
        )

    def test_public_correction_replans_and_preserves_history(self):
        self.establish_adult_complaint("咳嗽发烧两天")
        old = self.state.facts["chief_complaint"]
        reply = self.engine.correct_fact(
            self.state.session_id, "chief_complaint", "腹痛两小时"
        )
        current = self.state.facts["chief_complaint"]
        self.assertEqual(current.value, "腹痛两小时")
        self.assertEqual(current.supersedes, old.fact_id)
        self.assertEqual(reply.question_id, "emergency_screen")

    def test_correction_rejects_invalid_age_without_overwrite(self):
        self.answer("我自己", "self")
        self.answer("32岁")
        with self.assertRaises(ValueError):
            self.engine.correct_fact(self.state.session_id, "age_years", "年龄不详")
        self.assertEqual(self.state.facts["age_years"].value, 32)

    def test_stale_answer_cannot_be_written_into_next_question(self):
        first_action = self.reply.action_id
        self.engine.submit(
            self.state.session_id, UserInput("我自己", "self", first_action)
        )
        with self.assertRaises(StaleActionError):
            self.engine.submit(
                self.state.session_id, UserInput("重复的旧答案", None, first_action)
            )
        self.assertNotIn("age_years", self.state.facts)
        self.assertEqual(
            self.events.for_session(self.state.session_id)[-1].event_type,
            "turn.rejected",
        )

    def test_complete_generic_consultation_produces_auditable_summary(self):
        answers = {
            "consult_for": ("我自己", "self"),
            "age": ("32岁", None),
            "chief_complaint": ("腹痛两小时", None),
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
        reply = self.reply
        while reply.kind not in {"summary", "emergency"}:
            if reply.kind == "self_check":
                reply = self.answer("暂时跳过", "skip")
                continue
            text, choice = answers[reply.question_id]
            reply = self.answer(text, choice)
        self.assertEqual(reply.kind, "summary")
        self.assertIn("过敏信息", reply.summary["unknown"])
        screen = next(
            item for item in reply.summary["known"] if item["key"] == "emergency_screen"
        )
        self.assertEqual(screen["value"], "患者否认上述危险信号")
        self.assertEqual(self.state.phase, Phase.SUMMARY)
        self.assertIsNone(self.state.closed_reason)
        previous_action = reply.action_id
        changed = self.engine.correct_fact(self.state.session_id, "allergies", "青霉素过敏")
        self.assertEqual(changed.kind, "summary")
        self.assertNotEqual(changed.action_id, previous_action)
        with self.assertRaises(StaleActionError):
            self.engine.submit(
                self.state.session_id,
                UserInput("摘要准确", "confirm_summary", previous_action),
            )
        final = self.engine.submit(
            self.state.session_id,
            UserInput("摘要准确", "confirm_summary", changed.action_id),
        )
        self.assertEqual(final.kind, "closed")
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")


if __name__ == "__main__":
    unittest.main()
