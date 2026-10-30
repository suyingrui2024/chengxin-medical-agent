"""P4 direction guards: quote/assertion consistency, direction anchoring,
shadow naming, narrative severity consistency, infant-question gating.

Fixtures are the second-round disease-test cases verbatim (issue rows
P-03, T-06, O-05, D-13, N-10, J-07, S-11, G-11, E-10); nothing here is
invented patient wording. Direction selection is exercised through
``_select_directions`` exactly as the batch-B tests do, and the naming
/ severity cases go through the full ``compose_conclusion`` with a
stubbed proposer so the sheet surfaces are the ones asserted.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent.conclusion import (
    _select_directions,
    _sheet_evidence_count,
    compose_conclusion,
)
from medagent.direction_guards import is_affirmed_in_text
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
from medagent.reasoning import EvidenceRef, Hypothesis, ReasoningWorkspace


def closed_state(chief="发烧两天", risk=RiskLevel.ROUTINE):
    state = CaseState(session_id="direction-guards-test")
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(risk)
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
    state.transcript.append({"role": "user", "content": chief})
    return state


def add_fact(state, key, value, assertion=Assertion.PRESENT, evidence=None):
    fact = Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t2",
        evidence=(
            evidence
            if evidence is not None
            else (str(value) if value is not None else None)
        ),
    )
    state.record(fact)
    return fact


def hypothesis(hid, refs, status="active", prior="medium", category="common_condition"):
    return Hypothesis(
        id=hid,
        label="测试假设",
        category=category,
        prior_band=prior,
        status=status,
        evidence_for=refs,
    )


def select(state, hyp):
    workspace = ReasoningWorkspace()
    workspace.hypotheses[hyp.id] = hyp
    return _select_directions(workspace, state)


def all_quotes(state, hyp):
    directions, _abstained = select(state, hyp)
    return [quote for d in directions for quote in d["support_quotes"]]


def sheet_with_workspace(state, hyp):
    workspace = ReasoningWorkspace()
    workspace.hypotheses[hyp.id] = hyp

    class StubProposer:
        def propose(self, _state):
            return workspace

    with mock.patch.object(
        conclusion_module.adjustments, "RuleBasedProposer", StubProposer
    ):
        sheet = compose_conclusion(state)
    assert sheet is not None
    return sheet


def rendered_sheet_text(sheet):
    parts = [
        json.dumps(sheet.get("narrative") or {}, ensure_ascii=False),
        json.dumps(sheet.get("self_care") or {}, ensure_ascii=False),
        str(sheet.get("outlook") or ""),
        " ".join(sheet.get("return_now_if") or []),
    ]
    hint = (sheet.get("disposition") or {}).get("department_hint") or {}
    parts.append(str(hint.get("reason") or ""))
    return "\n".join(parts)


class GuardUnitTests(unittest.TestCase):
    def test_perception_negation_is_not_affirmation(self):
        # P-03 / T-06: "也不觉得呼吸困难" denies 困难, it does not affirm it.
        text = "静坐时也不觉得呼吸困难"
        self.assertFalse(is_affirmed_in_text(text, "困难"))
        self.assertFalse(is_affirmed_in_text(text, "呼吸困难"))

    def test_plain_affirmation_still_counts(self):
        self.assertTrue(is_affirmed_in_text("呼吸困难，喘得厉害", "困难"))
        self.assertTrue(is_affirmed_in_text("有点发烧，全身酸痛", "发烧"))

    def test_sentence_tail_uncertainty_covers_long_enumeration(self):
        # N-10 verbatim shape: options recapitulated, closed by 都不清楚.
        text = (
            "头晕是天旋地转还是头重脚轻、每次持续多久、"
            "和转头起身有没有关系、有没有耳鸣听力变化、走路稳不稳都不清楚"
        )
        self.assertFalse(is_affirmed_in_text(text, "头重脚轻"))
        self.assertFalse(is_affirmed_in_text(text, "天旋地转"))

    def test_local_fever_descriptor_is_not_systemic_fever(self):
        # D-13: "脸颊发红发热" is local warmth, not a fever.
        self.assertFalse(is_affirmed_in_text("脸颊发红发热", "发热"))
        self.assertTrue(is_affirmed_in_text("全身发热，觉得冷", "发热"))


class ReplayQuoteTests(unittest.TestCase):
    def test_p03_denial_chain_drops_both_substrings(self):
        # P-03 round-2 wording (issue row): denial sentence, quotes
        # "喘" (round 1) and "困难" (round-2换词链) must both fail.
        state = closed_state(chief="小儿感冒发热")
        breathing = add_fact(
            state,
            "resp_breathing",
            "呼吸和平时差不多，没有呼吸困难，也没有气短、喘，"
            "说话不费力，静坐时也不觉得呼吸困难。",
        )
        lower = hypothesis(
            "lower_respiratory",
            [EvidenceRef("resp_breathing", breathing.fact_id, "困难")],
            category="serious_condition",
        )
        asthma = hypothesis(
            "asthma_exacerbation",
            [EvidenceRef("resp_breathing", breathing.fact_id, "喘")],
            category="serious_condition",
        )
        self.assertEqual(all_quotes(state, lower), [])
        self.assertEqual(all_quotes(state, asthma), [])
        directions, abstained = select(state, lower)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_t06_denial_sentence_drops_asthma_quote(self):
        # T-06 verbatim denial sentence.
        state = closed_state(chief="过敏性鼻炎，打喷嚏流鼻涕")
        breathing = add_fact(
            state,
            "resp_breathing",
            "没有气短、喘，说完整句话不费力，静坐时也不觉得呼吸困难。",
        )
        asthma = hypothesis(
            "asthma_exacerbation",
            [EvidenceRef("resp_breathing", breathing.fact_id, "喘")],
            category="serious_condition",
        )
        lower = hypothesis(
            "lower_respiratory",
            [EvidenceRef("resp_breathing", breathing.fact_id, "困难")],
            category="serious_condition",
        )
        self.assertEqual(all_quotes(state, asthma), [])
        self.assertEqual(all_quotes(state, lower), [])

    def test_o05_encoded_and_truncated_quotes_drop(self):
        # O-05: "worsening" is the trajectory code, "咳" is a
        # truncation of "咳嗽也疼" (cough as an aggravating factor,
        # not a respiratory symptom).
        state = closed_state(chief="腰疼还往腿上窜，走到小腿发麻")
        trajectory = add_fact(state, "trajectory", "worsening", evidence="逐渐加重")
        cough = add_fact(state, "resp_cough_details", "咳嗽也疼")
        pneumonia = hypothesis(
            "lower_respiratory",
            [
                EvidenceRef("trajectory", trajectory.fact_id, "worsening"),
                EvidenceRef("resp_cough_details", cough.fact_id, "咳"),
            ],
            category="serious_condition",
        )
        directions, abstained = select(state, pneumonia)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)
        quotes = all_quotes(state, pneumonia)
        self.assertNotIn("worsening", quotes)
        self.assertNotIn("咳", quotes)

    def test_d13_local_fever_quote_drops_skin_direction(self):
        # D-13 verbatim skin detail + twice-denied fever.
        state = closed_state(chief="脸颊起了密密麻麻的小疹子")
        skin = add_fact(
            state,
            "skin_details",
            "长在脸颊，脸颊发红发热，起了密密麻麻的小疹子，"
            "洗脸时觉得刺痛",
        )
        add_fact(
            state, "associated_symptoms", None, Assertion.ABSENT, "没有发烧"
        )
        skin_infection = hypothesis(
            "skin_infection",
            [EvidenceRef("skin_details", skin.fact_id, "发热")],
            category="serious_condition",
        )
        directions, abstained = select(state, skin_infection)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)
        self.assertNotIn("发热", all_quotes(state, skin_infection))

    def test_n10_unknown_recapitulation_quote_drops(self):
        # N-10 verbatim dizzy answer: "头重脚轻" only occurs inside
        # the "……都不清楚" recapitulation; the affirmed sentences
        # that follow name other symptoms.
        state = closed_state(chief="头晕半小时前突然开始")
        dizzy = add_fact(
            state,
            "dizzy_details",
            "头晕是半小时前突然开始的。头晕是天旋地转还是头重脚轻、"
            "每次持续多久、和转头起身有没有关系、有没有耳鸣听力变化、"
            "走路稳不稳都不清楚。说话不清楚是有的，"
            "一边胳膊没劲也是有的。",
        )
        orthostatic = hypothesis(
            "orthostatic",
            [EvidenceRef("dizzy_details", dizzy.fact_id, "头重脚轻")],
        )
        self.assertEqual(all_quotes(state, orthostatic), [])
        directions, abstained = select(state, orthostatic)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_affirmed_head_heavy_light_is_still_kept(self):
        # Control against over-blocking: a genuinely affirmed
        # "头重脚轻" keeps its direction.
        state = closed_state(chief="头晕")
        dizzy = add_fact(state, "dizzy_details", "站起来头重脚轻，眼前发黑")
        orthostatic = hypothesis(
            "orthostatic",
            [EvidenceRef("dizzy_details", dizzy.fact_id, "头重脚轻")],
        )
        directions, abstained = select(state, orthostatic)
        self.assertFalse(abstained)
        self.assertEqual(directions[0]["support_quotes"], ["头重脚轻"])


class DirectionAnchoringTests(unittest.TestCase):
    def test_j07_trajectory_only_chest_direction_dropped(self):
        # J-07 verbatim: patient never said 胸痛; the sole support
        # is the trajectory sentence.
        state = closed_state(
            chief="我最近总提心吊胆，心慌手心出汗，一点小事就紧张"
        )
        add_fact(
            state,
            "associated_symptoms",
            "心里发慌、手心出汗，坐立不安、静不下来，"
            "晚上睡不踏实、容易惊醒",
        )
        trajectory = add_fact(
            state, "trajectory", "stable", evidence="一直这样，没什么变化。"
        )
        chest = hypothesis(
            "non_cardiac_chest",
            [EvidenceRef("trajectory", trajectory.fact_id, "一直这样，没什么变化。")],
        )
        directions, abstained = select(state, chest)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_j07_sheet_names_the_dropped_direction_nowhere(self):
        state = closed_state(
            chief="我最近总提心吊胆，心慌手心出汗，一点小事就紧张"
        )
        add_fact(
            state,
            "associated_symptoms",
            "心里发慌、手心出汗，坐立不安、静不下来，"
            "晚上睡不踏实、容易惊醒",
        )
        trajectory = add_fact(
            state, "trajectory", "stable", evidence="一直这样，没什么变化。"
        )
        chest = hypothesis(
            "non_cardiac_chest",
            [EvidenceRef("trajectory", trajectory.fact_id, "一直这样，没什么变化。")],
        )
        sheet = sheet_with_workspace(state, chest)
        self.assertEqual(sheet["directions"], [])
        self.assertNotIn("非心脏原因的胸痛", rendered_sheet_text(sheet))
        reasoning = (sheet["narrative"] or {}).get("reasoning") or ""
        self.assertNotIn("非心脏原因的胸痛", reasoning)

    def test_fever_only_cross_system_rule_out_dropped(self):
        # U-11 shape: urinary chief, a respiratory rule-out resting
        # on a single affirmed fever quote and nothing respiratory.
        state = closed_state(chief="小便时尿道疼，尿频")
        add_fact(state, "urinary_details", "小便时尿道疼，尿频尿急")
        fever = add_fact(state, "associated_symptoms", "有点发烧")
        pneumonia = hypothesis(
            "lower_respiratory",
            [EvidenceRef("associated_symptoms", fever.fact_id, "发烧")],
            category="serious_condition",
        )
        directions, _ = select(state, pneumonia)
        self.assertEqual(directions, [])
        # Control (K-12 pin): the same fever quote still keeps a
        # common-condition likely direction — the fever-only bar
        # applies to cross-system rule-outs, not to likely directions.
        flu = hypothesis(
            "influenza_like",
            [EvidenceRef("associated_symptoms", fever.fact_id, "发烧")],
        )
        flu_directions, flu_abstained = select(state, flu)
        self.assertFalse(flu_abstained)
        self.assertEqual(len(flu_directions), 1)

    def test_absent_denied_rule_out_dropped(self):
        # A cannot_exclude direction whose only quote is denied on
        # record by an ABSENT fact (affirmed in the chief, denied in
        # a later associated answer) must not stand.
        state = closed_state(chief="发烧两天")
        add_fact(
            state, "associated_symptoms", None, Assertion.ABSENT, "没有发烧"
        )
        chief_fact = state.facts["chief_complaint"]
        pneumonia = hypothesis(
            "lower_respiratory",
            [EvidenceRef("chief_complaint", chief_fact.fact_id, "发烧")],
            category="serious_condition",
        )
        directions, _ = select(state, pneumonia)
        self.assertEqual(directions, [])

    def test_d13_sheet_names_the_dropped_direction_nowhere(self):
        state = closed_state(chief="脸颊起了密密麻麻的小疹子")
        skin = add_fact(
            state,
            "skin_details",
            "长在脸颊，脸颊发红发热，起了密密麻麻的小疹子，"
            "洗脸时觉得刺痛",
        )
        add_fact(
            state, "associated_symptoms", None, Assertion.ABSENT, "没有发烧"
        )
        skin_infection = hypothesis(
            "skin_infection",
            [EvidenceRef("skin_details", skin.fact_id, "发热")],
            category="serious_condition",
        )
        sheet = sheet_with_workspace(state, skin_infection)
        self.assertEqual(sheet["directions"], [])
        self.assertNotIn("皮肤感染或带状疱疹可能", rendered_sheet_text(sheet))

    def test_sheet_evidence_count_ignores_local_fever_term(self):
        state = closed_state(chief="脸颊起了小疹子")
        skin = add_fact(
            state, "skin_details", "脸颊发红发热，觉得疼痛"
        )
        workspace = ReasoningWorkspace()
        workspace.hypotheses["skin_infection"] = hypothesis(
            "skin_infection",
            [EvidenceRef("skin_details", skin.fact_id, "疼痛")],
            category="serious_condition",
        )
        direction = {"hypothesis_id": "skin_infection", "support_quotes": ["疼痛"]}
        self.assertEqual(
            _sheet_evidence_count(workspace, state, direction), 1
        )


class SeverityConsistencyTests(unittest.TestCase):
    def _urgent_sheet(self, extra):
        state = closed_state(
            chief="腹股沟有个包块，站着更明显", risk=RiskLevel.URGENT
        )
        for key, value, assertion, evidence in extra:
            add_fact(state, key, value, assertion, evidence)
        return compose_conclusion(state)

    def test_s11_recorded_score_3_no_longer_called_high(self):
        sheet = self._urgent_sheet(
            [("severity", 3, Assertion.PRESENT, "3分")]
        )
        assert sheet is not None
        severity = (sheet["narrative"] or {})["severity"]
        self.assertNotIn("程度偏高", severity)
        self.assertIn("3 分", severity)
        # Disposition is untouched by the wording fallback.
        self.assertEqual(sheet["disposition"]["level"], "urgent_today")

    def test_high_score_keeps_high_wording(self):
        sheet = self._urgent_sheet(
            [("severity", 9, Assertion.PRESENT, "9分")]
        )
        assert sheet is not None
        self.assertIn("程度偏高", (sheet["narrative"] or {})["severity"])

    def test_unrecorded_severity_keeps_template(self):
        sheet = self._urgent_sheet([])
        assert sheet is not None
        self.assertIn("程度偏高", (sheet["narrative"] or {})["severity"])


class InfantGateTests(unittest.TestCase):
    def setUp(self):
        self.repo = ContentRepository()
        self.question = self.repo.question("infant_observable_signs")

    def _state(self, extra):
        state = closed_state(
            chief="我昨天吃完夜宵就拉肚子，肚子绞着疼，恶心想吐"
        )
        for key, value, assertion, evidence in extra:
            add_fact(state, key, value, assertion, evidence)
        return state

    def test_g11_adult_self_consultation_not_asked(self):
        # G-11 verbatim: self consultation, age answered unclear
        # (no age fact) — the infant question must not apply.
        state = self._state(
            [("consult_for", "self", Assertion.PRESENT, "我自己咨询")]
        )
        self.assertFalse(
            self.repo.question_applies_in_state(self.question, state)
        )

    def test_e10_adult_self_consultation_not_asked(self):
        # E-10: consult_for=self recorded before the question turn.
        state = self._state(
            [
                ("consult_for", "self", Assertion.PRESENT, "我自己"),
                ("associated_symptoms", "恶心呕吐，肚子疼", Assertion.PRESENT, None),
            ]
        )
        self.assertFalse(
            self.repo.question_applies_in_state(self.question, state)
        )

    def test_real_infant_consultation_still_asked(self):
        # True infant cases are untouched: child consultation with
        # age 1, and child consultation with age still unknown.
        infant = self._state(
            [
                ("consult_for", "child", Assertion.PRESENT, "孩子"),
                ("age_years", 1, Assertion.PRESENT, "1岁"),
            ]
        )
        self.assertTrue(
            self.repo.question_applies_in_state(self.question, infant)
        )
        infant_unknown_age = self._state(
            [("consult_for", "child", Assertion.PRESENT, "孩子")]
        )
        self.assertTrue(
            self.repo.question_applies_in_state(self.question, infant_unknown_age)
        )
        toddler = self._state(
            [("age_years", 2, Assertion.PRESENT, "2岁")]
        )
        self.assertTrue(
            self.repo.question_applies_in_state(self.question, toddler)
        )

    def test_unknown_age_without_child_consultation_not_asked(self):
        state = self._state([])
        self.assertFalse(
            self.repo.question_applies_in_state(self.question, state)
        )

    def test_older_child_not_asked(self):
        state = self._state(
            [
                ("consult_for", "child", Assertion.PRESENT, "孩子"),
                ("age_years", 5, Assertion.PRESENT, "5岁"),
            ]
        )
        self.assertFalse(
            self.repo.question_applies_in_state(self.question, state)
        )


if __name__ == "__main__":
    unittest.main()
