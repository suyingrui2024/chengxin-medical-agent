"""Hypothesis-driven ranking (implementation plan P0).

Covers the P0 contract: deterministic ranking, HISTORY group order
(deep-dive before associated_symptoms, urgent minimum facts first),
falsifiable-question promotion, the off-pathway P3 hook (defensive
import + degradation), unchanged candidate filtering (engine-level
asked-set regression), the adaptive-engine order check, and the
S1/G1/E3 focused pre-registration cases.

S1/G1/E3 note: verbatim round-2 transcripts were not located in
scratch, so these cases are built from the documented round-2 case
facts in ``~/workspace/scratch/patient-time-simulation.md`` §二
(S1 migratory right-lower abdominal pain, G1 adult acute
gastroenteritis with diarrhoea, E3 off-pathway gradual weight gain).
They pin deep-dive-first and dynamic reordering; the focused live-eval
comparison itself remains a supervisor step.
"""

from __future__ import annotations

import copy
import json
import unittest
from importlib import resources

from medagent import consultation as consultation_module
from medagent import hypothesis_ranking as hr_module
from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError
from medagent.conclusion import chief_matches_pathway
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
from medagent.events import EventStore
from medagent.hypothesis_ranking import (
    URGENT_MINIMUM_FACTS,
    HypothesisRanker,
)
from medagent.knowledge import ContentRepository, Question
from medagent.planner import RulePlanner
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

_CONTENT = ContentRepository()
_BY_ID = {question.id: question for question in _CONTENT.questions}

HISTORY_S1 = ["onset", "trajectory", "severity", "associated_symptoms", "gi_details"]

# Scripted answers for the respiratory engine-regression run (same shape
# as the reasoning tests' driver).
ANSWERS = {
    "consult_for": ("我自己", "self"),
    "sex": ("男", "male"),
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
    "reported_temperature": ("没量过", None),
    "medications_taken": ("没有", None),
    "allergies": ("没有", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想快点好", None),
}


def _fact(key, value):
    return Fact(
        key=key,
        value=value,
        assertion=Assertion.PRESENT,
        source=Source.PATIENT,
        turn_id="t0",
        evidence=value if isinstance(value, str) else None,
    )


def _state(chief, extras=(), risk=None):
    state = CaseState(session_id="ranker-test")
    for key, value in [
        ("consult_for", "self"),
        ("sex", "male"),
        ("age_years", 32),
        ("chief_complaint", chief),
        ("emergency_screen", "no"),
    ]:
        state.record(_fact(key, value))
    for key, value in extras:
        state.record(_fact(key, value))
    if risk is not None:
        state.risk = risk
    return state


def _questions(ids):
    return [_BY_ID[question_id] for question_id in ids]


def _differentials():
    raw = (
        resources.files("medagent.content")
        .joinpath("differentials.json")
        .read_text(encoding="utf-8")
    )
    return json.loads(raw)["pathways"]


def _rule_engine(ranker):
    engine = ConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=ranker,
    )
    engine.planner = RulePlanner(engine)
    return engine


def _drive(engine):
    state, reply = engine.create_session()
    asked = []
    for _ in range(30):
        if reply.kind in {"summary", "emergency", "urgent", "closed"}:
            break
        if reply.kind == "self_check":
            reply = engine.submit(
                state.session_id, UserInput("暂时跳过", "skip")
            )
            continue
        asked.append(reply.question_id)
        text, choice = ANSWERS[reply.question_id]
        reply = engine.submit(state.session_id, UserInput(text, choice))
    return reply, asked


class _StubBackend:
    name = "stub-test-only"

    def decide(self, session_id, messages):
        raise RuntimeError("unused by the ask_question check")


def _adaptive_engine():
    return AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=HypothesisRanker(season_override="spring"),
        backend=_StubBackend(),
    )


