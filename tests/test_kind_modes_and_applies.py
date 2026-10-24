"""Lane B1: per-kind subagent consumption in the adaptive engine, and
the widened question-applies corpus.

Per-kind consumption: the coordinator resolves an effective mode per
task kind (mode_for). The engine must consume *that* — the advertised
catalog follows the two model-proposed kinds only, the spawn branch's
shadow-vs-advisory decision follows the spawned kind, the controller's
case_digest trigger follows the case_digest kind, and an advisory
second-opinion disagreement is also recorded as a subagent.disagreement
event for metrics aggregation. Coordinators without mode_for (older
doubles) fall back to their global mode.

Applies corpus: question_applies used to match only the recorded
chief-complaint value — the model's normalized restatement. When the
restatement drifted (「肚子不舒服」 recorded as 「肚子时不时不舒服」)
the gi question's applies term stopped matching and the fixed question
was rejected. Evaluation now also uses the fact's verbatim evidence
(and superseded chief-complaint evidence), while a term the patient
mentions only to deny it (「不是肚子不舒服，是胃胀」) still does not
count as support.
"""
import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Assertion, CaseState, Fact, RiskLevel, Source
from medagent.events import EventStore
from medagent.knowledge import ContentRepository, _applies_term_match
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from tests.test_agent import ScriptBackend, cmd, fact


class _KindCoordinator:
    """Coordinator double implementing the per-kind contract."""

    def __init__(self, mode="off", kind_modes=None, spawn_result=None):
        self.mode = mode
        self.kind_modes = dict(kind_modes or {})
        self.spawn_result = spawn_result
        self.spawn_calls = []
        self.digest_calls = []

    def mode_for(self, task_kind):
        if isinstance(task_kind, str) and task_kind in self.kind_modes:
            return self.kind_modes[task_kind]
        return self.mode

    def run_spawn(self, state, proposal, *, session_id, cancelled=None):
        self.spawn_calls.append(proposal)
        return self.spawn_result

    def run_case_digest(self, state, *, session_id, cancelled=None):
        self.digest_calls.append(session_id)
        return {"points": ["既往对话要点"]}


class _LegacyCoordinator:
    """Pre-contract double: global mode only, no mode_for."""

    def __init__(self, mode, spawn_result=None):
        self.mode = mode
        self.spawn_result = spawn_result
        self.digest_calls = []

    def run_spawn(self, state, proposal, *, session_id, cancelled=None):
        return self.spawn_result

    def run_case_digest(self, state, *, session_id, cancelled=None):
        self.digest_calls.append(session_id)
        return {"points": ["既往对话要点"]}


class _UnusedBackend:
    name = "unused-test-only"

    def decide(self, session_id, messages):  # pragma: no cover
        raise RuntimeError("no scripted command")


def _engine(backend=None):
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=backend or _UnusedBackend(),
    )


def _spawn_result(kind, tendency=None):
    branch = {
        "subagent_id": "sub-1",
        "branch_id": "b1",
        "task_kind": kind,
        "status": "completed",
        "conclusion": "独立复核意见",
        "findings": [],
    }
    if tendency is not None:
        branch["disposition_tendency"] = tendency
    return {
        "ok": True,
        "spawn_id": "spawn-1",
        "task_kind": kind,
        "branches": [branch],
        "usage_total": {"total_tokens": 10},
    }


def _spawn_command(kind):
    return {
        "tool": "spawn_subagent",
        "arguments": {
            "task_kind": kind,
            "objective": "独立复核",
            "branches": [{"branch_id": "b1", "objective": "复核", "scope": {}}],
        },
    }


class AdvertisedCatalogPerKindTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()

    def _advertised_names(self):
        context = self.engine._effective_static_context()
        return [tool["name"] for tool in context["tools"]]

    def test_kind_override_advertises_despite_global_off(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="off", kind_modes={"second_opinion": "shadow"}
        )
        self.assertTrue(self.engine._spawn_advertised())
        self.assertIn("spawn_subagent", self._advertised_names())

    def test_controller_and_server_kinds_do_not_advertise(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="advisory",
            kind_modes={
                "evidence_lookup": "off",
                "second_opinion": "off",
                # case_digest / followup_batch stay advisory via the
                # global fallback — they must not advertise the tool.
            },
        )
        self.assertFalse(self.engine._spawn_advertised())
        self.assertNotIn("spawn_subagent", self._advertised_names())

    def test_case_digest_only_advisory_does_not_advertise(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="off", kind_modes={"case_digest": "advisory"}
        )
        self.assertFalse(self.engine._spawn_advertised())

    def test_evidence_lookup_override_advertises(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="off", kind_modes={"evidence_lookup": "advisory"}
        )
        self.assertTrue(self.engine._spawn_advertised())

    def test_legacy_coordinator_falls_back_to_global_mode(self):
        self.engine.subagent_coordinator = _LegacyCoordinator("advisory")
        self.assertTrue(self.engine._spawn_advertised())
        self.engine.subagent_coordinator = _LegacyCoordinator("off")
        self.assertFalse(self.engine._spawn_advertised())

    def test_no_coordinator_does_not_advertise(self):
        self.assertFalse(self.engine._spawn_advertised())


class SpawnBranchPerKindTests(unittest.TestCase):
    def setUp(self):
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=_UnusedBackend(),
        )
        self.state, _ = self.engine.create_session()
        self.state.risk.level = RiskLevel.ROUTINE

    def _disagreement_events(self):
        return [
            event
            for event in self.events.for_session(self.state.session_id)
            if event.event_type == "subagent.disagreement"
        ]

    def test_kind_shadow_overrides_global_advisory(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="advisory",
            kind_modes={"second_opinion": "shadow"},
            spawn_result=_spawn_result("second_opinion", "urgent_today"),
        )
        result = self.engine._execute(
            self.state, _spawn_command("second_opinion"), "复核一下", "turn-1"
        )
        self.assertEqual(
            result,
            {
                "ok": True,
                "spawn": {"status": "shadow_recorded", "spawn_id": "spawn-1"},
            },
        )
        self.assertEqual(self._disagreement_events(), [])

    def test_kind_advisory_overrides_global_shadow(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="shadow",
            kind_modes={"second_opinion": "advisory"},
            spawn_result=_spawn_result("second_opinion", "routine_visit"),
        )
        result = self.engine._execute(
            self.state, _spawn_command("second_opinion"), "复核一下", "turn-1"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            result["spawn"]["branches"][0]["conclusion"], "独立复核意见"
        )

    def test_disagreement_flag_also_emits_event(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="shadow",
            kind_modes={"second_opinion": "advisory"},
            spawn_result=_spawn_result("second_opinion", "urgent_today"),
        )
        result = self.engine._execute(
            self.state, _spawn_command("second_opinion"), "复核一下", "turn-1"
        )
        self.assertTrue(result["spawn"]["second_opinion_disagreement"])
        self.assertEqual(
            result["spawn"]["second_opinion_tendency"], "urgent_today"
        )
        events = self._disagreement_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0].payload,
            {
                "task_kind": "second_opinion",
                "tendency": "urgent_today",
                "risk_group": "routine",
            },
        )
        self.assertEqual(events[0].turn_id, "turn-1")

    def test_agreement_emits_no_event(self):
        self.engine.subagent_coordinator = _KindCoordinator(
            mode="advisory",
            spawn_result=_spawn_result("second_opinion", "routine_visit"),
        )
        result = self.engine._execute(
            self.state, _spawn_command("second_opinion"), "复核一下", "turn-1"
        )
        self.assertNotIn("second_opinion_disagreement", result["spawn"])
        self.assertEqual(self._disagreement_events(), [])


