"""Regression tests for the four P1-A fixes from the 2026-10-24 API
healthcheck (point A: adaptive decision loop).

- P1-A-D1: review_summary must not close while patient_goal is
  unrecorded, even with an abstain_reason for an unexplored pathway.
- P1-A-D2: spawn_subagent at the decision layer only admits
  evidence_lookup / second_opinion; controller-only kinds (case_digest,
  followup_batch) are refused before any coordinator call and without
  consuming the round's spawn budget.
- P1-A-D3: a repairable decision-envelope error from the backend
  (bad JSON, wrong key set, over-long decision) is fed back with repair
  guidance inside the 6-step self-correction loop; other backend
  failures keep the service_error path.
- P1-A-D4: a correction-only retry of items rejected earlier in the
  same round is not starved by the two-write cap, while the cap itself
  still blocks fresh or mixed writes.
"""

import json
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError
from medagent.consultation import UserInput
from medagent.domain import Assertion, Phase, RiskLevel
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact
from tests.test_agent_wave2_engine import make_engine


def _engine(backend=None, coordinator=None):
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend or ScriptBackend(),
        subagent_coordinator=coordinator,
    )


class _AdvisoryCoordinator:
    """Coordinator double that says ok to anything, advisory always."""

    def __init__(self, result=None):
        self.result = result or {"ok": True, "spawn_id": "s1", "branches": []}
        self.calls = []

    def mode_for(self, task_kind):
        return "advisory"

    def run_spawn(self, state, proposal, *, session_id, cancelled=None):
        self.calls.append(proposal)
        return self.result


def _spawn_command(kind):
    return cmd(
        "spawn_subagent",
        task_kind=kind,
        objective="复核",
        branches=[{"branch_id": "b1", "objective": "复核", "scope": {}}],
    )


class PatientGoalGateTests(unittest.TestCase):
    """P1-A-D1: the G6b healthcheck shape, without the pathway gate
    in the way — chief complaint 「肚子不太好」 triggers the abdominal
    pathway (detail gi_details) but never candidates gi_details, so the
    only thing standing between the case and a summary is the goal."""

    SEED_TEXT = "我自己，肚子不太好，32岁，这些危险情况都没有，其它不知道"

    def _seed_without_goal(self, engine, state):
        facts = []
        for question in engine.content.questions:
            if question.fact_key == "patient_goal":
                continue
            if not engine.content.question_applies(question, "肚子不太好"):
                continue
            if question.fact_key == "consult_for":
                facts.append(fact("consult_for", "self", "我自己"))
            elif question.fact_key == "chief_complaint":
                facts.append(fact("chief_complaint", "肚子不太好", "肚子不太好"))
            elif question.fact_key == "age_years":
                facts.append(fact("age_years", 32, "32岁"))
            elif question.fact_key == "emergency_screen":
                facts.append(
                    fact("emergency_screen", None, "这些危险情况都没有", "absent")
                )
            else:
                facts.append(fact(question.fact_key, None, "不知道", "unknown"))
        engine._execute(
            state,
            cmd("record_facts", facts=facts),
            self.SEED_TEXT,
            "t-seed",
        )
        return facts

    def test_abstained_summary_is_refused_without_patient_goal(self):
        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        self._seed_without_goal(engine, state)
        context = engine._context(state, self.SEED_TEXT)
        self.assertEqual(context["missing_required"], ["patient_goal"])
        self.assertEqual(
            context["pathway_exploration"],
            [
                {
                    "pathway": "abdominal",
                    "detail_fields": ["gi_details"],
                    "explored": False,
                }
            ],
        )
        with self.assertRaises(ProposalError) as caught:
            engine._execute(
                state,
                cmd(
                    "review_summary",
                    abstain_reason="患者已否认其他不适，通路细节暂不适用进一步探明",
                ),
                self.SEED_TEXT,
                "t-sum",
            )
        self.assertIn("patient_goal", str(caught.exception))
        self.assertNotEqual(state.phase, Phase.SUMMARY)

    def test_summary_passes_once_patient_goal_is_recorded(self):
        engine, _, _ = make_engine()
        state, _ = engine.create_session()
        self._seed_without_goal(engine, state)
        engine._execute(
            state,
            cmd(
                "record_facts",
                facts=[fact("patient_goal", "想知道要不要紧", "想知道要不要紧")],
            ),
            "想知道要不要紧",
            "t-goal",
        )
        self.assertEqual(engine._context(state, "复核")["missing_required"], [])
        reply = engine._execute(
            state,
            cmd(
                "review_summary",
                abstain_reason="患者已否认其他不适，通路细节暂不适用进一步探明",
            ),
            "复核",
            "t-sum",
        )
        self.assertEqual(reply.kind, "summary")
        self.assertEqual(state.phase, Phase.SUMMARY)


