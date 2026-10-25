"""Batch A (W2): pathway vocabulary alignment + off-pathway neutral fallback.

Covers G-09 (gi_details missed 「腹疼」 while the abdominal pathway
accepted it), the batch-A audit of the same colloquial-variant gap in
every other pathway detail question, the G-09 temperature note (fever
recorded only under associated_symptoms must make
reported_temperature requestable), the O-09/N-07 colloquial evidence
terms, and the neutral off-pathway conclusion fallback
(``apply_neutral_fallback``) that must not borrow nearest-pathway
template language.

Deliberately NOT covered here (clinical sign-off territory, recorded
for review rather than fixed): escalation of N-07 / P-13 / Y-12 / T-12
to emergency/urgent tiers — no new red-flag signal or threshold is
introduced by this batch.
"""
from __future__ import annotations

import json
import unittest
from importlib import resources

from medagent import conclusion as conclusion_module
from medagent.conclusion import (
    RETURN_NOW_IF,
    apply_neutral_fallback,
    chief_matches_pathway,
    compose_conclusion,
    emergency_explanation,
)
from medagent.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.knowledge import ContentRepository
from medagent.reasoning import RuleBasedProposer, validate_workspace


def closed_state(chief, facts=()):
    """facts: (key, value) PRESENT pairs, or (key, value, assertion)."""
    state = CaseState(session_id="batch-a-vocab-test")
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(RiskLevel.ROUTINE)
    state.record(
        Fact(
            key="chief_complaint",
            value=chief,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=chief,
        )
    )
    for item in facts:
        key, value = item[0], item[1]
        assertion = item[2] if len(item) > 2 else Assertion.PRESENT
        state.record(
            Fact(
                key=key,
                value=value,
                assertion=assertion,
                source=Source.PATIENT,
                turn_id="t1",
                evidence=value if isinstance(value, str) else "",
            )
        )
    return state


def load_content(name):
    return json.loads(
        resources.files("medagent.content").joinpath(name).read_text(encoding="utf-8")
    )


REPO = ContentRepository()


def active_hypothesis_ids(state):
    accepted, _ = validate_workspace(state, RuleBasedProposer().propose(state))
    return {h.id: h.status for h in accepted.active()}


class GiVocabularyTests(unittest.TestCase):
    def test_g09_right_upper_abdominal_pain_applies(self):
        # Live G-09 chief, recorded verbatim: pathway fired (腹疼) but
        # gi_details never entered the candidate set.
        state = closed_state("右上腹疼")
        self.assertTrue(
            REPO.question_applies_in_state(REPO.question("gi_details"), state)
        )

    def test_gi_colloquial_variants_apply(self):
        for chief in (
            "腹疼",
            "腹部疼",
            "下腹疼",
            "便血两次",
            "拉稀一天",
            "大便发黑",
        ):
            with self.subTest(chief=chief):
                state = closed_state(chief)
                self.assertTrue(
                    REPO.question_applies_in_state(
                        REPO.question("gi_details"), state
                    )
                )

    def test_pathway_detail_parity_audit(self):
        """Every pathway trigger term must be accepted by at least one
        of that pathway's detail questions — the G-09 shape (pathway
        fires, detail question never offered) must not recur in the
        pain/stool/bleed class — except for a documented exclusion
        set, where the narrower catalog is pinned behaviour and the
        pathway close-gate (not the question) is the safety net:

        * 反酸 / 烧心 / 胃胀: distinct upper-GI symptoms, not
          colloquial variants of abdominal pain; pinned as
          non-applying by test_pathway_close_gate (the gate refuses
          summary until gi_details is probed or abstained).
        * bare 肚子: deliberately not a term — it would make every
          drifted restatement (「肚子时不时不舒服」) match by value
          alone and collapse the value/evidence distinction pinned by
          test_kind_modes_and_applies. Every 肚子-prefixed
          presentation is covered by a specific term instead
          (肚子疼/痛/不舒服/难受/胀).
        Chest has no detail question by design and is skipped.
        """
        pathways = {
            p["id"]: set(p["chief_complaint_contains_any"])
            for p in load_content("differentials.json")["pathways"]
        }
        detail_questions = {
            "respiratory": ["resp_breathing", "resp_cough_details"],
            "abdominal": ["gi_details"],
            "headache": ["head_details"],
            "dizziness": ["dizzy_details"],
            "low_back": ["back_details"],
            "urinary": ["urinary_details"],
            "skin": ["skin_details"],
        }
        questions = {
            q["id"]: set(
                q.get("applies_when", {}).get("chief_complaint_contains_any", [])
            )
            for q in load_content("questions.json")["questions"]
        }
        exclusions = {"反酸", "烧心", "胃胀", "肚子"}
        for pathway_id, terms in pathways.items():
            if pathway_id not in detail_questions:
                continue
            covered = set().union(
                *(questions[qid] for qid in detail_questions[pathway_id])
            )
            # Substring coverage: a term is covered when a detail term
            # is a substring of it or vice versa (e.g. detail "腹疼"
            # covers pathway "下腹疼"; detail "小便" covers "小便疼").
            missing = [
                term
                for term in terms
                if term not in exclusions
                and not any(
                    detail in term or term in detail for detail in covered
                )
            ]
            self.assertEqual(missing, [], f"pathway {pathway_id}")