class DigestTriggerPerKindTests(unittest.TestCase):
    def _run_turn(self, coordinator):
        backend = ScriptBackend()
        engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        engine.subagent_coordinator = coordinator
        state, _ = engine.create_session()
        state.transcript = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"历史{i}"}
            for i in range(17)
        ]
        backend.commands = [
            cmd(
                "record_facts",
                facts=[fact("consult_for", "self", "我自己")],
            ),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        engine.submit(state.session_id, UserInput("我自己", "self"))
        return engine, state

    def test_case_digest_kind_advisory_triggers_despite_global_shadow(self):
        coordinator = _KindCoordinator(
            mode="shadow", kind_modes={"case_digest": "advisory"}
        )
        _engine_used, state = self._run_turn(coordinator)
        self.assertEqual(len(coordinator.digest_calls), 1)
        self.assertEqual(state.subagent_digest.get("status"), "ready")

    def test_case_digest_kind_off_suppresses_despite_global_advisory(self):
        coordinator = _KindCoordinator(
            mode="advisory", kind_modes={"case_digest": "off"}
        )
        _engine_used, state = self._run_turn(coordinator)
        self.assertEqual(coordinator.digest_calls, [])
        self.assertFalse(state.subagent_digest)

    def test_legacy_coordinator_digest_uses_global_mode(self):
        coordinator = _LegacyCoordinator("advisory")
        _engine_used, state = self._run_turn(coordinator)
        self.assertEqual(len(coordinator.digest_calls), 1)
        self.assertEqual(state.subagent_digest.get("status"), "ready")


def _chief_fact(value, evidence):
    return Fact(
        key="chief_complaint",
        value=value,
        assertion=Assertion.PRESENT,
        source=Source.PATIENT,
        turn_id="turn-1",
        evidence=evidence,
    )


class AppliesCorpusTests(unittest.TestCase):
    def setUp(self):
        self.content = ContentRepository()
        self.gi = self.content.question("gi_details")
        self.head = self.content.question("head_details")

    def _state(self, value, evidence, history=()):
        state = CaseState(session_id="case-applies")
        state.facts["chief_complaint"] = _chief_fact(value, evidence)
        state.history.extend(history)
        return state

    def test_normalized_value_misses_but_verbatim_evidence_applies(self):
        # The recorded failure shape: the model restated the complaint
        # and the applies term stopped matching the value alone.
        self.assertFalse(
            self.content.question_applies(self.gi, "肚子时不时不舒服")
        )
        state = self._state("肚子时不时不舒服", "肚子不舒服")
        self.assertTrue(self.content.question_applies_in_state(self.gi, state))

    def test_candidate_questions_use_the_widened_corpus(self):
        engine = _engine()
        state, _ = engine.create_session()
        state.facts["chief_complaint"] = _chief_fact(
            "肚子时不时不舒服", "肚子不舒服"
        )
        candidate_ids = [
            q.id for q in engine._candidate_questions(state, "肚子时不时不舒服")
        ]
        self.assertIn("gi_details", candidate_ids)

    def test_unrelated_complaint_still_rejected(self):
        state = self._state("头痛", "头痛，脖子有点僵")
        self.assertFalse(self.content.question_applies_in_state(self.gi, state))
        self.assertTrue(self.content.question_applies_in_state(self.head, state))

    def test_denied_utterance_does_not_apply(self):
        state = self._state("胃胀", "不是肚子不舒服，是胃胀")
        self.assertFalse(self.content.question_applies_in_state(self.gi, state))

    def test_term_matcher_denial_construction(self):
        self.assertFalse(_applies_term_match("不是肚子不舒服，是胃胀", "肚子不舒服"))
        self.assertFalse(_applies_term_match("我不是肚子不舒服", "肚子不舒服"))
        self.assertTrue(_applies_term_match("肚子不舒服，还有点恶心", "肚子不舒服"))
        # A later, genuinely affirmed mention still counts after a
        # denied one.
        self.assertTrue(
            _applies_term_match("不是胃的问题，是肚子不舒服", "肚子不舒服")
        )
        # Negation cues keep their existing semantics.
        self.assertFalse(_applies_term_match("没有肚子不舒服", "肚子不舒服"))

    def test_superseded_evidence_counts_after_correction(self):
        history_fact = _chief_fact("肚子不舒服", "肚子不舒服")
        state = self._state(
            "肚子时不时不舒服", "肚子时不时不舒服", history=[history_fact]
        )
        self.assertTrue(self.content.question_applies_in_state(self.gi, state))

    def test_history_alone_without_current_fact_does_not_apply(self):
        state = CaseState(session_id="case-wiped")
        state.history.append(_chief_fact("肚子不舒服", "肚子不舒服"))
        self.assertFalse(self.content.question_applies_in_state(self.gi, state))

    def test_no_chief_fact_at_all_does_not_apply(self):
        state = CaseState(session_id="case-empty")
        self.assertFalse(self.content.question_applies_in_state(self.gi, state))
        # Questions without applies terms stay unconditional.
        sex = self.content.question("sex")
        self.assertTrue(self.content.question_applies_in_state(sex, state))


if __name__ == "__main__":
    unittest.main()
