"""Measurement feedback is a full patient utterance, not only a number."""
import json
import unittest

from medagent.consultation import ConsultationEngine, UserInput
from medagent.domain import Assertion, ObservationQuality, Phase
from medagent.extensions.builtin import observation
from medagent.interpreter import ObservationInputError
from medagent.measurement_reports import temperature_report
from tests.test_agent import cmd, fact
from tests.test_repeated_observations import measured_case, repeat, OPENING


REPORT = "腋温37.6℃；更正：昨晚用过药物甲，用量不清楚。"
MEDICATION = "昨晚用过药物甲，用量不清楚"


def medication_commands(summary=True):
    return [
        cmd(
            "record_facts",
            facts=[fact("medications_taken", MEDICATION, MEDICATION, correction=True)],
        ),
        cmd("review_summary")
        if summary
        else cmd("ask_question", question_id="trajectory"),
    ]


class MeasurementParserTests(unittest.TestCase):
    def test_separate_explicit_reading_from_other_numbers_without_choosing_between_readings(
        self,
    ):
        value = observation("腋温37.6℃，昨晚用药物甲2片", None, "temperature")
        self.assertEqual(value.value, 37.6)
        self.assertTrue(temperature_report("腋温37.6℃，昨晚用药物甲2片").has_supplement)
        for text in [
            "38.6还是39.2",
            "腋温38.6℃还是39.2",
            "腋温38.6℃，39.2℃",
            "腋温38.6℉",
            "体温380，过敏不知道",
            "年龄38.6岁",
            "过敏不知道，昨晚用过药物甲",
        ]:
            with self.subTest(text=text), self.assertRaises(ObservationInputError):
                observation(text, None, "temperature")

    def test_adjacent_measurement_qualifiers_are_not_dropped_or_applied_to_other_fields(
        self,
    ):
        for text in [
            "腋温38.6℃，读数可能不准",
            "腋温38.6℃，这个读数我不确定",
            "不太确定，腋温38.6℃",
            "腋温38.6℃，没看清",
            "腋温38.6℃但读数没看清",
        ]:
            with self.subTest(text=text):
                value = observation(text, None, "temperature")
                self.assertEqual(
                    (value.assertion, value.quality),
                    (Assertion.UNKNOWN, ObservationQuality.INADEQUATE),
                )
        for text in ["腋温37.6℃，过敏不知道", "用药不确定，腋温37.6℃", "腋温37.6℃，不知道有没有其他过敏"]:
            with self.subTest(text=text):
                self.assertEqual(observation(text, None, "temperature").value, 37.6)

    def test_only_real_unambiguous_measurements_pass_and_common_simple_reports_keep_zero_model_path(
        self,
    ):
        for text in [
            "38.25",
            "38.25℃",
            "腋温38.25℃",
            "我刚刚又测了一次腋温40.1℃，想重新测量。",
            "可能是38.6℃，读数没看清",
        ]:
            self.assertFalse(temperature_report(text).has_supplement, text)
        with self.assertRaises(ObservationInputError):
            observation("如果腋温40.1℃", None, "temperature")
        self.assertTrue(temperature_report("体温不知道，昨晚用过药物甲").has_supplement)