class RankerDeterminismTests(unittest.TestCase):
    def test_same_input_twice_same_pick(self):
        ranker = HypothesisRanker(season_override="winter")
        state = _state("转移性右下腹痛")
        candidates = _questions(HISTORY_S1)
        self.assertEqual(
            ranker.choose(state, candidates), ranker.choose(state, candidates)
        )
        self.assertEqual(
            ranker.order(state, candidates), ranker.order(state, candidates)
        )

    def test_missing_age_resolves_to_adult_and_stays_deterministic(self):
        ranker = HypothesisRanker(season_override="spring")
        state = CaseState(session_id="no-age")
        state.record(_fact("chief_complaint", "咳嗽发烧两天"))
        self.assertEqual(ranker._age_band(state), "adult")
        candidates = _questions(HISTORY_S1)
        self.assertEqual(
            ranker.choose(state, candidates), ranker.choose(state, candidates)
        )

    def test_age_band_resolution(self):
        ranker = HypothesisRanker(season_override="spring")

        def band(facts):
            state = CaseState(session_id="age-band")
            for key, value in facts:
                state.record(_fact(key, value))
            return ranker._age_band(state)

        # Contract key first, then the actually recorded age_years key.
        self.assertEqual(band([("age", 8)]), "child")
        self.assertEqual(band([("age_years", 17)]), "child")
        self.assertEqual(band([("age_years", 18)]), "adult")
        self.assertEqual(band([("age_years", 64)]), "adult")
        self.assertEqual(band([("age_years", 65)]), "older")
        self.assertEqual(band([("age_years", "32岁")]), "adult")
        self.assertEqual(band([]), "adult")

    def test_season_weight_lookup_and_missing_season_degradation(self):
        ranker = HypothesisRanker(season_override="spring")
        weights = copy.deepcopy(ranker._weights)
        weights["seasons"]["spring"]["respiratory"] = 2.0
        ranker._weights = weights
        self.assertEqual(ranker._weight("seasons", "spring", "respiratory"), 2.0)
        # A season key absent from the table degrades to equal weights:
        # it must NOT silently reuse another season's weights.
        del ranker._weights["seasons"]["winter"]
        self.assertEqual(ranker._weight("seasons", "winter", "respiratory"), 1.0)
        self.assertEqual(ranker._weight("age_weights", "no_such_band", "chest"), 1.0)

    def test_missing_season_key_keeps_ranking_equal_weighted(self):
        baseline = HypothesisRanker(season_override="spring")
        state = _state(
            "咳嗽发烧两天", extras=[("resp_cough_details", "干咳为主")]
        )
        candidates = _questions(
            [
                "onset",
                "trajectory",
                "severity",
                "associated_symptoms",
                "resp_breathing",
                "resp_cough_details",
                "resp_intake",
                "reported_temperature",
            ]
        )
        baseline_scores = dict(baseline.ranked_hypotheses(state))
        degraded = HypothesisRanker(season_override="winter")
        weights = copy.deepcopy(degraded._weights)
        del weights["seasons"]["winter"]
        degraded._weights = weights
        degraded_scores = dict(degraded.ranked_hypotheses(state))
        self.assertEqual(baseline_scores, degraded_scores)

    def test_weights_table_schema_all_ones(self):
        raw = (
            resources.files("medagent.content")
            .joinpath("prior_weights.json")
            .read_text(encoding="utf-8")
        )
        table = json.loads(raw)
        self.assertEqual(table["version"], "1.0.0")
        domains = {p["id"] for p in _differentials()} | {"general"}
        for season in ("spring", "summer", "autumn", "winter"):
            self.assertEqual(set(table["seasons"][season]), domains)
            self.assertTrue(all(v == 1.0 for v in table["seasons"][season].values()))
        bands = table["age_bands"]
        self.assertEqual(
            [(b["min_age"], b["max_age"]) for b in bands],
            [(0, 17), (18, 64), (65, 150)],
        )
        for band in bands:
            self.assertEqual(set(table["age_weights"][band["name"]]), domains)
            self.assertTrue(
                all(v == 1.0 for v in table["age_weights"][band["name"]].values())
            )


