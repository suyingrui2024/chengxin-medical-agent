"""Disease batch-B rework tests (evidence core).

Pins the three rework fixes without touching gate expectations,
differentials.json, or direction/department visibility defaults:

(a) encoding-value conditions quote patient wording: an equals/gte/lte
    condition whose fact carries genuine patient phrasing quotes that
    phrasing (trajectory ``stable`` recorded as "变化不大"), while a
    pure number, a digit-plus-unit score ("3分"), an encoded value, a
    negated phrasing, or an uncertain phrasing still falls back to
    ``str(value)`` and stays subject to the composer's citation gate;
(b) sheet_group counts distinct valid evidence tokens (option (b)-②):
    one referenced fact naming several of a hypothesis's DSL symptoms
    drives its group, a single token never does, and certainty still
    recomputes from the valid-quote count alone;
(c) R2: the composer uses text_match.find_unnegated_certain directly —
    no local fallback — so the denial-list scope and adjacent
    negations follow the shared implementation.
"""

from __future__ import annotations

import unittest
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent import text_match as text_match_module
from medagent.conclusion import (
    RETURN_NOW_IF,
    _sheet_evidence_count,
    _valid_support_quote,
    compose_conclusion,
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
from medagent.reasoning import (
    EvidenceRef,
    Hypothesis,
    ReasoningWorkspace,
    RuleBasedProposer,
)


def closed_state(chief):
    state = CaseState(session_id="batch-b-rework-test")
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
    state.transcript.append({"role": "user", "content": fact.evidence or ""})
    return fact


def propose_one(state, fact_key, condition, chief_terms=("胸口",)):
    """Propose a single synthetic hypothesis carrying one condition."""
    pathways = [
        {
            "chief_complaint_contains_any": list(chief_terms),
            "hypotheses": [
                {
                    "id": "synthetic",
                    "label": "合成假设",
                    "category": "common_condition",
                    "prior_band": "medium",
                    "support": [dict(condition, fact_key=fact_key)],
                    "refute": [],
                }
            ],
        }
    ]
    workspace = RuleBasedProposer(pathways=pathways).propose(state)
    hypothesis = workspace.hypotheses.get("synthetic")
    if hypothesis is None:
        return []
    return hypothesis.evidence_for


class EncodingWordingTests(unittest.TestCase):
    def test_trajectory_stable_quotes_patient_wording(self):
        state = closed_state("胸口闷")
        add_fact(state, "trajectory", "stable", evidence="变化不大")
        refs = propose_one(state, "trajectory", {"equals": "stable"})
        self.assertEqual([ref.quote for ref in refs], ["变化不大"])
        self.assertTrue(_valid_support_quote(state, refs[0]))

    def test_trajectory_improving_quotes_patient_wording(self):
        state = closed_state("胸闷")
        add_fact(state, "trajectory", "improving", evidence="在减轻")
        refs = propose_one(
            state, "trajectory", {"equals": "improving"}, chief_terms=("胸闷",)
        )
        self.assertEqual([ref.quote for ref in refs], ["在减轻"])
        self.assertTrue(_valid_support_quote(state, refs[0]))

    def test_severity_number_stays_numeric_and_invalid(self):
        state = closed_state("突然剧烈头痛")
        add_fact(state, "severity", 10, evidence="10")
        refs = propose_one(
            state, "severity", {"gte": 9}, chief_terms=("头痛",)
        )
        self.assertEqual([ref.quote for ref in refs], ["10"])
        self.assertFalse(_valid_support_quote(state, refs[0]))

    def test_temperature_number_stays_numeric_and_invalid(self):
        state = closed_state("咳嗽气喘两天")
        add_fact(state, "temperature_c", 38.5, evidence="38.5")
        refs = propose_one(
            state, "temperature_c", {"gte": 38.5}, chief_terms=("咳",)
        )
        self.assertEqual([ref.quote for ref in refs], ["38.5"])
        self.assertFalse(_valid_support_quote(state, refs[0]))

    def test_digit_plus_unit_score_is_not_wording(self):
        state = closed_state("胸口闷")
        add_fact(state, "severity", 3, evidence="3分")
        refs = propose_one(state, "severity", {"lte": 6})
        # "3分" has only one CJK character, so it is not genuine
        # wording: the quote falls back to the numeric value.
        self.assertEqual([ref.quote for ref in refs], ["3"])
        self.assertFalse(_valid_support_quote(state, refs[0]))

    def test_negated_and_uncertain_wording_fall_back(self):
        state = closed_state("胸口闷")
        add_fact(state, "trajectory", "stable", evidence="没有变化")
        refs = propose_one(state, "trajectory", {"equals": "stable"})
        self.assertEqual([ref.quote for ref in refs], ["stable"])
        self.assertFalse(_valid_support_quote(state, refs[0]))

        state2 = closed_state("胸口闷")
        add_fact(state2, "trajectory", "stable", evidence="变化不大不清楚")
        refs2 = propose_one(state2, "trajectory", {"equals": "stable"})
        self.assertEqual([ref.quote for ref in refs2], ["stable"])
        self.assertFalse(_valid_support_quote(state2, refs2[0]))

    def test_chinese_numeral_temperature_is_wording(self):
        state = closed_state("咳嗽气喘两天")
        add_fact(state, "temperature_c", 38.5, evidence="三十八度五")
        refs = propose_one(
            state, "temperature_c", {"gte": 38.5}, chief_terms=("咳",)
        )
        self.assertEqual([ref.quote for ref in refs], ["三十八度五"])
        self.assertTrue(_valid_support_quote(state, refs[0]))

    def test_chest_noncardiac_end_to_end(self):
        # Probe case chest-noncardiac-stable through the real proposer.
        state = closed_state("胸口闷")
        add_fact(state, "trajectory", "stable", evidence="变化不大")
        add_fact(state, "severity", 3, evidence="3分")
        add_fact(
            state,
            "associated_symptoms",
            None,
            Assertion.ABSENT,
            "没有其他不舒服",
        )
        add_fact(
            state, "relevant_history", None, Assertion.ABSENT, "没有病史"
        )
        sheet = compose_conclusion(state)
        self.assertFalse(sheet["directions_abstained"])
        self.assertIn(
            "non_cardiac_chest",
            [d["hypothesis_id"] for d in sheet["directions"]],
        )


class SheetGroupTokenTests(unittest.TestCase):
    def _patched_sheet(self, state, workspace):
        class StubProposer:
            def propose(self, _state):
                return workspace

        with mock.patch.object(
            conclusion_module, "RuleBasedProposer", StubProposer
        ):
            return compose_conclusion(state)

    def test_single_ref_multi_term_drives_its_group(self):
        # Radiculopathy shape: one ref, but the referenced fact names
        # two DSL terms (放射到小腿 + 腿麻) -> back safety net.
        state = closed_state("腰痛，腿也痛")
        back = add_fact(state, "back_details", "疼痛放射到小腿，腿麻")
        workspace = ReasoningWorkspace()
        workspace.hypotheses["radiculopathy"] = Hypothesis(
            id="radiculopathy",
            label="腰椎间盘问题压迫神经可能",
            category="common_condition",
            prior_band="medium",
            status="active",
            evidence_for=[
                EvidenceRef("back_details", back.fact_id, "腿麻")
            ],
        )
        sheet = self._patched_sheet(state, workspace)
        self.assertEqual(
            sheet["return_now_if"],
            ["大小便异常", "会阴麻木新出现或加重", "下肢无力加重"],
        )

    def test_cystitis_multi_term_drives_urinary_group(self):
        state = closed_state("尿频尿急")
        urinary = add_fact(
            state, "urinary_details", "尿频尿急，尿痛，有烧灼感"
        )
        workspace = ReasoningWorkspace()
        workspace.hypotheses["cystitis"] = Hypothesis(
            id="cystitis",
            label="膀胱炎一类下尿路感染",
            category="common_condition",
            prior_band="high",
            status="active",
            evidence_for=[
                EvidenceRef("urinary_details", urinary.fact_id, "尿痛")
            ],
        )
        sheet = self._patched_sheet(state, workspace)
        self.assertEqual(sheet["return_now_if"], ["腰痛伴高热", "完全尿不出"])

    def test_single_token_still_generic(self):
        state = closed_state("小便时尿道疼，尿频")
        add_fact(state, "urinary_details", "小便时尿道疼，尿频尿急")
        fever = add_fact(state, "associated_symptoms", "有点发烧")
        workspace = ReasoningWorkspace()
        workspace.hypotheses["influenza_like"] = Hypothesis(
            id="influenza_like",
            label="流感样疾病",
            category="common_condition",
            prior_band="medium",
            status="active",
            evidence_for=[
                EvidenceRef("associated_symptoms", fever.fact_id, "发烧")
            ],
        )
        sheet = self._patched_sheet(state, workspace)
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_certainty_ignores_token_count(self):
        # Supported + high + one valid quote + a second DSL term in
        # the same fact: sheet_group drives, but certainty stays
        # "possible" (recomputed from the one valid quote, not the
        # token count).
        state = closed_state("尿频尿急")
        urinary = add_fact(
            state, "urinary_details", "尿频尿急，尿痛，有烧灼感"
        )
        workspace = ReasoningWorkspace()
        workspace.hypotheses["cystitis"] = Hypothesis(
            id="cystitis",
            label="膀胱炎一类下尿路感染",
            category="common_condition",
            prior_band="high",
            status="supported",
            evidence_for=[
                EvidenceRef("urinary_details", urinary.fact_id, "尿痛")
            ],
        )
        directions, _ = conclusion_module._select_directions(
            workspace, state
        )
        self.assertEqual(directions[0]["certainty"], "possible")
        self.assertEqual(
            _sheet_evidence_count(workspace, state, directions[0]), 3
        )
        sheet = self._patched_sheet(state, workspace)
        self.assertEqual(sheet["return_now_if"], ["腰痛伴高热", "完全尿不出"])

    def test_radiculopathy_and_cystitis_end_to_end(self):
        state = closed_state("腰痛，腿也痛")
        add_fact(state, "trajectory", "stable", evidence="stable")
        add_fact(state, "severity", 4, evidence="4")
        add_fact(state, "back_details", "疼痛放射到小腿，腿麻")
        add_fact(state, "associated_symptoms", "腿麻到脚背")
        sheet = compose_conclusion(state)
        self.assertIn(
            "radiculopathy",
            [d["hypothesis_id"] for d in sheet["directions"]],
        )
        self.assertEqual(
            sheet["return_now_if"],
            ["大小便异常", "会阴麻木新出现或加重", "下肢无力加重"],
        )

        state2 = closed_state("尿频尿急")
        add_fact(state2, "trajectory", "stable", evidence="stable")
        add_fact(state2, "severity", 4, evidence="4")
        add_fact(state2, "associated_symptoms", "小便烧灼感")
        add_fact(
            state2, "urinary_details", "尿频尿急，尿痛，有烧灼感"
        )
        sheet2 = compose_conclusion(state2)
        self.assertIn(
            "cystitis",
            [d["hypothesis_id"] for d in sheet2["directions"]],
        )
        self.assertEqual(sheet2["return_now_if"], ["腰痛伴高热", "完全尿不出"])


class SharedImplementationTests(unittest.TestCase):
    def test_composer_uses_shared_find_unnegated_certain(self):
        self.assertIs(
            conclusion_module.find_unnegated_certain,
            text_match_module.find_unnegated_certain,
        )
        self.assertFalse(
            hasattr(conclusion_module, "_quote_affirmed_certain_local")
        )

    def test_denial_list_later_item_is_not_affirmed(self):
        # Shared semantics: "、" does not break a denial list, so the
        # later item is denied. The deleted local fallback treated
        # "、" as a clause break and read it as affirmed. (A
        # multi-character later item is used so the single-character
        # quote rule cannot short-circuit the test.)
        state = closed_state("咳两天")
        fact = add_fact(state, "resp_breathing", "没有气短、呼吸困难")
        ref = EvidenceRef("resp_breathing", fact.fact_id, "呼吸困难")
        self.assertFalse(_valid_support_quote(state, ref))

    def test_adjacent_bushi_denies(self):
        state = closed_state("头晕两天")
        fact = add_fact(state, "dizzy_details", "不是天旋地转")
        ref = EvidenceRef("dizzy_details", fact.fact_id, "天旋地转")
        self.assertFalse(_valid_support_quote(state, ref))

    def test_affirmed_term_still_valid(self):
        state = closed_state("咳两天")
        fact = add_fact(state, "resp_cough_details", "有点气短")
        ref = EvidenceRef("resp_cough_details", fact.fact_id, "气短")
        self.assertTrue(_valid_support_quote(state, ref))


if __name__ == "__main__":
    unittest.main()
