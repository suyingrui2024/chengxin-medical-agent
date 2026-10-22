"""Reasoning core (design phase 3): workspace population, evidence
validation, differential question selection, abstention, and the
triage reasoning-review provider contract."""
from __future__ import annotations

import unittest

from medagent.consultation import UserInput
from medagent.domain import CaseState
from medagent.reasoning import (
    DifferentialPlanner,
    EvidenceRef,
    Hypothesis,
    ReasoningWorkspace,
    RuleBasedProposer,
    enable_differential_planning,
    validate_workspace,
)
from medagent.runtime import build_runtime
from medagent.triage import TriageService
from medagent.triage_review import ReasoningReviewProvider

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "age": ("32岁", None),
    "chief_complaint": ("咳嗽发烧两天", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两天前开始", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("6分", None),
    "associated_symptoms": ("有点乏力头痛", None),
    "resp_breathing": ("有点气短，活动后明显", None),
    "resp_cough_details": ("干咳为主", None),
    "resp_intake": ("能正常喝水吃饭", None),
    "medications_taken": ("没有", None),
    "allergies": ("没有", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想快点好", None),
}


class _Driver:
    def __init__(self, engine, state, reply):
        self.engine = engine
        self.state = state
        self.reply = reply
        self.asked = []

    def run(self, max_turns=25):
        reply = self.reply
        for _ in range(max_turns):
            if reply.kind in {"summary", "emergency", "urgent", "closed"}:
                break
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    self.state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            self.asked.append(reply.question_id)
            text, choice = ANSWERS[reply.question_id]
            reply = self.engine.submit(
                self.state.session_id, UserInput(text, choice)
            )
        self.reply = reply
        return reply


class DifferentialFlowTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def test_workspace_populates_and_tracks_support(self):
        enable_differential_planning(self.engine)
        driver = _Driver(self.engine, self.state, self.reply)
        driver.run()
        self.assertEqual(driver.reply.kind, "summary")
        workspace = ReasoningWorkspace.from_dict(self.state.reasoning)
        self.assertIn("uri", workspace.hypotheses)
        lower = workspace.hypotheses["lower_respiratory"]
        # 气短 breathing answer + severity 6 support it
        self.assertEqual(lower.status, "supported")
        self.assertTrue(lower.evidence_for)
        kinds = [
            e.event_type for e in self.events.for_session(self.state.session_id)
        ]
        self.assertIn("reasoning.workspace_updated", kinds)

    def test_differential_planner_prioritizes_red_flag_discrimination(self):
        enable_differential_planning(self.engine)
        driver = _Driver(self.engine, self.state, self.reply)
        # Drive only through the history phase opening questions.
        reply = driver.reply
        asked = []
        while len(asked) < 8 and reply.kind not in {"summary", "emergency"}:
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    self.state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            asked.append(reply.question_id)
            text, choice = ANSWERS[reply.question_id]
            reply = self.engine.submit(
                self.state.session_id, UserInput(text, choice)
            )
        history_questions = asked[4:]  # after consult_for/age/chief + screen
        # resp_breathing directly probes the severe_breathing red-flag
        # domain and discriminates three active hypotheses, so the
        # differential planner asks it first, ahead of bank order (onset).
        self.assertEqual(history_questions[0], "resp_breathing")

    def test_rule_planner_keeps_bank_order(self):
        driver = _Driver(self.engine, self.state, self.reply)
        reply = driver.reply
        asked = []
        while len(asked) < 8 and reply.kind not in {"summary", "emergency"}:
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    self.state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            asked.append(reply.question_id)
            text, choice = ANSWERS[reply.question_id]
            reply = self.engine.submit(
                self.state.session_id, UserInput(text, choice)
            )
        history_questions = asked[4:]
        self.assertEqual(history_questions[:3], ["onset", "trajectory", "severity"])
        self.assertEqual(self.state.reasoning, {})


class ValidatorTests(unittest.TestCase):
    def _state_with_facts(self):
        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        state, reply = engine.create_session()
        engine.submit(state.session_id, UserInput("我自己", "self"))
        engine.submit(state.session_id, UserInput("32岁"))
        engine.submit(state.session_id, UserInput("咳嗽发烧两天"))
        return host, engine, state

    def test_fabricated_quote_is_rejected(self):
        host, engine, state = self._state_with_facts()
        try:
            fact = state.facts["chief_complaint"]
            workspace = ReasoningWorkspace(hypotheses={
                "fake": Hypothesis(
                    id="fake", label="虚构假设", category="common_condition",
                    prior_band="low",
                    evidence_for=[EvidenceRef("chief_complaint", fact.fact_id, "根本没说过的话")],
                ),
                "also_fake": Hypothesis(
                    id="also_fake", label="错位引用", category="common_condition",
                    prior_band="low",
                    evidence_for=[EvidenceRef("chief_complaint", "not-the-fact-id", "咳嗽")],
                ),
            })
            accepted, rejected = validate_workspace(state, workspace)
            self.assertEqual(accepted.hypotheses, {})
            self.assertEqual({item[0] for item in rejected}, {"fake", "also_fake"})
        finally:
            host.stop()

    def test_proposer_output_validates_clean(self):
        host, engine, state = self._state_with_facts()
        try:
            proposed = RuleBasedProposer().propose(state)
            accepted, rejected = validate_workspace(state, proposed)
            self.assertEqual(rejected, [])
            self.assertTrue(accepted.hypotheses)
        finally:
            host.stop()


class AbstentionTests(unittest.TestCase):
    def test_unresolved_red_flag_with_stagnation_abstains_to_urgent(self):
        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        try:
            state, _ = engine.create_session()
            # Fill every question fact so no candidates remain.
            from medagent.domain import Assertion, Fact, Source

            for question in engine.content.questions:
                state.record(Fact(
                    key=question.fact_key, value="已填", assertion=Assertion.PRESENT,
                    source=Source.PATIENT, turn_id="t",
                ))
            state.reasoning = {
                "version": 4,
                "stagnation_turns": 3,
                "hypotheses": [{
                    "id": "lower_respiratory", "label": "下呼吸道感染/肺炎可能",
                    "category": "serious_condition", "prior_band": "medium",
                    "status": "active",
                    "evidence_for": [{
                        "fact_key": "chief_complaint",
                        "fact_id": state.facts["chief_complaint"].fact_id,
                        "quote": "已填",
                    }],
                    "evidence_against": [],
                    "red_flags": ["severe_breathing"],
                    "discriminating_questions": [],
                    "updated_at_rev": 0,
                }],
            }
            engine._offer_task = lambda *a, **k: None
            planner = DifferentialPlanner(engine)
            # The proposer recomputes from facts; stub it to keep the
            # preloaded workspace so this test isolates the abstain rule.
            planner._proposer = _StaticProposer(state.reasoning)
            reply = planner.plan(state)
            self.assertEqual(reply.kind, "urgent")
        finally:
            host.stop()


class _StaticProposer:
    def __init__(self, raw):
        self._workspace = ReasoningWorkspace.from_dict(raw)

    def propose(self, state):
        return self._workspace


class ReasoningReviewProviderTests(unittest.TestCase):
    def test_provider_adds_validated_hits_only(self):
        def decide(text, catalogue):
            return {"hits": [
                {"flag_id": "severe_breathing", "evidence": "吸不到底"},
                {"flag_id": "not_a_flag", "evidence": "吸不到底"},
                {"flag_id": "cyanosis", "evidence": "编造的证据"},
            ]}

        service = TriageService(
            semantic_provider=ReasoningReviewProvider(decide)
        )
        labels = service.scan_text("她哮喘犯了，气都吸不到底")
        self.assertIn("严重呼吸困难", labels)

    def test_provider_failure_falls_back_to_deterministic(self):
        def decide(text, catalogue):
            raise RuntimeError("backend down")

        service = TriageService(
            semantic_provider=ReasoningReviewProvider(decide)
        )
        self.assertEqual(service.scan_text("患者失去意识了"), ["意识明显异常"])
        self.assertEqual(service.scan_text("最近有点失眠"), [])


if __name__ == "__main__":
    unittest.main()