class RankerOrderTests(unittest.TestCase):
    def test_deep_dive_before_associated_symptoms(self):
        ranker = HypothesisRanker(season_override="spring")
        state = _state("转移性右下腹痛")
        ordered = ranker.order(state, _questions(HISTORY_S1))
        deep = {"onset", "trajectory", "severity", "gi_details"}
        self.assertTrue(
            all(ordered.index(q) < ordered.index("associated_symptoms") for q in deep)
        )
        self.assertIn(ranker.choose(state, _questions(HISTORY_S1)), deep)

    def test_urgent_missing_minimum_facts_first(self):
        ranker = HypothesisRanker(season_override="spring")
        risk = RiskAssessment(level=RiskLevel.URGENT, triggers=["chest_pain"])
        state = _state("胸口疼", risk=risk)
        ordered = ranker.order(state, _questions(HISTORY_S1))
        # URGENT_MINIMUM_FACTS order: severity, associated_symptoms, ...
        self.assertEqual(ordered[:2], ["severity", "associated_symptoms"])
        # Once severity is recorded (hence no longer a candidate), the
        # remaining missing minimum fact still leads.
        state2 = _state("胸口疼", extras=[("severity", "8分")], risk=risk)
        ordered2 = ranker.order(
            state2, _questions(["onset", "trajectory", "associated_symptoms"])
        )
        self.assertEqual(ordered2[0], "associated_symptoms")

    def test_urgent_minimum_facts_mirror_consultation(self):
        self.assertEqual(
            URGENT_MINIMUM_FACTS, consultation_module.URGENT_MINIMUM_FACTS
        )

    def test_non_history_phases_keep_catalog_order(self):
        ranker = HypothesisRanker(season_override="spring")
        state = _state("咳嗽发烧两天")
        opening = _questions(["consult_for", "sex", "age", "chief_complaint"])
        self.assertEqual(
            ranker.order(state, opening),
            ["consult_for", "sex", "age", "chief_complaint"],
        )
        background = _questions(
            ["medications_taken", "allergies", "relevant_history", "patient_goal"]
        )
        self.assertEqual(
            ranker.order(state, background),
            ["medications_taken", "allergies", "relevant_history", "patient_goal"],
        )

    def test_choose_empty_candidates_none(self):
        ranker = HypothesisRanker(season_override="spring")
        self.assertIsNone(ranker.choose(_state("咳嗽"), []))
        self.assertEqual(ranker.order(_state("咳嗽"), []), [])

    def test_catalog_order_breaks_ties_not_passed_order(self):
        # Respiratory state: trajectory and resp_breathing both score 5,
        # severity scores 2; catalog order (trajectory before
        # resp_breathing) must decide the tie even when candidates are
        # passed in reverse catalog order.
        ranker = HypothesisRanker(season_override="spring")
        state = _state(
            "咳嗽发烧两天", extras=[("resp_cough_details", "干咳为主")]
        )
        candidates = _questions(
            [
                "resp_breathing",
                "severity",
                "trajectory",
                "onset",
                "reported_temperature",
            ]
        )
        ordered = ranker.order(state, candidates)
        self.assertLess(ordered.index("trajectory"), ordered.index("resp_breathing"))
        self.assertLess(ordered.index("resp_breathing"), ordered.index("severity"))

    def test_falsifiable_promotion_within_deep_dive_group(self):
        # Ground truth from differentials.json: trajectory is in uri's
        # refute conditions and uri is top-3 here, so trajectory is
        # falsifiable; onset is not. Catalog order has onset BEFORE
        # trajectory, so trajectory-first proves score promotion.
        ranker = HypothesisRanker(season_override="spring")
        state = _state(
            "咳嗽发烧两天", extras=[("resp_cough_details", "干咳为主")]
        )
        ranked = [hid for hid, _ in ranker.ranked_hypotheses(state)]
        self.assertIn("uri", ranked[:3])
        ordered = ranker.order(state, _questions(["onset", "trajectory", "severity"]))
        self.assertLess(ordered.index("trajectory"), ordered.index("onset"))

    def test_proactive_section_contains_a_falsifiable_question(self):
        # Chest + stable trajectory: non_cardiac_chest enters the top-3
        # and its refute conditions cite associated_symptoms, so the
        # associated_symptoms question (proactive group 3) is falsifiable
        # by the contract's first clause. Recomputed here from the JSON
        # rather than trusting the ranker's own predicate.
        ranker = HypothesisRanker(season_override="spring")
        state = _state("胸口疼", extras=[("trajectory", "stable")])
        ranked = [hid for hid, _ in ranker.ranked_hypotheses(state)]
        self.assertIn("non_cardiac_chest", ranked[:3])
        refute_keys = set()
        for pathway in _differentials():
            for hyp in pathway["hypotheses"]:
                if hyp["id"] in ranked[:3]:
                    refute_keys |= {
                        c["fact_key"] for c in hyp.get("refute", [])
                    }
        self.assertIn("associated_symptoms", refute_keys)
        ordered = ranker.order(state, _questions(HISTORY_S1))
        proactive = ordered[ordered.index("associated_symptoms"):]
        self.assertIn("associated_symptoms", proactive)

    def test_off_pathway_hook_with_real_p3_degrades_without_ids(self):
        # P3's warning_questions returns {"domain", "question"} dicts
        # with no question id, so they cannot enter choose()'s id
        # stream; ranking must stay deterministic and must not crash.
        ranker = HypothesisRanker(season_override="spring")
        chief = "体重一直在慢慢涨"
        self.assertFalse(chief_matches_pathway(chief))
        warnings = ranker.off_pathway_warnings(chief)
        self.assertEqual(len(warnings), 6)
        self.assertTrue(all("id" not in w for w in warnings))
        state = _state(chief)
        candidates = _questions(
            ["onset", "trajectory", "severity", "associated_symptoms"]
        )
        self.assertEqual(
            ranker.order(state, candidates), ranker.order(state, candidates)
        )
        self.assertEqual(
            set(ranker.order(state, candidates)), {c.id for c in candidates}
        )

    def test_off_pathway_hook_inserts_id_bearing_warnings_first(self):
        probe = Question(
            id="probe_warning",
            phase=Phase.HISTORY,
            fact_key="probe_warning",
            prompt="probe",
            input="text",
            options=[],
            applies_when={},
        )
        calls = []
        original = hr_module._warning_questions

        def stub(chief):
            calls.append(chief)
            return [{"id": "probe_warning", "question": "probe?"}]

        hr_module._warning_questions = stub
        try:
            ranker = HypothesisRanker(season_override="spring")
            state = _state("体重一直在慢慢涨")
            ordered = ranker.order(
                state, [probe] + _questions(["onset", "trajectory"])
            )
            self.assertEqual(ordered[0], "probe_warning")
            self.assertEqual(calls, ["体重一直在慢慢涨"])
            # On-pathway chiefs never consult the hook.
            calls.clear()
            ranker.order(_state("转移性右下腹痛"), _questions(HISTORY_S1))
            self.assertEqual(calls, [])
        finally:
            hr_module._warning_questions = original