class TemperatureAppliesTests(unittest.TestCase):
    def test_fever_in_associated_symptoms_makes_temperature_askable(self):
        # G-09 note: fever lived only in associated_symptoms, so the
        # temperature question was never requestable.
        state = closed_state(
            "右上腹疼",
            [("associated_symptoms", "发烧、恶心、吃不下东西")],
        )
        self.assertTrue(
            REPO.question_applies_in_state(
                REPO.question("reported_temperature"), state
            )
        )

    def test_denied_fever_does_not_trigger_temperature(self):
        state = closed_state(
            "右上腹疼",
            [("associated_symptoms", None, Assertion.ABSENT)],
        )
        self.assertFalse(
            REPO.question_applies_in_state(
                REPO.question("reported_temperature"), state
            )
        )
        state = closed_state(
            "右上腹疼",
            [("associated_symptoms", "没有发烧")],
        )
        self.assertFalse(
            REPO.question_applies_in_state(
                REPO.question("reported_temperature"), state
            )
        )


class ColloquialEvidenceTests(unittest.TestCase):
    def test_o09_cauda_colloquial_phrasing_activates(self):
        # Live O-09 facts, verbatim: previously no hypothesis activated
        # and the sheet fell through to self_care.
        state = closed_state(
            "腰疼，大小便费劲，屁股那块发麻",
            [
                ("back_details", "小便解不干净，大便费劲，屁股那块发麻，没有发烧"),
                ("severity", 7),
            ],
        )
        self.assertIn("cauda_serious", active_hypothesis_ids(state))
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["disposition"]["level"], "routine_visit")

    def test_cauda_threshold_unchanged(self):
        # No new clinical threshold: the severity gte-8 support
        # condition is untouched; only colloquial evidence terms grew.
        for pathway in load_content("differentials.json")["pathways"]:
            for hypothesis in pathway["hypotheses"]:
                if hypothesis["id"] == "cauda_serious":
                    self.assertIn(
                        {"fact_key": "severity", "gte": 8},
                        hypothesis["support"],
                    )

    def test_n07_focal_weakness_colloquial_activates(self):
        state = closed_state(
            "一边胳膊没劲，说话也不清楚，头晕",
            [("dizzy_details", "一边胳膊没劲，说话不清楚，头晕半小时前突然开始")],
        )
        self.assertIn("posterior_stroke", active_hypothesis_ids(state))

    def test_dizzy_prompt_asks_focal_weakness(self):
        prompt = next(
            q["prompt"]
            for q in load_content("questions.json")["questions"]
            if q["id"] == "dizzy_details"
        )
        self.assertIn("一边胳膊或腿突然没劲", prompt)
        self.assertIn("持续多久", prompt)


