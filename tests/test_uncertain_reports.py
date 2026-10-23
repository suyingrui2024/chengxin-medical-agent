"""Unresolved patient narratives survive actual consultation and correction."""

import unittest

from medagent.consultation import UserInput
from medagent.agent import AdaptiveConsultationEngine
from medagent.domain import Assertion, Source
from medagent.events import Event, EventStore
from medagent.knowledge import ContentRepository
from medagent.interpreter import Interpretation
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


MEDICATION = "昨晚用过药物甲，用量不清楚"
ALLERGY = "以前有过皮疹，是否药物过敏不确定"
ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("男", "male"),
    "age": ("32岁", None),
    "chief_complaint": ("手臂疼痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("昨天", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("3分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": (MEDICATION, None),
    "allergies": (ALLERGY, None),
    "relevant_history": ("没有", None),
    "patient_goal": ("整理就医记录", None),
}


class UncertainReportTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.engine = self.host.start().services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def complete(self, caregiver=False):
        state, reply = self.engine.create_session()
        for _ in range(30):
            if reply.kind in {"summary", "emergency", "closed"}:
                break
            text, choice = (
                ("暂时跳过", "skip")
                if reply.kind == "self_check"
                else ANSWERS[reply.question_id]
            )
            if caregiver and reply.question_id == "consult_for":
                text, choice = "家人", "other_adult"
            reply = self.engine.submit(
                state.session_id, UserInput(text, choice, reply.action_id)
            )
        self.assertEqual(reply.kind, "summary")
        return state, reply

    def test_complete_consultation_keeps_reports_without_inventing_confirmed_values(
        self,
    ):
        state, reply = self.complete()
        reports = {item["key"]: item for item in reply.summary["unknown_details"]}
        for key, text in [("medications_taken", MEDICATION), ("allergies", ALLERGY)]:
            self.assertEqual(reports[key]["evidence"], text)
            self.assertEqual(reports[key]["source"], "patient")
            self.assertEqual(reports[key]["quality"], "not_assessed")
            self.assertEqual(state.facts[key].assertion, Assertion.UNKNOWN)
            self.assertIsNone(state.facts[key].value)
            self.assertIsNone(state.get(key))
            self.assertFalse(any(item["key"] == key for item in reply.summary["known"]))
        self.assertEqual(state.calculations, [])
        view = reply.to_dict()["presentation"]["blocks"][-1]["data"]
        self.assertEqual(view["unknown_details"], reply.summary["unknown_details"])
        self.assertEqual(state.get("age_years"), 32)

    def test_correction_uses_only_current_evidence_and_keeps_history(self):
        state, reply = self.complete()
        first = state.facts["medications_taken"]
        reply = self.engine.correct_fact(
            state.session_id, "medications_taken", "不记得了", first.fact_id
        )
        report = next(
            item
            for item in reply.summary["unknown_details"]
            if item["key"] == "medications_taken"
        )
        self.assertEqual(report["evidence"], "不记得了")
        self.assertEqual(first.evidence, MEDICATION)
        self.assertEqual(state.facts["medications_taken"].supersedes, first.fact_id)
        reply = self.engine.correct_fact(state.session_id, "medications_taken", "没有")
        self.assertFalse(
            any(
                item["key"] == "medications_taken"
                for item in reply.summary["unknown_details"]
            )
        )
        self.assertEqual(state.facts["medications_taken"].assertion, Assertion.ABSENT)
        self.assertEqual(
            len([f for f in state.history if f.key == "medications_taken"]), 3
        )
        self.assertIn("过敏信息", reply.summary["unknown"])
        self.assertNotIn("已做处理/用药", reply.summary["unknown"])

    def test_caregiver_and_skipped_measurement_keep_their_actual_source_and_quality(
        self,
    ):
        state, reply = self.complete(caregiver=True)
        reports = {item["key"]: item for item in reply.summary["unknown_details"]}
        self.assertEqual(state.facts["allergies"].source, Source.CAREGIVER)
        self.assertEqual(reports["allergies"]["source"], "caregiver")
        skipped = [item for item in reports.values() if item["quality"] == "not_done"]
        self.assertTrue(skipped)
        self.assertTrue(all(item["evidence"] == "暂时跳过" for item in skipped))


class AdaptiveUncertainReportTests(unittest.TestCase):
    def test_model_proposal_keeps_quoted_report_and_its_uncertainty_in_summary(self):
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
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("medications_taken", None, MEDICATION, "unknown"),
                    fact("allergies", None, "是否药物过敏不确定", "unknown"),
                ],
            ),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        engine.submit(state.session_id, UserInput(f"{MEDICATION}；{ALLERGY}"))
        summary = engine._finish(state).summary
        reports = {item["key"]: item for item in summary["unknown_details"]}
        self.assertEqual(reports["medications_taken"]["evidence"], MEDICATION)
        self.assertEqual(reports["allergies"]["evidence"], "是否药物过敏不确定")
        self.assertEqual(
            reports["allergies"]["context"]["text"], f"{MEDICATION}；{ALLERGY}"
        )
        self.assertEqual(reports["allergies"]["context"]["scope"], "source_turn")
        self.assertEqual(
            reports["allergies"]["context"]["turn_id"], state.facts["allergies"].turn_id
        )
        old = state.facts["allergies"]
        engine.events.append(
            Event(
                "another-session",
                "turn.user_message",
                {"text": "其他患者的信息：不清楚"},
                turn_id="missing-source-turn",
            )
        )
        engine._record_fact(
            state,
            "allergies",
            Interpretation(None, Assertion.UNKNOWN),
            "missing-source-turn",
            "不清楚",
        )
        replaced = next(
            item
            for item in engine._finish(state).summary["unknown_details"]
            if item["key"] == "allergies"
        )
        self.assertNotIn("context", replaced)
        self.assertEqual(old.evidence, "是否药物过敏不确定")
        self.assertEqual(summary["known"], [])
        self.assertEqual(len(backend.calls), 2)
