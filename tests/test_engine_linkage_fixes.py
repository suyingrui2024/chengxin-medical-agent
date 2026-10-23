"""Engine-linkage fixes from the 2026-10-24 full-surface audit.

1. The adaptive engine's correct_fact override bypassed the base
   engine's CLOSED guard and silently rewound closed cases to HISTORY.
2. _record_reply re-composed the conclusion sheet on every already-
   closed guard reply (revision bump per message, further_reading
   overwritten).
3. ConsultationEngine.persist_state as the public write-through for
   out-of-pipeline mutations; the education write-back now persists,
   and education.needs_enrichment exposes the snapshot predicate.
4. The adaptive model context now carries the prior_case projection
   for follow-up re-triage sessions.
5. The advertised tool catalog no longer offers spawn_subagent when
   no coordinator is wired in / enabled; the offer is resolved against
   the live coordinator at message-build time, not frozen at
   construction (production assembly injects the coordinator via
   setattr after the engine exists).
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import ConsultationEngine, UserInput
from medagent.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.education import needs_enrichment
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.subagents import SubagentCoordinator
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend
from tests.test_agent_wave2_engine import make_engine as make_adaptive_engine
from tests.test_conclusion import ANSWERS
from tests.test_education import (
    FakeEngine,
    FakeEvents,
    FakeWebTools,
    make_enricher,
    make_item,
    make_state,
)


def _closed_state_with_complaint(engine, closed_reason, risk_level):
    state, _reply = engine.create_session()
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽两天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="咳嗽两天",
        )
    )
    state.phase = Phase.CLOSED
    state.closed_reason = closed_reason
    state.risk = RiskAssessment(risk_level)
    return state


class CorrectFactClosedGuardParityTests(unittest.TestCase):
    """Fix 1: both engines refuse corrections on confirmed/urgent
    closes and keep the emergency_route re-triage exemption."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.deterministic = self.context.services.require("consultation")
        self.adaptive, _backend, _events = make_adaptive_engine()

    def tearDown(self):
        self.host.stop()

    def _engines(self):
        return {
            "deterministic": self.deterministic,
            "adaptive": self.adaptive,
        }.items()

    def test_correction_rejected_and_state_untouched_on_final_closes(self):
        for name, engine in self._engines():
            for reason, risk in (
                ("confirmed_by_user", RiskLevel.ROUTINE),
                ("urgent_route", RiskLevel.URGENT),
            ):
                with self.subTest(engine=name, closed_reason=reason):
                    state = _closed_state_with_complaint(engine, reason, risk)
                    with self.assertRaises(ValueError) as caught:
                        engine.correct_fact(
                            state.session_id, "chief_complaint", "咳嗽三天"
                        )
                    self.assertIn("会谈已结束", str(caught.exception))
                    self.assertEqual(state.phase, Phase.CLOSED)
                    self.assertEqual(state.closed_reason, reason)
                    self.assertEqual(state.get("chief_complaint"), "咳嗽两天")

    def test_correction_still_available_on_emergency_route_close(self):
        for name, engine in self._engines():
            with self.subTest(engine=name):
                state = _closed_state_with_complaint(
                    engine, "emergency_route", RiskLevel.EMERGENCY
                )
                # Must not raise the closed-case error: the correction
                # re-runs triage on the emergency route instead.
                engine.correct_fact(state.session_id, "chief_complaint", "咳嗽三天")
                self.assertEqual(state.get("chief_complaint"), "咳嗽三天")
                corrections = [
                    event
                    for event in engine.events.for_session(state.session_id)
                    if event.event_type == "fact.correction_requested"
                ]
                self.assertEqual(len(corrections), 1)