class NeutralFallbackTests(unittest.TestCase):
    def test_skin_pathway_narrowed_off_gum_and_eyelid(self):
        # Bare 「红肿」 used to hand gum bleeding and an eyelid lump to
        # the skin pathway; only skin-anchored redness still triggers it.
        self.assertFalse(chief_matches_pathway("刷牙总出血，牙龈红肿，咬苹果也出血"))
        self.assertFalse(chief_matches_pathway("眼皮上长了个红肿的小包，碰着疼"))
        self.assertTrue(chief_matches_pathway("皮肤红肿一片，很痒"))
        self.assertTrue(chief_matches_pathway("身上红肿一片"))

    def test_gum_bleeding_sheet_is_neutral(self):
        state = closed_state(
            "刷牙总出血，牙龈红肿，咬苹果也出血",
            [("skin_details", "牙龈红肿出血")],
        )
        sheet = compose_conclusion(state)
        self.assertTrue(sheet["neutral_fallback"])
        self.assertIn("身体", sheet["narrative"]["framing"])
        self.assertNotIn("皮肤", sheet["narrative"]["framing"])
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_sudden_vision_and_hearing_sheets_are_neutral_not_mild(self):
        # Y-12 / T-12 shape: off-pathway sheets must not assert
        # 「程度较轻」 from the generic template against a 7/6 severity.
        for chief, facts in (
            (
                "眼前突然闪光，还感觉一块黑影挡着视野",
                [("associated_symptoms", "视力下降了，没有眼痛"), ("severity", 7)],
            ),
            (
                "一只耳朵突然听不见了，还嗡嗡响",
                [("associated_symptoms", "轻微头晕"), ("severity", 6)],
            ),
        ):
            with self.subTest(chief=chief):
                sheet = compose_conclusion(closed_state(chief, facts))
                self.assertTrue(sheet["neutral_fallback"])
                self.assertNotIn("程度较轻", sheet["narrative"]["severity"])
                self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_apply_neutral_fallback_direct(self):
        state = closed_state(
            "腰痛三天，搬东西扭了一下",
            [
                (
                    "back_details",
                    "疼会往右腿窜，大小便都正常，会阴不麻，也没发烧。",
                ),
                ("trajectory", "stable"),
                ("severity", 7),
            ],
        )
        sheet = compose_conclusion(state)
        self.assertEqual(
            sheet["return_now_if"],
            ["大小便异常", "会阴麻木新出现或加重", "下肢无力加重"],
        )
        neutral = apply_neutral_fallback(sheet)
        self.assertEqual(neutral["return_now_if"], RETURN_NOW_IF)
        self.assertIn("身体", neutral["narrative"]["framing"])
        self.assertEqual(
            neutral["self_care"]["do"],
            ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
        )
        # Untouched: disposition, directions, department hint.
        self.assertEqual(neutral["disposition"], sheet["disposition"])
        self.assertEqual(neutral["directions"], sheet["directions"])
        self.assertEqual(
            neutral["disposition"].get("department_hint"),
            sheet["disposition"].get("department_hint"),
        )
        self.assertTrue(neutral["neutral_fallback"])
        # Idempotent.
        self.assertEqual(apply_neutral_fallback(neutral), neutral)
        # Original sheet is not mutated.
        self.assertNotIn("neutral_fallback", sheet)

    def test_emergency_explanation_untouched(self):
        self.assertEqual(
            emergency_explanation(["大量或无法控制的出血"]),
            "你描述的情况包含需要立即处理的危险信号（大量或无法控制的出血）。"
            "请现在拨打 120，等待时保持安静休息，不要自行开车，有家人陪同更好。",
        )


class GoalLineTests(unittest.TestCase):
    def test_goal_line_filled_from_patient_goal(self):
        state = closed_state(
            "心跳忽快忽慢，休息时也能感觉到心慌",
            [("patient_goal", "想知道要不要去检查")],
        )
        self.assertEqual(
            compose_conclusion(state)["goal_line"], "想知道要不要去检查"
        )
        self.assertIsNone(compose_conclusion(closed_state("膝盖疼一周"))["goal_line"])