class RankerEngineRegressionTests(unittest.TestCase):
    def test_runtime_wires_hypothesis_ranker(self):
        host = build_runtime()
        context = host.start()
        try:
            engine = context.services.require("consultation")
            self.assertIsInstance(engine.ranker, HypothesisRanker)
        finally:
            host.stop()

    def test_candidate_filtering_unchanged_under_reordering(self):
        # Same scripted answers, RulePlanner both arms: the asked SET
        # must be identical (order may differ) and no question asked
        # twice — reordering must neither add nor drop a question.
        reply_old, asked_old = _drive(_rule_engine(OrderedQuestionRanker()))
        reply_new, asked_new = _drive(_rule_engine(HypothesisRanker(season_override="spring")))
        self.assertEqual(reply_old.kind, "summary")
        self.assertEqual(reply_new.kind, "summary")
        self.assertEqual(len(asked_old), len(set(asked_old)))
        self.assertEqual(len(asked_new), len(set(asked_new)))
        self.assertEqual(set(asked_old), set(asked_new))
        self.assertIn("associated_symptoms", asked_new)
        self.assertIn("resp_breathing", asked_new)


class AdaptiveOrderCheckTests(unittest.TestCase):
    def _ask(self, engine, state, question_id):
        return engine._execute(
            state,
            {"tool": "ask_question", "arguments": {"question_id": question_id}},
            "",
            "t1",
        )

    def test_mismatched_proposal_raises_with_pick(self):
        engine = _adaptive_engine()
        state = _state("转移性右下腹痛")
        with self.assertRaises(ProposalError) as caught:
            self._ask(engine, state, "associated_symptoms")
        self.assertEqual(
            str(caught.exception),
            "按假设排序应先问 onset，当前提议 associated_symptoms 暂缓",
        )

    def test_pick_proposal_passes(self):
        engine = _adaptive_engine()
        state = _state("转移性右下腹痛")
        reply = self._ask(engine, state, "onset")
        self.assertEqual(reply.question_id, "onset")

    def test_active_question_follow_up_exempt(self):
        engine = _adaptive_engine()
        state = _state("转移性右下腹痛")
        state.active_question = "associated_symptoms"
        reply = self._ask(engine, state, "associated_symptoms")
        self.assertEqual(reply.question_id, "associated_symptoms")

    def test_clarification_round_exempt(self):
        engine = _adaptive_engine()
        state = _state("转移性右下腹痛")
        state.active_question_is_clarification = True
        reply = self._ask(engine, state, "associated_symptoms")
        self.assertEqual(reply.question_id, "associated_symptoms")

    def test_emergency_clarification_pending_exempt(self):
        engine = _adaptive_engine()
        state = _state("转移性右下腹痛")
        state.runtime_snapshot["emergency_clarification"] = {"signal": "chest"}
        reply = self._ask(engine, state, "associated_symptoms")
        self.assertEqual(reply.question_id, "associated_symptoms")