class MeasurementSupplementTests(unittest.TestCase):
    def test_request_with_history_but_no_reading_processes_history_before_offering_repeat(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        backend.commands = medication_commands()
        reply = engine.submit(state.session_id, UserInput("想重新测量；更正：" + MEDICATION))
        self.assertEqual(reply.kind, "self_check")
        self.assertEqual(state.get("medications_taken"), MEDICATION)
        self.assertEqual(state.get("temperature_c"), 38.25)
        self.assertEqual(state.pending_self_checks, [])
        before = len(backend.calls)
        reply = engine.submit(
            state.session_id, UserInput("37.6℃", expected_action_id=reply.action_id)
        )
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(len(backend.calls), before)

    def test_candidate_after_another_number_keeps_both_sources_and_does_not_drop_the_first_clause(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        text = "更正：昨晚用药物甲2片；刚刚又测了一次腋温37.6℃，想重新测量。"
        offered = engine.submit(state.session_id, UserInput(text))
        self.assertEqual(offered.task["repeat"]["candidate"]["value"], 37.6)
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("medications_taken", "昨晚用药物甲2片", "昨晚用药物甲2片", correction=True)
                ],
            ),
            cmd("review_summary"),
        ]
        reply = engine.submit(
            state.session_id,
            UserInput(
                "核对并记录37.6℃",
                offered.task["repeat"]["candidate"]["choice"],
                offered.action_id,
            ),
        )
        self.assertEqual(state.get("medications_taken"), "昨晚用药物甲2片")
        self.assertEqual(state.get("temperature_c"), 37.6)
        self.assertEqual(
            state.facts["medications_taken"].turn_id,
            state.facts["temperature_c"].turn_id,
        )
        self.assertEqual(reply.kind, "summary")

    def test_repeat_handles_same_turn_medication_correction_and_preserves_full_source(
        self,
    ):
        engine, backend, state, prior = measured_case(summary=True)
        offered = repeat(engine, state, prior)
        backend.commands = medication_commands()
        before = len(backend.calls)
        reply = engine.submit(
            state.session_id, UserInput(REPORT, expected_action_id=offered.action_id)
        )
        self.assertEqual(state.get("temperature_c"), 37.6)
        self.assertEqual(state.get("medications_taken"), MEDICATION)
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(len(backend.calls) - before, 2)
        measure = state.facts["temperature_c"]
        medication = state.facts["medications_taken"]
        self.assertEqual(measure.turn_id, medication.turn_id)
        self.assertEqual(measure.evidence, "腋温37.6℃")
        context = engine._fact_provenance(measure, engine._source_turns(state))[
            "context"
        ]
        self.assertEqual(context["text"], REPORT)
        self.assertEqual(
            reply.summary["observations"][0]["supplement"],
            {"text": REPORT, "status": "forwarded"},
        )
        self.assertIsNone(state.observation_feedback)

    def test_failed_supplement_retry_does_not_measure_twice_or_reenter_the_consumed_request(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        request = "我刚刚又测了一次腋温37.6℃，想重新测量；更正：昨晚用过药物甲，用量不清楚。"
        offered = engine.submit(state.session_id, UserInput(request))
        # No scripted response intentionally simulates a backend failure, not a parsed unknown.
        backend.commands = []
        failed = engine.submit(
            state.session_id,
            UserInput(
                "核对并记录37.6℃",
                offered.task["repeat"]["candidate"]["choice"],
                offered.action_id,
            ),
        )
        self.assertEqual(failed.kind, "service_error")
        self.assertEqual(failed.observation_result["supplement"]["status"], "pending")
        self.assertEqual(state.get("temperature_c"), 37.6)
        old_id = state.facts["temperature_c"].fact_id
        backend.commands = [
            cmd("repeat_self_check", task_id="axillary_temperature", evidence="想重新测量"),
            *medication_commands(),
        ]
        reply = engine.submit(
            state.session_id, UserInput("重试这一轮", "retry_turn", failed.action_id)
        )
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(state.get("medications_taken"), MEDICATION)
        self.assertEqual(state.facts["temperature_c"].fact_id, old_id)
        self.assertEqual(len(state.observation_attempts), 1)
        self.assertEqual(len([f for f in state.history if f.key == "temperature_c"]), 2)
        self.assertEqual(
            state.facts["medications_taken"].turn_id,
            state.facts["temperature_c"].turn_id,
        )
        rejections = [
            e
            for e in engine.events.for_session(state.session_id)
            if e.event_type == "tool.completed"
            and e.payload.get("result", {}).get("ok") is False
        ]
        self.assertTrue(
            any("已处理" in json.dumps(e.payload, ensure_ascii=False) for e in rejections)
        )

    def test_uncertain_repeat_keeps_old_reading_and_still_processes_the_medication(
        self,
    ):
        engine, backend, state, prior = measured_case(summary=True)
        old = state.facts["temperature_c"]
        offered = repeat(engine, state, prior)
        backend.commands = medication_commands()
        reply = engine.submit(
            state.session_id,
            UserInput(
                "腋温38.6℃，读数可能不准；更正：" + MEDICATION, expected_action_id=offered.action_id
            ),
        )
        self.assertIs(state.facts["temperature_c"], old)
        self.assertEqual(state.observation_attempts[-1]["status"], "unconfirmed")
        self.assertEqual(state.get("medications_taken"), MEDICATION)
        self.assertEqual(reply.kind, "summary")

    def test_first_measurement_forwards_full_report_and_does_not_mistake_dose_for_temperature(
        self,
    ):
        engine, backend, state, _ = measured_case()
        # Replay the same real registered task on a fresh case with its actual opening.
        state, _ = engine.create_session()
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("sex", "male", "男"),
                    fact("age_years", 32, "32岁"),
                    fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
                    fact("onset", "两天", "两天"),
                    fact("emergency_screen", None, "上述危险信号都没有", "absent"),
                ],
            )
        ]
        offered = engine.submit(state.session_id, UserInput(OPENING))
        report = "腋温37.6℃，昨晚用药物甲2片"
        backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("medications_taken", "昨晚用药物甲2片", "昨晚用药物甲2片")],
            ),
            cmd("ask_question", question_id="trajectory"),
        ]
        reply = engine.submit(
            state.session_id, UserInput(report, expected_action_id=offered.action_id)
        )
        self.assertEqual(
            (state.get("temperature_c"), state.get("medications_taken")),
            (37.6, "昨晚用药物甲2片"),
        )
        self.assertEqual(reply.question_id, "trajectory")
        self.assertEqual(reply.observation_result["supplement"]["text"], report)

    def test_urgent_reading_and_raw_emergency_never_wait_for_supplement_planning(self):
        for text, kind in [
            ("腋温40.1℃；更正：" + MEDICATION, "urgent"),
            ("腋温37.6℃，嘴唇发紫", "emergency"),
        ]:
            with self.subTest(text=text):
                engine, backend, state, prior = measured_case()
                offered = repeat(engine, state, prior)
                before = len(backend.calls)
                reply = engine.submit(
                    state.session_id,
                    UserInput(text, expected_action_id=offered.action_id),
                )
                self.assertEqual(reply.kind, kind)
                self.assertEqual(len(backend.calls), before)
                self.assertEqual(state.phase, Phase.CLOSED)

    def test_supplement_cancels_a_suspended_form_instead_of_trapping_the_next_answer(
        self,
    ):
        engine, backend, state, _ = measured_case()
        backend.commands = [
            cmd(
                "run_capability",
                id="calculate.units",
                input={"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                evidence="请把1.25kg换算成g",
            )
        ]
        form = engine.submit(state.session_id, UserInput("请把1.25kg换算成g"))
        offered = repeat(engine, state, form)
        backend.commands = medication_commands(summary=False)
        reply = engine.submit(
            state.session_id, UserInput(REPORT, expected_action_id=offered.action_id)
        )
        self.assertEqual(reply.question_id, "trajectory")
        self.assertIsNone(state.pending_calculation)
        self.assertIsNone(state.calculation_resume)
        self.assertEqual(state.calculations, [])

    def test_demo_preserves_the_supplement_without_claiming_model_processing(self):
        original, _, state, prior = measured_case()
        engine = ConsultationEngine(
            original.content,
            original.triage,
            original.self_checks,
            original.events,
            original.ranker,
        )
        for name in (
            "_sessions",
            "_session_locks",
            "_session_conditions",
            "_latest_replies",
        ):
            setattr(engine, name, getattr(original, name))
        offered = repeat(engine, state, prior)
        reply = engine.submit(
            state.session_id, UserInput(REPORT, expected_action_id=offered.action_id)
        )
        self.assertEqual(
            reply.observation_result["supplement"]["status"], "unprocessed"
        )
        self.assertNotIn("medications_taken", state.facts)
        self.assertIsNone(state.observation_feedback)


if __name__ == "__main__":
    unittest.main()
