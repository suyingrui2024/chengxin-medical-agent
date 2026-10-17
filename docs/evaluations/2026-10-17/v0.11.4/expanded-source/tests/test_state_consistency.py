"""Regression cases for evidence ownership, corrections and stale clients."""
import json
import threading
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import (
    ConsultationEngine,
    StaleActionError,
    SubjectBoundaryError,
    UserInput,
)
from medagent.domain import Assertion, Fact, Phase, RiskLevel, Source
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class PlannerFixture:
    name = "state-regression-fixture"

    def __init__(self):
        self.calls = []
        self.commands = []

    def decide(self, sid, messages):
        self.calls.append(messages)
        if self.commands:
            return self.commands.pop(0)
        context = json.loads(messages[-1]["content"])
        context = context.get("tool_result", {}).get("case", context)
        return {
            "tool": "ask_question",
            "arguments": {"question_id": context["questions"][0]["id"]},
        }


class StateConsistencyTests(unittest.TestCase):
    def engines(self):
        for kind in (ConsultationEngine, AdaptiveConsultationEngine):
            content, events, backend = (
                ContentRepository(),
                EventStore(),
                PlannerFixture(),
            )
            kwargs = dict(
                content=content,
                events=events,
                triage=TriageService(),
                self_checks=SelfCheckService(content.registry),
                ranker=OrderedQuestionRanker(),
            )
            if kind is AdaptiveConsultationEngine:
                kwargs["backend"] = backend
            engine = kind(**kwargs)
            state, opening = engine.create_session()
            yield engine, state, opening, backend

    @staticmethod
    def record(state, key, value, assertion=Assertion.PRESENT):
        state.record(
            Fact(
                key,
                value,
                assertion,
                Source.PATIENT,
                "synthetic-fixture",
                evidence=str(value),
            )
        )

    def establish(self, state):
        self.record(state, "consult_for", "self")
        self.record(state, "age_years", 32)
        self.record(state, "chief_complaint", "发烧")
        self.record(state, "emergency_screen", None, Assertion.ABSENT)

    def test_age_correction_keeps_reading_and_recomputes_existing_risk(self):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                self.record(state, "temperature_c", 38.2)
                reading = state.facts["temperature_c"]
                reply = engine.correct_fact(state.session_id, "age_years", "2个月")
                self.assertIs(state.facts["temperature_c"], reading)
                self.assertEqual(state.risk.level, RiskLevel.URGENT)
                self.assertEqual(reply.kind, "urgent")
                self.assertEqual(backend.calls, [])

    def test_chief_correction_invalidates_episode_but_keeps_background_and_history(
        self,
    ):
        for engine, state, _, _ in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                for key, value in {
                    "onset": "两天",
                    "medications_taken": "已吃药",
                    "temperature_c": 38.2,
                    "severity": 3,
                    "allergies": "青霉素过敏",
                    "relevant_history": "既往病史",
                }.items():
                    self.record(state, key, value)
                original = dict(state.facts)
                engine.correct_fact(state.session_id, "chief_complaint", "头痛")
                for key in (
                    "onset",
                    "medications_taken",
                    "severity",
                    "emergency_screen",
                ):
                    self.assertNotIn(key, state.facts)
                    self.assertIn(original[key], state.history)
                self.assertIs(state.facts["allergies"], original["allergies"])
                self.assertIs(state.facts["age_years"], original["age_years"])
                self.assertIs(state.facts["temperature_c"], original["temperature_c"])
                invalidated = [
                    e
                    for e in engine.events.for_session(state.session_id)
                    if e.event_type == "fact.invalidated"
                ]
                self.assertTrue(
                    all(
                        e.payload["because"] == "chief_complaint_corrected"
                        for e in invalidated
                    )
                )

    def test_ui_correction_revalidates_inflight_task_without_a_model_or_fake_result(
        self,
    ):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                engine._offer_task(state, "发烧")
                state.pending_input = "旧的失败输入"
                engine.correct_fact(state.session_id, "age_years", "40岁")
                self.assertEqual(state.active_task, "axillary_temperature")
                self.assertNotIn("temperature_c", state.facts)
                self.assertEqual(backend.calls, [])
                self.assertIsNone(state.pending_input)
                self.assertTrue(
                    any(
                        e.event_type == "self_check.cancelled"
                        for e in engine.events.for_session(state.session_id)
                    )
                )

    def test_stale_or_invalidated_fact_cannot_be_overwritten(self):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                old_id = state.facts["age_years"].fact_id
                engine.correct_fact(state.session_id, "age_years", "40岁", old_id)
                calls = len(backend.calls)
                with self.assertRaises(StaleActionError):
                    engine.correct_fact(state.session_id, "age_years", "50岁", old_id)
                with self.assertRaises(StaleActionError):
                    engine.correct_fact(state.session_id, "onset", "一天", "removed-fact")
                self.assertEqual(state.get("age_years"), 40)
                self.assertEqual(len(backend.calls), calls)

    def test_stale_correction_with_emergency_text_still_routes_without_a_model(self):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                reply = engine.correct_fact(
                    state.session_id, "age_years", "现在叫不醒", "old-version"
                )
                self.assertEqual(reply.kind, "emergency")
                self.assertEqual(state.get("age_years"), 32)
                self.assertEqual(backend.calls, [])

    def test_editing_age_cannot_retract_an_emergency_route(self):
        for engine, state, opening, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                engine.submit(
                    state.session_id,
                    UserInput("现在叫不醒", expected_action_id=opening.action_id),
                )
                reply = engine.correct_fact(state.session_id, "age_years", "40岁")
                self.assertEqual(reply.kind, "emergency")
                self.assertEqual(state.risk.level, RiskLevel.EMERGENCY)
                self.assertEqual(state.closed_reason, "emergency_route")
                self.assertEqual(backend.calls, [])

    def test_confirmed_subject_requires_a_new_case(self):
        for engine, state, _, _ in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                before = state.to_dict()
                with self.assertRaises(SubjectBoundaryError):
                    engine.correct_fact(state.session_id, "consult_for", "孩子")
                self.assertEqual(state.to_dict(), before)

    def test_concurrent_editors_cannot_both_commit_the_same_fact_version(self):
        for engine, state, _, _ in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                version = state.facts["age_years"].fact_id
                barrier, results = threading.Barrier(2), []

                def edit(value):
                    barrier.wait(timeout=3)
                    try:
                        engine.correct_fact(
                            state.session_id, "age_years", value, version
                        )
                        results.append("committed")
                    except StaleActionError:
                        results.append("stale")

                threads = [
                    threading.Thread(target=edit, args=(value,))
                    for value in ("40岁", "41岁")
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=3)
                    self.assertFalse(thread.is_alive())
                self.assertCountEqual(results, ["committed", "stale"])
                self.assertEqual(
                    len([f for f in state.history if f.key == "age_years"]), 2
                )

    def test_chief_correction_keeps_a_high_reading_and_urgent_route(self):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                self.record(state, "temperature_c", 40.1)
                reply = engine.correct_fact(state.session_id, "chief_complaint", "头痛")
                self.assertEqual(state.get("temperature_c"), 40.1)
                self.assertEqual(reply.kind, "urgent")
                self.assertEqual(backend.calls, [])

    def test_unknown_screen_never_offers_a_self_check_in_either_mode(self):
        for engine, state, _, _ in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                self.record(state, "emergency_screen", None, Assertion.UNKNOWN)
                self.assertIsNone(engine._offer_task(state, "发烧"))
                if isinstance(engine, AdaptiveConsultationEngine):
                    context = engine._context(state, "想测量")
                    self.assertEqual(context["available_self_checks"], [])
                    self.assertIn("未知不能当阴性", context["self_check_block_reason"])

    def test_malformed_temperature_is_not_recorded_and_the_task_remains(self):
        for engine, state, _, backend in self.engines():
            with self.subTest(engine=type(engine).__name__):
                self.establish(state)
                engine._offer_task(state, "发烧")
                for text in (
                    "380",
                    "38.2和39.1",
                    "-38.2",
                    "98.6°F",
                    "3.82e1",
                    "38,2",
                    "完成了",
                    "我38岁",
                    "血糖38.2mmol/L",
                    "体重38.2kg",
                    "38.2kg",
                    "38.2F",
                    "38.2%",
                    "发烧38天",
                ):
                    reply = engine.submit(state.session_id, UserInput(text))
                    self.assertEqual(reply.kind, "self_check", text)
                    self.assertEqual(state.active_task, "axillary_temperature")
                    self.assertNotIn("temperature_c", state.facts)
                self.assertEqual(backend.calls, [])
                engine.submit(state.session_id, UserInput("38.25℃"))
                self.assertEqual(state.get("temperature_c"), 38.25)
                self.assertEqual(
                    state.facts["temperature_c"].quality.value, "not_assessed"
                )

    def test_action_tokens_change_even_when_the_same_question_is_asked_again(self):
        for engine, state, opening, _ in self.engines():
            with self.subTest(engine=type(engine).__name__):
                question = engine.content.question(state.active_question)
                reply = engine._ask(state, question, "fixture-repeat")
                engine._record_reply(state, reply, "fixture", "repeat")
                self.assertNotEqual(opening.action_id, reply.action_id)
                with self.assertRaises(StaleActionError):
                    engine.submit(
                        state.session_id,
                        UserInput("延迟的旧回答", expected_action_id=opening.action_id),
                    )
                emergency = engine.submit(
                    state.session_id,
                    UserInput("现在叫不醒", expected_action_id=opening.action_id),
                )
                self.assertEqual(emergency.kind, "emergency")

    def test_model_batch_rejects_subject_switch_before_any_other_write(self):
        engine, state, opening, backend = list(self.engines())[1]
        self.establish(state)
        backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "age_years",
                            "value": 67,
                            "assertion": "present",
                            "evidence": "67岁",
                            "correction": True,
                        },
                        {
                            "key": "consult_for",
                            "value": "other_adult",
                            "assertion": "present",
                            "evidence": "母亲",
                            "correction": True,
                        },
                    ]
                },
            }
        ]
        reply = engine.submit(
            state.session_id,
            UserInput("换成母亲，67岁", expected_action_id=opening.action_id),
        )
        self.assertEqual(reply.kind, "subject_change")
        self.assertEqual(state.get("age_years"), 32)
        self.assertEqual(state.get("consult_for"), "self")
        self.assertEqual(len(backend.calls), 1)

    def test_new_evidence_in_chief_correction_batch_survives_invalidation(self):
        engine, state, opening, backend = list(self.engines())[1]
        self.establish(state)
        self.record(state, "onset", "两天")
        backend.commands = [
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        {
                            "key": "chief_complaint",
                            "value": "头痛",
                            "assertion": "present",
                            "evidence": "头痛",
                            "correction": True,
                        },
                        {
                            "key": "onset",
                            "value": "一小时",
                            "assertion": "present",
                            "evidence": "一小时",
                            "correction": True,
                        },
                    ]
                },
            }
        ]
        engine.submit(
            state.session_id,
            UserInput("更正为头痛一小时", expected_action_id=opening.action_id),
        )
        self.assertEqual(state.get("onset"), "一小时")
        self.assertNotIn("emergency_screen", state.facts)
        self.assertTrue(
            any(f.key == "onset" and f.value == "两天" for f in state.history)
        )


if __name__ == "__main__":
    unittest.main()
