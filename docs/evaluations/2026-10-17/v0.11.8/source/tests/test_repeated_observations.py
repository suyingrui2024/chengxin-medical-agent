"""Real controller transitions, source identity and observation interruption boundaries."""
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import ConsultationEngine, StaleActionError, UserInput
from medagent.domain import Assertion, ObservationQuality, Phase, Source
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


OPENING = "我自己32岁，咳嗽发烧两天；上述危险信号都没有；想按图测一下腋温。"
DETAILS = (
    "总体变化不大；不适程度3分；没有其他不适；没有气促，能完整说话；干咳，没有咳血；能正常喝水吃饭，排尿正常；没有用药；过敏不知道；没有相关既往病史；想整理信息去就医。"
)
ROWS = [
    ("trajectory", "stable", "变化不大", "present"),
    ("severity", 3, "不适程度3分", "present"),
    ("associated_symptoms", None, "没有其他不适", "absent"),
    ("resp_breathing", "没有气促，能完整说话", "没有气促，能完整说话", "present"),
    ("resp_cough_details", "干咳，没有咳血", "干咳，没有咳血", "present"),
    ("resp_intake", "能正常喝水吃饭，排尿正常", "能正常喝水吃饭，排尿正常", "present"),
    ("medications_taken", None, "没有用药", "absent"),
    ("allergies", None, "过敏不知道", "unknown"),
    ("relevant_history", None, "没有相关既往病史", "absent"),
    ("patient_goal", "整理信息去就医", "想整理信息去就医", "present"),
]


def measured_case(summary=False, caregiver=False):
    backend = ScriptBackend()
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend,
    )
    state, _ = engine.create_session()
    subject, age, text = "self", 32, OPENING
    if caregiver:
        subject, age, text = "other_adult", 67, OPENING.replace("我自己32岁", "给母亲咨询，她67岁")
    backend.commands = [
        cmd(
            "record_facts",
            facts=[
                fact("consult_for", subject, "给母亲咨询" if caregiver else "我自己"),
                fact("age_years", age, f"{age}岁"),
                fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
                fact("onset", "两天", "两天"),
                fact("emergency_screen", None, "上述危险信号都没有", "absent"),
            ],
        ),
        cmd("show_self_check", task_id="axillary_temperature"),
    ]
    reply = engine.submit(state.session_id, UserInput(text))
    assert reply.kind == "self_check", reply.kind
    backend.commands = [cmd("ask_question", question_id="trajectory")]
    reply = engine.submit(state.session_id, UserInput("腋温38.25℃"))
    assert state.facts["temperature_c"].value == 38.25
    if summary:
        backend.commands = [
            cmd("record_facts", facts=[fact(k, v, e, a) for k, v, e, a in ROWS]),
            cmd("review_summary"),
        ]
        reply = engine.submit(state.session_id, UserInput(DETAILS))
        assert reply.kind == "summary", reply.kind
    return engine, backend, state, reply


def repeat(engine, state, reply):
    return engine.submit(
        state.session_id,
        UserInput("再测一次腋温", "repeat_self_check:axillary_temperature", reply.action_id),
    )