class ConclusionRecomposeGuardTests(unittest.TestCase):
    """Fix 2: the sheet is composed once, on the closing transition;
    later guard replies must not recompose it."""

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

    def _to_summary(self):
        reply = self.reply
        for _ in range(30):
            if reply.kind == "summary":
                return reply
            if reply.kind == "self_check":
                reply = self._answer("暂时跳过", "skip")
                continue
            text, choice = ANSWERS.get(reply.question_id, ("没有", None))
            reply = self._answer(text, choice)
        raise AssertionError(f"no summary; last={reply.kind}")

    def test_post_close_messages_do_not_recompose_sheet(self):
        self._to_summary()
        final = self._answer("确认", "confirm_summary")
        self.assertEqual(final.kind, "closed")
        sheet = self.state.conclusion
        self.assertIsNotNone(sheet)
        revision = sheet["revision"]
        # Simulate the post-close enrichment landing on the sheet.
        sheet["further_reading"] = {
            "status": "ready",
            "sheet_revision": revision,
            "citations": [],
        }
        generated = [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.event_type == "conclusion.generated"
        ]
        self.assertEqual(len(generated), 1)

        for text in ("还在吗", "谢谢"):
            guard = self._answer(text)
            self.assertEqual(guard.kind, "closed")
            self.assertIsNone(guard.conclusion)

        self.assertEqual(self.state.conclusion["revision"], revision)
        self.assertIn("further_reading", self.state.conclusion)
        generated = [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.event_type == "conclusion.generated"
        ]
        self.assertEqual(len(generated), 1)


class _RecordingStore:
    def __init__(self, fail=False):
        self.saved = []
        self.fail = fail

    def load_closed(self):
        return []

    def save(self, state):
        if self.fail:
            raise RuntimeError("store unavailable")
        self.saved.append(state.session_id)


class PersistStateTests(unittest.TestCase):
    """Fix 3a: persist_state is the public, fail-open write-through."""

    def _engine(self, store):
        return ConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            session_store=store,
        )

    def test_persist_state_writes_through(self):
        store = _RecordingStore()
        engine = self._engine(store)
        state, _reply = engine.create_session()
        store.saved.clear()
        engine.persist_state(state)
        self.assertEqual(store.saved, [state.session_id])

    def test_persist_state_fail_open(self):
        store = _RecordingStore(fail=True)
        engine = self._engine(store)
        state = CaseState(session_id="persist-fail-open")
        engine.persist_state(state)  # must not raise
        self.assertEqual(engine._persist_failures, 1)


class _PersistRecordingEngine(FakeEngine):
    def __init__(self, state):
        super().__init__(state)
        self.persist_calls = []

    def persist_state(self, state):
        self.persist_calls.append(
            (state.session_id, "further_reading" in (state.conclusion or {}))
        )


class EducationWriteBackPersistTests(unittest.TestCase):
    """Fix 3b: a successful education write-back persists the case."""

    def test_write_back_persists_after_further_reading_lands(self):
        items = [make_item("T1", url="https://www.who.int/a")]
        tools = FakeWebTools(
            mode="education",
            search_handler=lambda q, p: {"status": "ok", "results": items},
            fetch_handler=lambda url: {
                "content": "上呼吸道感染多由病毒引起，通常一周左右自行缓解。",
                "content_sha256": "fake",
                "injection_suspect": False,
                "status": "ok",
            },
        )
        events = FakeEvents()
        state = make_state()
        engine = _PersistRecordingEngine(state)
        self.assertTrue(make_enricher(events, tools).schedule(engine, state))
        self.assertEqual(state.conclusion["further_reading"]["status"], "ready")
        # Persisted exactly once, and only after further_reading existed.
        self.assertEqual(engine.persist_calls, [(state.session_id, True)])


class NeedsEnrichmentTests(unittest.TestCase):
    """Fix 3b: the snapshot predicate mirrors the sheet's shape only."""

    def test_closed_visible_sheet_without_reading_needs_enrichment(self):
        self.assertTrue(needs_enrichment(make_state()))

    def test_each_condition_flips_it_off(self):
        self.assertFalse(needs_enrichment(make_state(phase=Phase.HISTORY)))
        self.assertFalse(needs_enrichment(make_state({})))
        from tests.test_education import make_sheet

        hidden = make_state(make_sheet(directions_visible=False))
        self.assertFalse(needs_enrichment(hidden))
        enriched = make_state(
            make_sheet(further_reading={"status": "ready", "sheet_revision": 1})
        )
        self.assertFalse(needs_enrichment(enriched))

    def test_dict_shaped_snapshot(self):
        snapshot = {
            "phase": "closed",
            "conclusion": {"directions_visible": True},
        }
        self.assertTrue(needs_enrichment(snapshot))
        snapshot["conclusion"]["further_reading"] = {"status": "ready"}
        self.assertFalse(needs_enrichment(snapshot))
        self.assertFalse(needs_enrichment({"phase": "summary", "conclusion": {}}))
        self.assertFalse(needs_enrichment(None))