class SkinPathwayNarrowingRegressionTests(unittest.TestCase):
    """RW1 rework of the W2 skin narrowing (release-gate regression).

    W2 replaced bare 「红肿」 with 「皮肤红肿/身上红肿」 only, which
    dropped two conclusion-corpus must-not-miss cases whose chiefs
    name a body site (「腿上红肿越来越大」,
    「脚上红肿」). The fix restores site-qualified 「红肿」 terms
    instead of the bare term: the skin pathway fires for a named skin
    site, while gum (牙龈) and eyelid (眼皮) phrasings still stay off
    it. The W2 tests above are untouched; these pins hold both ends.

    Residual risk, stated plainly (do not hide it): this is the
    enumeration fallback, not true exclusion. A site-less chief such
    as bare 「红肿」 still matches nothing, and any body site not in
    the list (e.g. 「臀部红肿」 phrased without an enumerated site)
    still misses the pathway. True exclusion — bare 「红肿」 plus a
    gum/eyelid-context exclusion honoured by the pathway matchers —
    needs wiring in the matchers themselves (reasoning.py proposer,
    conclusion.py chief_matches_pathway, agent.py exploration), which
    is outside this rework's file set; until that lands, extending
    this list is the only in-scope lever.
    """

    def test_spreading_leg_and_diabetic_foot_chiefs_match_skin(self):
        self.assertTrue(chief_matches_pathway("腿上红肿越来越大"))
        self.assertTrue(chief_matches_pathway("脚上红肿"))

    def test_other_qualified_sites_match_and_bare_redness_does_not(self):
        for chief in (
            "手上红肿一片",
            "脸上红肿",
            "脚红肿，走路疼",
            "红肿扩散，越来越大",
        ):
            with self.subTest(chief=chief):
                self.assertTrue(chief_matches_pathway(chief))
        # Pinned enumeration gap: site-less redness matches nothing.
        self.assertFalse(chief_matches_pathway("红肿"))

    def test_gum_and_eyelid_chiefs_still_off_skin_pathway(self):
        self.assertFalse(chief_matches_pathway("刷牙总出血，牙龈红肿，咬苹果也出血"))
        self.assertFalse(chief_matches_pathway("眼皮上长了个红肿的小包，碰着疼"))

    def test_spreading_leg_sheet_is_routine_visit_with_skin_infection(self):
        # Conclusion-corpus skin-infection-spreading, verbatim facts.
        state = closed_state(
            "腿上红肿越来越大",
            [
                ("associated_symptoms", "有点发烧，腿发烫"),
                ("skin_details", "红肿热痛，红肿扩散，破溃流脓"),
                ("severity", 4),
            ],
        )
        self.assertIn("skin_infection", active_hypothesis_ids(state))
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["disposition"]["level"], "routine_visit")
        self.assertTrue(sheet["directions"])
        self.assertIn(
            "skin_infection",
            [d["hypothesis_id"] for d in sheet["directions"]],
        )

    def test_diabetic_foot_sheet_is_routine_visit_with_skin_infection(self):
        # Conclusion-corpus skin-infection-diabetic-foot, verbatim facts.
        state = closed_state(
            "脚上红肿",
            [
                ("relevant_history", "糖尿病"),
                ("skin_details", "红肿扩散，疼痛，发热"),
                ("severity", 4),
            ],
        )
        self.assertIn("skin_infection", active_hypothesis_ids(state))
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["disposition"]["level"], "routine_visit")
        self.assertTrue(sheet["directions"])
        self.assertIn(
            "skin_infection",
            [d["hypothesis_id"] for d in sheet["directions"]],
        )

    def test_skin_details_applies_mirrors_narrowed_pathway(self):
        # Task item 2: skin_details no longer applies on the bare
        # 「红肿」 that W2 left in questions.json while narrowing the
        # pathway — gum/eyelid chiefs must not be asked skin details,
        # and the two regression chiefs must be.
        for chief, expected in (
            ("腿上红肿越来越大", True),
            ("脚上红肿", True),
            ("刷牙总出血，牙龈红肿，咬苹果也出血", False),
            ("眼皮上长了个红肿的小包，碰着疼", False),
        ):
            with self.subTest(chief=chief):
                self.assertEqual(
                    REPO.question_applies_in_state(
                        REPO.question("skin_details"), closed_state(chief)
                    ),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