class RepeatedObservationTests(unittest.TestCase):
    def test_field_correction_cancels_repeat_and_cannot_restore_an_old_candidate(self):
        engine, _, state, _ = measured_case(summary=True)
        offered = engine.submit(state.session_id, UserInput("刚刚又测了一次腋温38.6℃"))
        choice = offered.task["repeat"]["candidate"]["choice"]
        corrected = engine.correct_fact(
            state.session_id, "age_years", "40岁", state.facts["age_years"].fact_id
        )
        self.assertIsNone(state.observation_resume)
        self.assertIsNone(state.active_task)
        self.assertEqual(state.observation_attempts[-1]["status"], "cancelled")
        self.assertEqual(state.get("temperature_c"), 38.25)
        with self.assertRaises(StaleActionError):
            engine.submit(
                state.session_id, UserInput("确认", choice, corrected.action_id)
            )

    def test_completed_calculation_survives_observation_without_duplicate_execution(
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
        result = engine.submit(
            state.session_id,
            UserInput(
                "确认参数",
                "confirm_calculation",
                form.action_id,
                {
                    "proposal_id": form.calculation_input["proposal_id"],
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )
        calls = len(backend.calls)
        offered = repeat(engine, state, result)
        restored = engine.submit(
            state.session_id, UserInput("37.6℃", expected_action_id=offered.action_id)
        )
        self.assertEqual(restored.kind, "calculation_result")
        self.assertEqual(
            restored.calculation_result["execution_id"],
            result.calculation_result["execution_id"],
        )
        self.assertEqual(len(state.calculations), 1)
        resumed = engine.submit(
            state.session_id,
            UserInput(
                "继续问诊",
                "resume_after_calculation",
                restored.action_id,
                {"execution_id": result.calculation_result["execution_id"]},
            ),
        )
        self.assertEqual(resumed.question_id, "trajectory")
        self.assertEqual(len(backend.calls), calls)

    def test_hypothetical_input_during_active_repeat_is_not_recorded(self):
        engine, backend, state, reply = measured_case()
        old = state.facts["temperature_c"]
        offered = repeat(engine, state, reply)
        failure = engine.submit(
            state.session_id,
            UserInput("如果测到40.1℃", expected_action_id=offered.action_id),
        )
        self.assertTrue(failure.input_error)
        self.assertIs(state.facts["temperature_c"], old)
        self.assertEqual(state.phase, Phase.OBSERVATION)

    def test_new_reading_supersedes_and_restores_the_unanswered_question_without_a_model(
        self,
    ):
        engine, backend, state, prior = measured_case(caregiver=True)
        old, calls = state.facts["temperature_c"], len(backend.calls)
        offered = repeat(engine, state, prior)
        self.assertEqual(offered.task["repeat"]["previous"]["fact_id"], old.fact_id)
        self.assertEqual(state.facts["temperature_c"].fact_id, old.fact_id)
        resumed = engine.submit(
            state.session_id, UserInput("腋温37.6℃", expected_action_id=offered.action_id)
        )
        new = state.facts["temperature_c"]
        self.assertEqual(
            (new.value, new.supersedes, new.source, new.quality),
            (37.6, old.fact_id, Source.CAREGIVER, ObservationQuality.NOT_ASSESSED),
        )
        self.assertEqual(
            (resumed.question_id, resumed.message), (prior.question_id, prior.message)
        )
        self.assertEqual(len(backend.calls), calls)
        self.assertEqual(
            [e["value"] for e in resumed.observation_result["entries"]], [38.25, 37.6]
        )

    def test_invalid_retry_and_skip_preserve_old_fact_and_discard_previous_candidate(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        old, calls = state.facts["temperature_c"], len(backend.calls)
        offered = engine.submit(state.session_id, UserInput("刚刚又测了一次腋温38.6℃"))
        candidate_choice = offered.task["repeat"]["candidate"]["choice"]
        failed = engine.submit(
            state.session_id,
            UserInput("38.6还是39.2", expected_action_id=offered.action_id),
        )
        self.assertTrue(failed.input_error)
        self.assertNotIn("candidate", failed.task["repeat"])
        with self.assertRaises(StaleActionError):
            engine.submit(
                state.session_id, UserInput("确认", candidate_choice, failed.action_id)
            )
        resumed = engine.submit(
            state.session_id, UserInput("暂时跳过", "skip", failed.action_id)
        )
        self.assertIs(state.facts["temperature_c"], old)
        self.assertEqual(resumed.summary["observations"][0]["latest_status"], "skipped")
        self.assertEqual(len([f for f in state.history if f.key == "temperature_c"]), 1)
        self.assertEqual(len(backend.calls), calls)

    def test_uncertain_number_does_not_become_a_measurement_or_erase_previous_value(
        self,
    ):
        engine, backend, state, prior = measured_case()
        old = state.facts["temperature_c"]
        repeat(engine, state, prior)
        reply = engine.submit(state.session_id, UserInput("可能是38.6℃，读数没看清"))
        self.assertIs(state.facts["temperature_c"], old)
        self.assertEqual(reply.observation_result["latest_status"], "unconfirmed")
        self.assertEqual(state.observation_attempts[-1]["quality"], "inadequate")
        # An unrelated unknown allergy does not make a clearly reported reading unknown.
        repeat(engine, state, reply)
        backend.commands = [
            cmd("record_facts", facts=[fact("allergies", None, "过敏不知道", "unknown")]),
            cmd("ask_question", question_id="trajectory"),
        ]
        reply = engine.submit(state.session_id, UserInput("腋温37.8℃，过敏不知道"))
        self.assertEqual(state.get("temperature_c"), 37.8)
        self.assertEqual(state.facts["allergies"].assertion, Assertion.UNKNOWN)
        self.assertEqual(reply.kind, "question")

    def test_reported_high_candidate_requires_confirmation_then_existing_triage_runs_without_a_model(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        calls = len(backend.calls)
        text = "我刚刚又测了一次腋温40.1℃，想重新测量。"
        offered = engine.submit(state.session_id, UserInput(text))
        self.assertEqual(state.get("temperature_c"), 38.25)
        source = next(
            e
            for e in reversed(engine.events.for_session(state.session_id))
            if e.event_type == "turn.user_message"
        )
        candidate = offered.task["repeat"]["candidate"]
        reply = engine.submit(
            state.session_id,
            UserInput("核对并记录40.1℃", candidate["choice"], offered.action_id),
        )
        current = state.facts["temperature_c"]
        self.assertEqual(
            (current.value, current.turn_id, current.evidence),
            (40.1, source.turn_id, text),
        )
        self.assertEqual(reply.kind, "urgent")
        self.assertEqual(state.phase, Phase.CLOSED)
        self.assertFalse(reply.summary["observations"][0]["can_repeat"])
        self.assertIsNone(state.observation_resume)
        self.assertEqual(len(backend.calls), calls)

    def test_repeat_actions_require_current_version_before_any_mutation_and_do_not_reopen_closed_cases(
        self,
    ):
        engine, _, state, prior = measured_case()
        before = len(engine.events.for_session(state.session_id))
        for action in (None, "old-page"):
            with self.assertRaises(StaleActionError):
                engine.submit(
                    state.session_id,
                    UserInput("再测一次", "repeat_self_check:axillary_temperature", action),
                )
        self.assertEqual(len(engine.events.for_session(state.session_id)), before)
        offered = repeat(engine, state, prior)
        emergency = engine.submit(
            state.session_id, UserInput("嘴唇发紫", "skip", "old-page")
        )
        self.assertEqual(emergency.kind, "emergency")
        self.assertEqual(state.observation_attempts[-1]["status"], "cancelled")
        self.assertIsNone(state.observation_resume)
        with self.assertRaises(StaleActionError):
            repeat(engine, state, emergency)
        self.assertEqual(state.get("temperature_c"), 38.25)

    def test_repeat_interrupts_and_restores_calculation_then_summary_rebuilds_current_measurement(
        self,
    ):
        engine, backend, state, _ = measured_case(summary=True)
        backend.commands = [
            cmd(
                "run_capability",
                id="calculate.units",
                input={"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                evidence="请把1.25kg换算成g",
            )
        ]
        form = engine.submit(state.session_id, UserInput("请把1.25kg换算成g"))
        calls, proposal_id = len(backend.calls), form.calculation_input["proposal_id"]
        offered = repeat(engine, state, form)
        resumed = engine.submit(
            state.session_id, UserInput("腋温37.7℃", expected_action_id=offered.action_id)
        )
        self.assertEqual(resumed.calculation_input["proposal_id"], proposal_id)
        result = engine.submit(
            state.session_id,
            UserInput(
                "确认参数",
                "confirm_calculation",
                resumed.action_id,
                {
                    "proposal_id": proposal_id,
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                },
            ),
        )
        summary = engine.submit(
            state.session_id,
            UserInput(
                "继续问诊",
                "resume_after_calculation",
                result.action_id,
                {"execution_id": result.calculation_result["execution_id"]},
            ),
        )
        self.assertEqual(
            summary.summary["observations"][0]["entries"][-1]["value"], 37.7
        )
        self.assertEqual(len(backend.calls), calls)

    def test_literal_repeat_does_not_execute_negated_hypothetical_or_unrelated_requests(
        self,
    ):
        engine, _, state, _ = measured_case()
        for text in (
            "如果想重新测量腋温40.1℃呢",
            "不想重新测量",
            "血糖40.1，是否重新测量",
            "再测一次血压",
            "比如刚刚又测了一次腋温40.1℃",
        ):
            self.assertIsNone(
                engine._handle_observation_action(
                    state, UserInput(text), text, "test-turn"
                )
            )
        for text in ("想重新测量，血糖40.1", "想重新测量，腋温可能是38.6℃"):
            engine, _, state, _ = measured_case()
            reply = engine._handle_observation_action(
                state, UserInput(text), text, "test-turn"
            )
            if text == "想重新测量，血糖40.1":
                self.assertIsNone(reply)
                self.assertTrue(state.pending_self_checks[0]["repeat"])
                self.assertEqual(state.pending_self_checks[0]["evidence"], text)
                self.assertEqual(state.get("temperature_c"), 38.25)
                continue
            self.assertIsNotNone(reply)
            self.assertNotIn("candidate", reply.task["repeat"])
            engine._handle_observation_action(
                state, UserInput("跳过", "skip"), "跳过", "skip-turn"
            )

    def test_model_tool_is_registered_but_requires_actual_request_and_task_eligibility(
        self,
    ):
        engine, _, state, _ = measured_case()
        for identity, evidence, text in (
            ("body_location", "想重新测量", "想重新测量"),
            ("axillary_temperature", "想重新测量", "没有说过"),
        ):
            with self.assertRaises(ValueError):
                engine._execute(
                    state,
                    cmd("repeat_self_check", task_id=identity, evidence=evidence),
                    text,
                    "test-turn",
                )
        reply = engine._execute(
            state,
            cmd("repeat_self_check", task_id="axillary_temperature", evidence="想重新测量"),
            "想重新测量",
            "test-turn",
        )
        self.assertEqual(reply.kind, "self_check")

    def test_history_coverage_keeps_retained_value_visible_after_many_skips(self):
        engine, backend, state, reply = measured_case(summary=True)
        calls = len(backend.calls)
        for _ in range(10):
            offered = repeat(engine, state, reply)
            reply = engine.submit(
                state.session_id, UserInput("暂时跳过", "skip", offered.action_id)
            )
        series = reply.summary["observations"][0]
        self.assertEqual((series["total"], len(series["entries"])), (11, 8))
        self.assertEqual(
            [e["value"] for e in series["entries"] if e["current"]], [38.25]
        )
        self.assertEqual(len(backend.calls), calls)

    def test_demo_engine_uses_the_same_repeat_controller(self):
        original, _, state, reply = measured_case()
        engine = ConsultationEngine(
            original.content,
            original.triage,
            original.self_checks,
            original.events,
            original.ranker,
        )
        engine._sessions = original._sessions
        engine._session_locks = original._session_locks
        engine._session_conditions = original._session_conditions
        engine._latest_replies = original._latest_replies
        offered = repeat(engine, state, reply)
        resumed = engine.submit(
            state.session_id, UserInput("37.9℃", expected_action_id=offered.action_id)
        )
        self.assertEqual(state.get("temperature_c"), 37.9)
        self.assertEqual(resumed.question_id, reply.question_id)


if __name__ == "__main__":
    unittest.main()