class PriorCaseContextTests(unittest.TestCase):
    """Fix 4: re-triage sessions project prior_case into the context."""

    def setUp(self):
        self.engine, _backend, _events = make_adaptive_engine()
        self.state, _reply = self.engine.create_session()

    def test_prior_case_projected_with_banner_fields(self):
        self.state.runtime_snapshot["prior_case"] = {
            "session_id": "old-session",
            "chief_complaint": "咳嗽三天",
            "closed_reason": "confirmed_by_user",
            "disposition_level": "routine_visit",
            "disposition_text": "请在 72 小时内到线下医疗机构就诊评估。",
            "outcome": "worsened",
            "outcome_note": "咳得更厉害了",
            "outcome_recorded_at": "2026-10-23T10:00:00+08:00",
        }
        context = self.engine._context(self.state, "还是咳嗽")
        self.assertEqual(
            context["prior_case"],
            {
                "chief_complaint": "咳嗽三天",
                "disposition_level": "routine_visit",
                "disposition_text": "请在 72 小时内到线下医疗机构就诊评估。",
                "outcome": "worsened",
                "outcome_note": "咳得更厉害了",
            },
        )

    def test_no_prior_case_no_key(self):
        context = self.engine._context(self.state, "咳嗽")
        self.assertNotIn("prior_case", context)

    def test_prompt_carries_retriage_guidance(self):
        self.assertIn("prior_case", self.engine.prompt)
        self.assertIn("不要让患者重述上次结论", self.engine.prompt)


SPAWN_COMMAND = {
    "tool": "spawn_subagent",
    "arguments": {
        "task_kind": "second_opinion",
        "objective": "独立复核",
        "branches": [{"branch_id": "b1", "objective": "复核", "scope": {}}],
    },
}


class ToolCatalogTrimTests(unittest.TestCase):
    """Fix 5: spawn_subagent is advertised only when usable."""

    def _engine(self, coordinator=None):
        return AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=ScriptBackend(),
            subagent_coordinator=coordinator,
        )

    @staticmethod
    def _tool_names(engine):
        # static_context is the immutable base catalog (always the full
        # tool list); what the model is actually offered is resolved
        # against the live coordinator at message-build time, because
        # production assembly injects the coordinator after construction.
        # Assert on the advertised list — same semantics as before.
        context = engine._effective_static_context()
        return [tool["name"] for tool in context["tools"]]

    def test_no_coordinator_trims_spawn_but_keeps_the_rest(self):
        names = self._tool_names(self._engine())
        self.assertNotIn("spawn_subagent", names)
        self.assertIn("record_facts", names)
        self.assertIn("ask_patient", names)
        self.assertIn("review_summary", names)

    def test_off_mode_coordinator_trims_spawn(self):
        coordinator = SubagentCoordinator(
            EventStore(), ScriptBackend(), mode="off"
        )
        names = self._tool_names(self._engine(coordinator))
        self.assertNotIn("spawn_subagent", names)

    def test_enabled_coordinator_keeps_spawn(self):
        for mode in ("shadow", "advisory"):
            with self.subTest(mode=mode):
                coordinator = SubagentCoordinator(
                    EventStore(), ScriptBackend(), mode=mode
                )
                names = self._tool_names(self._engine(coordinator))
                self.assertIn("spawn_subagent", names)

    def test_execution_side_remains_fail_closed_without_coordinator(self):
        engine = self._engine()
        state, _reply = engine.create_session()
        result = engine._execute(state, SPAWN_COMMAND, "复核一下", "turn-1")
        self.assertEqual(result, {"ok": False, "status": "unavailable"})


if __name__ == "__main__":
    unittest.main()