class SpawnKindWhitelistTests(unittest.TestCase):
    """P1-A-D2."""

    def setUp(self):
        self.coordinator = _AdvisoryCoordinator()
        self.engine = _engine(coordinator=self.coordinator)
        self.state, _ = self.engine.create_session()
        self.state.risk.level = RiskLevel.ROUTINE

    def _execute(self, kind):
        return self.engine._execute(
            self.state, _spawn_command(kind), "复核一下", "turn-1"
        )

    def test_controller_only_kinds_are_refused_without_coordinator_call(self):
        for kind in ("case_digest", "followup_batch"):
            result = self._execute(kind)
            self.assertFalse(result["ok"])
            self.assertIn("evidence_lookup", result["error"])
            self.assertIn("second_opinion", result["error"])
        self.assertEqual(self.coordinator.calls, [])

    def test_refused_kind_does_not_consume_the_round_spawn_budget(self):
        refused = self._execute("case_digest")
        self.assertFalse(refused["ok"])
        # The very next legal proposal in the same round still runs.
        result = self._execute("evidence_lookup")
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.coordinator.calls), 1)
        self.assertEqual(self.coordinator.calls[0]["task_kind"], "evidence_lookup")

    def test_both_legal_kinds_still_pass(self):
        for kind in ("evidence_lookup", "second_opinion"):
            engine = _engine(coordinator=_AdvisoryCoordinator())
            state, _ = engine.create_session()
            state.risk.level = RiskLevel.ROUTINE
            result = engine._execute(state, _spawn_command(kind), "复核一下", "t")
            self.assertTrue(result["ok"], kind)

    def test_case_digest_proposal_is_replanned_end_to_end(self):
        backend = ScriptBackend()
        engine = _engine(backend=backend, coordinator=self.coordinator)
        state, _ = engine.create_session()
        backend.commands = [
            _spawn_command("case_digest"),
            cmd("ask_question", question_id="chief_complaint"),
        ]
        reply = engine.submit(state.session_id, UserInput("我自己，咳嗽"))
        self.assertEqual(reply.question_id, "chief_complaint")
        self.assertEqual(self.coordinator.calls, [])
        tool_results = [
            event.payload["result"]
            for event in engine.events.for_session(state.session_id)
            if event.event_type == "tool.completed"
            and isinstance(event.payload.get("result"), dict)
        ]
        self.assertFalse(tool_results[0]["ok"])
        self.assertIn("evidence_lookup", tool_results[0]["error"])


class _RaisingBackend:
    """Backend that pops actions: exceptions are raised, dicts returned."""

    name = "raising-test-only"

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
        self.seen_messages = []

    def decide(self, session_id, messages):
        self.calls += 1
        self.seen_messages.append([dict(message) for message in messages])
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


def _envelope_errors():
    return [
        json.JSONDecodeError("Expecting value", "not json", 0),
        ValueError("decision must contain only tool and arguments"),
        ValueError("invalid decision size"),
    ]


