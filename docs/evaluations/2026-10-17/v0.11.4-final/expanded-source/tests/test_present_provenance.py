"""Keep partial reports accessible in summaries and after the recent window ends."""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import Event, EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


RAW = "我自己，32岁。手臂不舒服，从昨天开始，变化不大，影响程度3分。没有其他不适；上述危险信号都没有。昨晚用过药物甲，用量不清楚。没有过敏；没有相关既往病史。这次想整理就医记录。"
ROWS = [
    ("consult_for", "self", "我自己", "present"),
    ("age_years", 32, "32岁", "present"),
    ("chief_complaint", "手臂不舒服", "手臂不舒服", "present"),
    ("onset", "昨天", "从昨天开始", "present"),
    ("trajectory", "stable", "变化不大", "present"),
    ("severity", 3, "影响程度3分", "present"),
    ("associated_symptoms", None, "没有其他不适", "absent"),
    ("emergency_screen", None, "上述危险信号都没有", "absent"),
    ("medications_taken", "用过药物甲", "昨晚用过药物甲", "present"),
    ("allergies", None, "没有过敏", "absent"),
    ("relevant_history", None, "没有相关既往病史", "absent"),
    ("patient_goal", "整理就医记录", "想整理就医记录", "present"),
]


def engine_with_backend():
    backend = ScriptBackend()
    return (
        AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=backend,
        ),
        backend,
    )


def complete_report(caregiver=False):
    engine, backend = engine_with_backend()
    rows, raw = list(ROWS), RAW
    if caregiver:
        rows[:2] = [
            ("consult_for", "other_adult", "给母亲咨询", "present"),
            ("age_years", 67, "67岁", "present"),
        ]
        raw = raw.replace("我自己，32岁", "给母亲咨询，她67岁")
    backend.commands = [
        cmd("record_facts", facts=[fact(k, v, e, a) for k, v, e, a in rows]),
        cmd("review_summary"),
    ]
    state, _ = engine.create_session()
    reply = engine.submit(state.session_id, UserInput(raw))
    assert reply.kind == "summary"
    return engine, backend, state, reply, raw


class PresentProvenanceTests(unittest.TestCase):
    def test_true_partial_report_keeps_its_quote_and_whole_source_without_inventing_a_dose(
        self,
    ):
        for caregiver in [False, True]:
            with self.subTest(caregiver=caregiver):
                engine, backend, state, reply, raw = complete_report(caregiver)
                item = next(
                    item
                    for item in reply.summary["known"]
                    if item["key"] == "medications_taken"
                )
                self.assertEqual(item["value"], "用过药物甲")
                self.assertEqual(item["evidence"], "昨晚用过药物甲")
                self.assertEqual(item["context"]["text"], raw)
                self.assertIn("用量不清楚", item["context"]["text"])
                self.assertEqual(item["context"]["scope"], "source_turn")
                self.assertEqual(
                    item["context"]["turn_id"], state.facts["medications_taken"].turn_id
                )
                self.assertEqual(
                    item["source"], "caregiver" if caregiver else "patient"
                )
                self.assertEqual(item["quality"], "not_assessed")
                self.assertEqual(
                    state.facts["medications_taken"].assertion, Assertion.PRESENT
                )
                self.assertEqual(len(backend.calls), 2)

    def test_old_sources_are_deduplicated_only_after_the_recent_window_and_do_not_revert_corrections(
        self,
    ):
        engine, _, state, _, raw = complete_report()
        recent = engine._context(state, "继续")
        self.assertNotIn("earlier_source_turns", recent)
        engine.correct_fact(state.session_id, "age_years", "40岁")
        for index in range(12):
            state.transcript += [
                {"role": "user", "content": f"后续补充 {index}"},
                {"role": "assistant", "content": "已收到"},
            ]
        context = engine._context(state, "继续核对")
        self.assertFalse(any(item["content"] == raw for item in context["transcript"]))
        self.assertEqual(len(context["earlier_source_turns"]), 1)
        source = context["earlier_source_turns"][0]
        self.assertEqual(source["text"], raw)
        self.assertEqual(
            context["facts"]["medications_taken"]["source_context_id"], source["id"]
        )
        self.assertIn("用量不清楚", source["text"])
        self.assertEqual(context["facts"]["age_years"]["value"], 40)
        self.assertNotIn("source_context_id", context["facts"]["age_years"])

    def test_new_field_correction_replaces_its_quote_and_reference_but_keeps_history(
        self,
    ):
        engine, _, state, _, raw = complete_report()
        old = state.facts["medications_taken"]
        reply = engine.correct_fact(state.session_id, "medications_taken", "今晚用过药物乙")
        item = next(
            item
            for item in reply.summary["known"]
            if item["key"] == "medications_taken"
        )
        self.assertEqual(item["evidence"], "今晚用过药物乙")
        self.assertNotIn("context", item)
        self.assertEqual(state.facts["medications_taken"].supersedes, old.fact_id)
        self.assertEqual(old.evidence, "昨晚用过药物甲")
        self.assertEqual(
            len([f for f in state.history if f.key == "medications_taken"]), 2
        )

    def test_missing_foreign_or_non_patient_sources_are_not_fabricated_as_patient_context(
        self,
    ):
        engine, _ = engine_with_backend()
        state = CaseState("this-case")
        report = "用过药物甲"
        engine.events.append(
            Event(
                "another-case",
                "turn.user_message",
                {"text": report + "，用量不清楚"},
                turn_id="same-id",
            )
        )
        state.record(
            Fact(
                "medications_taken",
                report,
                Assertion.PRESENT,
                Source.PATIENT,
                "same-id",
                evidence=report,
            )
        )
        item = engine._finish(state).summary["known"][0]
        self.assertNotIn("context", item)
        self.assertNotIn("earlier_source_turns", engine._context(state, "继续"))
        engine.events.append(
            Event(
                state.session_id,
                "turn.user_message",
                {"text": report + "，用量不清楚"},
                turn_id="same-id",
            )
        )
        state.facts["medications_taken"].source = Source.SYSTEM_INFERENCE
        self.assertNotIn("context", engine._finish(state).summary["known"][0])
        self.assertNotIn(
            "source_context_id",
            engine._context(state, "继续")["facts"]["medications_taken"],
        )