class FocusedPreregistrationTests(unittest.TestCase):
    def test_s1_deep_dive_first_and_dynamic_reorder(self):
        ranker = HypothesisRanker(season_override="spring")
        before_state = _state("转移性右下腹痛")
        candidates = _questions(HISTORY_S1)
        before = ranker.order(before_state, candidates)
        self.assertTrue(
            all(
                before.index(q) < before.index("associated_symptoms")
                for q in ("onset", "trajectory", "severity", "gi_details")
            )
        )
        # Same candidate set, new evidence: appendicitis enters the
        # ranking and the order changes (dynamic reorder, reproducible).
        after_state = _state(
            "转移性右下腹痛",
            extras=[
                ("gi_details", "右下腹痛，从肚脐周围转移过来"),
                ("trajectory", "worsening"),
            ],
        )
        ranked = ranker.ranked_hypotheses(after_state)
        self.assertEqual(ranked[0][0], "appendicitis")
        after = ranker.order(after_state, candidates)
        self.assertNotEqual(before, after)
        self.assertTrue(
            all(
                after.index(q) < after.index("associated_symptoms")
                for q in ("onset", "trajectory", "severity", "gi_details")
            )
        )
        self.assertEqual(after, ranker.order(after_state, candidates))

    def test_g1_deep_dive_first_and_no_invented_questions(self):
        ranker = HypothesisRanker(season_override="spring")
        state = _state("拉肚子")  # adult patient (age_years 32)
        self.assertEqual(ranker._age_band(state), "adult")
        candidates = _questions(HISTORY_S1)
        ordered = ranker.order(state, candidates)
        self.assertTrue(
            all(
                ordered.index(q) < ordered.index("associated_symptoms")
                for q in ("onset", "trajectory", "severity", "gi_details")
            )
        )
        # The ranker only reorders what it is given: it never invents
        # the infant question for an adult patient.
        self.assertEqual(set(ordered), {c.id for c in candidates})
        self.assertNotIn("infant_observable_signs", ordered)

    def test_e3_off_pathway_deterministic_with_warnings(self):
        ranker = HypothesisRanker(season_override="spring")
        chief = "体重一直在慢慢涨"
        self.assertFalse(chief_matches_pathway(chief))
        self.assertEqual(len(ranker.off_pathway_warnings(chief)), 6)
        state = _state(chief)
        candidates = _questions(
            ["onset", "trajectory", "severity", "associated_symptoms"]
        )
        ordered = ranker.order(state, candidates)
        self.assertEqual(ordered, ranker.order(state, candidates))
        self.assertTrue(
            all(
                ordered.index(q) < ordered.index("associated_symptoms")
                for q in ("onset", "trajectory", "severity")
            )
        )


if __name__ == "__main__":
    unittest.main()