class DecisionEnvelopeRepairTests(unittest.TestCase):
    """P1-A-D3."""

    def test_each_envelope_error_is_repaired_inside_the_loop(self):
        for error in _envelope_errors():
            with self.subTest(error=str(error)):
                backend = _RaisingBackend(
                    [error, cmd("ask_question", question_id="chief_complaint")]
                )
                engine = _engine(backend=backend)
                state, _ = engine.create_session()
                reply = engine.submit(state.session_id, UserInput("咳嗽"))
                self.assertEqual(reply.question_id, "chief_complaint")
                self.assertEqual(backend.calls, 2)
                failed = [
                    event
                    for event in engine.events.for_session(state.session_id)
                    if event.event_type == "agent.backend_failed"
                ]
                self.assertEqual(len(failed), 1)
                self.assertTrue(failed[0].payload["repairable_envelope"])
                # The repair guidance reaches the model as the last
                # message of the retry call, in tool_result shape.
                retry_last = backend.seen_messages[1][-1]["content"]
                self.assertIn("tool_result", retry_last)
                self.assertIn("JSON", retry_last)

    def test_non_envelope_failures_keep_service_error_semantics(self):
        for error in (
            ValueError("max output tokens must be between 128 and 8192"),
            RuntimeError("connection reset"),
        ):
            with self.subTest(error=str(error)):
                backend = _RaisingBackend([error])
                engine = _engine(backend=backend)
                state, _ = engine.create_session()
                reply = engine.submit(state.session_id, UserInput("咳嗽"))
                self.assertEqual(reply.kind, "service_error")
                self.assertEqual(state.pending_input, "咳嗽")
                self.assertEqual(backend.calls, 1)
                failed = [
                    event
                    for event in engine.events.for_session(state.session_id)
                    if event.event_type == "agent.backend_failed"
                ]
                self.assertEqual(len(failed), 1)
                self.assertNotIn("repairable_envelope", failed[0].payload)

    def test_repeated_envelope_errors_still_exhaust_the_loop(self):
        backend = _RaisingBackend(
            [ValueError("invalid decision size")] * 10
        )
        engine = _engine(backend=backend)
        state, _ = engine.create_session()
        reply = engine.submit(state.session_id, UserInput("咳嗽"))
        self.assertEqual(reply.kind, "service_error")
        self.assertEqual(backend.calls, 6)
        self.assertEqual(state.pending_input, "咳嗽")


class WriteCapCorrectionTests(unittest.TestCase):
    """P1-A-D4: the healthcheck probe-2b starvation shape, verbatim."""

    TEXT = "我自己32岁，咳嗽，没有过敏"

    def test_correction_only_retry_lands_after_two_partial_writes(self):
        backend = ScriptBackend()
        engine = _engine(backend=backend)
        state, _ = engine.create_session()
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("allergies", "无", "没有过敏", "absent"),
                ],
            ),
            cmd(
                "record_facts",
                facts=[
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                    fact("allergies", None, "没有过敏", "unknown"),
                ],
            ),
            cmd(
                "record_facts",
                facts=[fact("allergies", None, "没有过敏", "absent")],
            ),
            cmd("ask_question", question_id="sex"),
        ]
        reply = engine.submit(state.session_id, UserInput(self.TEXT))
        self.assertIn("allergies", state.facts)
        self.assertEqual(state.assertion("allergies"), Assertion.ABSENT)
        self.assertEqual(reply.question_id, "sex")

    def test_cap_still_blocks_a_fresh_third_write(self):
        backend = ScriptBackend()
        engine = _engine(backend=backend)
        state, _ = engine.create_session()
        backend.commands = [
            cmd("record_facts", facts=[fact("age_years", 32, "32岁")]),
            cmd("record_facts", facts=[fact("consult_for", "self", "我自己")]),
            cmd("record_facts", facts=[fact("chief_complaint", "腹痛", "腹痛")]),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = engine.submit(state.session_id, UserInput("我自己32岁腹痛"))
        self.assertNotIn("chief_complaint", state.facts)
        self.assertEqual(reply.question_id, "emergency_screen")

    def test_mixed_correction_plus_new_key_is_still_capped(self):
        backend = ScriptBackend()
        engine = _engine(backend=backend)
        state, _ = engine.create_session()
        text = "我自己32岁，咳嗽，没有过敏，没有用药"
        backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("age_years", 32, "32岁"),
                    fact("allergies", "无", "没有过敏", "absent"),
                ],
            ),
            cmd(
                "record_facts",
                facts=[
                    fact("chief_complaint", "咳嗽", "咳嗽"),
                    fact("allergies", None, "没有过敏", "unknown"),
                ],
            ),
            # Mixed: a rejected key plus a brand-new key — not a
            # correction-only retry, so the cap must block the whole
            # call and neither item may land.
            cmd(
                "record_facts",
                facts=[
                    fact("allergies", None, "没有过敏", "absent"),
                    fact("medications_taken", None, "没有用药", "absent"),
                ],
            ),
            # The pure correction retry afterwards still lands.
            cmd(
                "record_facts",
                facts=[fact("allergies", None, "没有过敏", "absent")],
            ),
            cmd("ask_question", question_id="sex"),
        ]
        reply = engine.submit(state.session_id, UserInput(text))
        self.assertNotIn("medications_taken", state.facts)
        self.assertIn("allergies", state.facts)
        self.assertEqual(state.assertion("allergies"), Assertion.ABSENT)
        self.assertEqual(reply.question_id, "sex")


if __name__ == "__main__":
    unittest.main()
