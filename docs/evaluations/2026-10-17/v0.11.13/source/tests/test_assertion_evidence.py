"""Uncertainty, trimmed quotes and unrelated denials through the actual controller."""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError, validate_proposals
from medagent.consultation import UserInput
from medagent.domain import Assertion, CaseState
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.interpreter import Interpreter
from tests.test_agent import ScriptBackend, cmd, fact


class AssertionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.content = ContentRepository()

    def proposal(self, raw, evidence, key="medications_taken", assertion="absent"):
        return validate_proposals(
            self.content,
            CaseState("synthetic"),
            raw,
            [fact(key, None, evidence, assertion)],
        )

    def test_uncertain_negatives_are_rejected_in_all_four_history_fields(self):
        for key in [
            "medications_taken",
            "allergies",
            "relevant_history",
            "associated_symptoms",
        ]:
            for text in [
                "不知道",
                "不清楚",
                "不确定",
                "不记得",
                "记不清",
                "不能确定",
                "好像没有",
                "可能没有",
                "应该没有",
                "没把握",
            ]:
                with self.subTest(key=key, text=text):
                    with self.assertRaises(ProposalError):
                        self.proposal(text, text, key)
                    self.assertEqual(
                        self.proposal(text, text, key, "unknown")[0]["assertion"],
                        Assertion.UNKNOWN,
                    )

    def test_a_shortened_quote_cannot_remove_its_uncertainty_or_question_scope(self):
        cases = [
            ("我记不清有没有用药", "没有用药"),
            ("好像没有用药", "没有用药"),
            ("没有用药的事我不确定", "没有用药"),
            ("有没有用药", "没有用药"),
            ("是否没有用药", "没有用药"),
            ("没有用药，具体不确定", "没有用药"),
            ("但我不记得了。没有用药", "没有用药"),
        ]
        for raw, evidence in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(ProposalError):
                    self.proposal(raw, evidence)

    def test_named_uncertainty_in_another_clause_does_not_erase_a_clear_denial(self):
        for raw in [
            "过敏不清楚，没有用药",
            "没有用药，过敏不清楚",
            "不确定有没有过敏；我没有用药。",
            "我没有用药，但剂量的问题是家人的，我不清楚",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    self.proposal(raw, "没有用药")[0]["assertion"], Assertion.ABSENT
                )
        for text in ["没有", "无", "没", "未用药", "没吃药", "从未用药", "否认用药"]:
            with self.subTest(text=text):
                self.assertEqual(
                    self.proposal(text, text)[0]["assertion"], Assertion.ABSENT
                )

    def test_interrogatives_and_non_absence_negations_do_not_become_absent(self):
        for text in [
            "不舒服",
            "不愿服药",
            "有没有用药",
            "有无过敏",
            "是否用药",
            "无所谓用药",
            "用药无效",
            "没有必要用药",
            "不是没有用药",
            "并非没有用药",
            "不能否认有过敏",
        ]:
            with self.subTest(text=text):
                with self.assertRaises(ProposalError):
                    self.proposal(text, text)

    def test_repeated_quote_must_be_disambiguated_instead_of_choosing_the_certain_occurrence(
        self,
    ):
        raw = "好像没有用药；更正一下，昨天没有用药"
        with self.assertRaises(ProposalError):
            self.proposal(raw, "没有用药")
        self.assertEqual(self.proposal(raw, "昨天没有用药")[0]["assertion"], Assertion.ABSENT)

    def test_atomic_batch_can_recover_from_a_rejected_negative_without_losing_age(self):
        backend = ScriptBackend()
        events = EventStore()
        engine = AdaptiveConsultationEngine(
            content=self.content,
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=events,
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        state, _ = engine.create_session()
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("age_years", 32, "32岁"),
                    fact("allergies", None, "过敏不知道", "absent"),
                ],
            ),
            cmd(
                "record_facts",
                facts=[
                    fact("age_years", 32, "32岁"),
                    fact("allergies", None, "过敏不知道", "unknown"),
                ],
            ),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        reply = engine.submit(state.session_id, UserInput("32岁，过敏不知道"))
        self.assertEqual(reply.question_id, "chief_complaint")
        self.assertEqual(state.get("age_years"), 32)
        self.assertEqual(state.facts["allergies"].assertion, Assertion.UNKNOWN)
        self.assertEqual(len(state.history), 2)
        self.assertIn("unknown/null", backend.calls[1][-1]["content"])
        recorded = [
            event
            for event in events.for_session(state.session_id)
            if event.event_type == "fact.recorded"
        ]
        self.assertEqual(len(recorded), 2)
        self.assertTrue(
            all(event.payload["assertion"] != "absent" for event in recorded)
        )

    def test_invalid_correction_keeps_the_prior_fact_until_a_valid_correction_arrives(
        self,
    ):
        state = CaseState("synthetic")
        from medagent.domain import Fact, Source

        old = Fact(
            "allergies",
            None,
            Assertion.ABSENT,
            Source.PATIENT,
            "old-turn",
            evidence="没有过敏",
        )
        state.record(old)
        with self.assertRaises(ProposalError):
            validate_proposals(
                self.content,
                state,
                "现在过敏不清楚",
                [fact("allergies", None, "过敏不清楚", "absent", correction=True)],
            )
        self.assertIs(state.facts["allergies"], old)
        self.assertEqual(state.history, [old])
        self.assertEqual(
            validate_proposals(
                self.content,
                state,
                "现在过敏不清楚",
                [fact("allergies", None, "过敏不清楚", "unknown", correction=True)],
            )[0]["assertion"],
            Assertion.UNKNOWN,
        )

    def test_present_text_cannot_disguise_a_false_denial_or_drop_uncertainty(self):
        for raw, evidence, value in [
            ("过敏不清楚", "过敏不清楚", "没有过敏"),
            ("好像没有过敏", "没有过敏", "没有过敏"),
            ("不知道有没有过敏", "没有过敏", "明确否认过敏"),
            ("用过药物甲，用量不清楚", "用过药物甲，用量不清楚", "用过药物甲"),
        ]:
            with self.subTest(raw=raw):
                with self.assertRaises(ProposalError):
                    validate_proposals(
                        self.content,
                        CaseState("synthetic"),
                        raw,
                        [
                            fact(
                                "allergies" if "过敏" in raw else "medications_taken",
                                value,
                                evidence,
                            )
                        ],
                    )
        raw = "昨晚用过药物甲，用量不清楚"
        result = validate_proposals(
            self.content,
            CaseState("synthetic"),
            raw,
            [fact("medications_taken", raw, raw)],
        )
        self.assertEqual(result[0]["value"], raw)
        self.assertEqual(result[0]["assertion"], Assertion.PRESENT)

    def test_demo_and_direct_correction_parser_preserve_qualified_history_as_unknown(
        self,
    ):
        for question_id in [
            "allergies",
            "medications_taken",
            "relevant_history",
            "associated_symptoms",
            "emergency_screen",
        ]:
            for raw in ["好像没有", "应该没有", "记不清", "不太确定"]:
                with self.subTest(question=question_id, raw=raw):
                    result = Interpreter().interpret(
                        self.content.question(question_id), raw
                    )
                    self.assertEqual(result.assertion, Assertion.UNKNOWN)
                    self.assertIsNone(result.value)
