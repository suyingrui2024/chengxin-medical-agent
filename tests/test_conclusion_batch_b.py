"""Disease batch B conclusion tests (WORRY / EVIDENCE / UNKNOWN / GOAL).

Covers the conclusion composer's batch-B contract:

- worry slot: three-source priority, goal blanking only when the goal
  sentence itself is the worry source, and no direction label or
  department name ever leaks into the worry / goal responses;
- goal_response: non-visit goals only, 60-char truncation, banned
  degradation;
- gaps: declined vs asked_unknown states, grouped rendering, and the
  label fallback chain (no raw fact key may surface);
- citation gate: denial / unknown / truncated single character /
  numeric / encoded quotes are all filtered, directions with no valid
  support abstain, and certainty is recomputed from valid evidence;
- sheet_group: a single-evidence cross-system direction (K-12 shape)
  no longer drives outlook, self-care, narrative group, or the safety
  net; a direction with >= 2 valid quotes does.
"""

from __future__ import annotations

import unittest
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent.conclusion import (
    RETURN_NOW_IF,
    _build_goal_response,
    _build_worry,
    _gaps,
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
from medagent.handoff import REGISTERED_FACT_KEYS
from medagent.reasoning import EvidenceRef, Hypothesis, ReasoningWorkspace


def closed_state(chief="发烧两天"):
    state = CaseState(session_id="batch-b-test")
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
    return conclusion_module._select_directions(workspace, state)


class WorryTests(unittest.TestCase):
    def test_patient_worry_beats_goal_worry_and_goal_survives(self):
        state = closed_state()
        add_fact(state, "patient_worry", "食物中毒")
        add_fact(state, "patient_goal", "我这是不是肺炎？")
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["worry"]["object"], "食物中毒")
        # The worry came from patient_worry, not from the goal slot, so
        # the goal line is not blanked (blanking applies only when the
        # goal sentence itself is the worry source).
        self.assertEqual(sheet["goal_line"], "我这是不是肺炎？")

    def test_goal_worry_moves_to_worry_and_blanks_goal(self):
        state = closed_state()
        add_fact(state, "patient_goal", "我这是不是食物中毒？")
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["worry"]["object"], "食物中毒")
        self.assertIsNone(sheet["goal_line"])
        self.assertIsNone(sheet["goal_response"])

    def test_plain_goal_has_no_worry(self):
        state = closed_state()
        add_fact(state, "patient_goal", "平时怎么护眼")
        sheet = compose_conclusion(state)
        self.assertIsNone(sheet["worry"])
        self.assertEqual(sheet["goal_line"], "平时怎么护眼")

    def test_worry_response_uses_only_collected_facts(self):
        state = closed_state()
        add_fact(state, "patient_worry", "食物中毒")
        add_fact(state, "severity", 7)
        sheet = compose_conclusion(state)
        response = sheet["worry"]["response"]
        self.assertIn("你问过是不是‘食物中毒’", response)
        self.assertIn("已确认的是发烧两天", response)
        self.assertIn("影响程度为 7 分", response)
        self.assertIn("建议按结论单的处置去做", response)

    def test_worry_response_without_severity_omits_score(self):
        state = closed_state()
        add_fact(state, "patient_worry", "食物中毒")
        sheet = compose_conclusion(state)
        self.assertNotIn("影响程度", sheet["worry"]["response"])

    def test_worry_and_goal_responses_leak_no_direction_or_department(self):
        # Directions are present but shadow (directions_visible
        # defaults off); the department hint, if any, is shadow too.
        # Neither may appear in the worry / goal responses.
        state = closed_state()
        add_fact(state, "patient_worry", "食物中毒")
        add_fact(state, "patient_goal", "平时怎么护眼")
        sheet = compose_conclusion(state)
        self.assertFalse(sheet["directions_visible"])
        self.assertTrue(sheet["directions"])
        labels = [d["patient_label"] for d in sheet["directions"]]
        dept = (sheet["disposition"].get("department_hint") or {}).get(
            "department"
        )
        for text in (
            sheet["worry"]["response"],
            sheet["goal_response"],
        ):
            for label in labels:
                self.assertNotIn(label, text)
            if dept:
                self.assertNotIn(dept, text)

    def test_worry_banned_object_degrades(self):
        state = closed_state(chief="肚子疼两天")
        worry, _consumed = _build_worry(state, [], None)
        self.assertIsNone(worry)
        add_fact(state, "patient_worry", "肯定是肺炎")
        worry, _consumed = _build_worry(state, [], None)
        self.assertEqual(worry["object"], "你问过的那个担心")
        self.assertNotIn("肯定是", worry["response"])
        self.assertNotIn("肺炎", worry["response"])
        self.assertIn("目前还不能仅凭这次问诊确定这一点", worry["response"])


class GoalResponseTests(unittest.TestCase):
    def test_plain_goal_gets_response(self):
        self.assertIn(
            "平时怎么护眼",
            _build_goal_response("平时怎么护眼"),
        )
        self.assertIn("不足以直接回答", _build_goal_response("平时怎么护眼"))

    def test_visit_keyword_goals_get_none(self):
        for goal in (
            "想知道要不要去检查",
            "想知道要不要去医院",
            "想知道要不要去就诊",
            "想知道要不要去看医生",
        ):
            self.assertIsNone(_build_goal_response(goal), goal)

    def test_long_goal_is_truncated(self):
        goal = "平时应该怎么注意饮食起居和运动锻炼才能让身体恢复得更快更好呢" + "啊" * 40
        self.assertGreater(len(goal), 60)
        response = _build_goal_response(goal)
        self.assertNotIn("啊" * 40, response)
        self.assertIn(goal[:60], response)

    def test_banned_goal_degrades_without_quoting(self):
        response = _build_goal_response("想知道是不是肯定是肺炎")
        self.assertNotIn("肯定是", response)
        self.assertIn("你问到的这一点", response)


class GapsTests(unittest.TestCase):
    def test_three_states_render_grouped(self):
        state = closed_state(chief="膝盖疼一周")
        summary = {
            "unknown_details": [
                {"key": "temperature_c", "label": "体温"},
                {"key": "sex", "label": "sex"},
                {"key": "age_years", "label": "年龄"},
            ],
            "declined_details": [{"key": "age_years", "label": "年龄"}],
        }
        sheet = compose_conclusion(state, summary)
        by_key = {gap["fact_key"]: gap for gap in sheet["gaps"]}
        self.assertEqual(by_key["temperature_c"]["state"], "asked_unknown")
        # Raw-key label falls back to the handoff label table.
        self.assertEqual(by_key["sex"]["label"], "性别")
        self.assertEqual(by_key["sex"]["state"], "asked_unknown")
        self.assertEqual(by_key["age_years"]["state"], "declined")
        reasoning = sheet["narrative"]["reasoning"]
        self.assertIn("你提到体温、性别还不清楚", reasoning)
        self.assertIn("年龄你表示暂不提供", reasoning)
        self.assertNotIn("还没有确认", reasoning)

    def test_refusal_evidence_without_declined_list_is_declined(self):
        state = closed_state(chief="膝盖疼一周")
        add_fact(
            state,
            "allergies",
            None,
            Assertion.UNKNOWN,
            "我不想说这个",
        )
        summary = {
            "unknown_details": [{"key": "allergies", "label": "过敏信息"}],
        }
        gaps = _gaps(state, summary, ReasoningWorkspace())
        self.assertEqual(gaps[0]["state"], "declined")

    def test_all_registered_keys_resolve_to_non_key_labels(self):
        state = closed_state(chief="膝盖疼一周")
        for key in sorted(REGISTERED_FACT_KEYS):
            gaps = _gaps(
                state,
                {"unknown_details": [{"key": key, "label": None}]},
                ReasoningWorkspace(),
            )
            self.assertEqual(len(gaps), 1, key)
            self.assertNotEqual(gaps[0]["label"], key, key)
            self.assertTrue(gaps[0]["label"], key)


class CitationGateTests(unittest.TestCase):
    def test_denied_quote_drops_direction(self):
        state = closed_state(chief="肚子疼两天")
        denied = add_fact(
            state, "associated_symptoms", None, Assertion.ABSENT, "没有呕吐"
        )
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("associated_symptoms", denied.fact_id, "呕吐")],
        )
        directions, abstained = select(state, hyp)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_unknown_quote_drops_direction(self):
        state = closed_state(chief="肚子疼两天")
        unknown = add_fact(
            state, "associated_symptoms", None, Assertion.UNKNOWN, "呕吐不清楚"
        )
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("associated_symptoms", unknown.fact_id, "呕吐")],
        )
        directions, abstained = select(state, hyp)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_truncated_single_character_quote_drops_direction(self):
        state = closed_state(chief="肚子疼两天")
        cough = add_fact(state, "resp_cough_details", "不敢咳嗽")
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("resp_cough_details", cough.fact_id, "咳")],
        )
        directions, abstained = select(state, hyp)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_numeric_quote_drops_direction(self):
        state = closed_state(chief="肚子疼两天")
        severity = add_fact(state, "severity", 9, evidence="影响程度9分")
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("severity", severity.fact_id, "9")],
        )
        directions, abstained = select(state, hyp)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_encoded_value_quote_drops_direction(self):
        state = closed_state(chief="肚子疼两天")
        trajectory = add_fact(state, "trajectory", "worsening", evidence="在加重")
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("trajectory", trajectory.fact_id, "worsening")],
        )
        directions, abstained = select(state, hyp)
        self.assertEqual(directions, [])
        self.assertTrue(abstained)

    def test_chief_complaint_single_character_quote_is_kept(self):
        state = closed_state(chief="咳两天")
        chief_fact = state.facts["chief_complaint"]
        hyp = hypothesis(
            "influenza_like",
            [EvidenceRef("chief_complaint", chief_fact.fact_id, "咳")],
        )
        directions, abstained = select(state, hyp)
        self.assertFalse(abstained)
        self.assertEqual(directions[0]["support_quotes"], ["咳"])

    def test_certainty_recomputed_from_valid_quotes(self):
        state = closed_state()
        chief_fact = state.facts["chief_complaint"]
        denied = add_fact(
            state, "associated_symptoms", None, Assertion.ABSENT, "没有呕吐"
        )
        hyp = hypothesis(
            "influenza_like",
            [
                EvidenceRef("chief_complaint", chief_fact.fact_id, "发烧"),
                EvidenceRef("associated_symptoms", denied.fact_id, "呕吐"),
            ],
            status="supported",
            prior="high",
        )
        directions, abstained = select(state, hyp)
        self.assertFalse(abstained)
        # Two raw refs but only one valid: "possible", not "more_likely".
        self.assertEqual(directions[0]["certainty"], "possible")
        self.assertEqual(directions[0]["support_quotes"], ["发烧"])

        state2 = closed_state()
        chief_fact2 = state2.facts["chief_complaint"]
        aches = add_fact(state2, "associated_symptoms", "全身酸痛")
        hyp2 = hypothesis(
            "influenza_like",
            [
                EvidenceRef("chief_complaint", chief_fact2.fact_id, "发烧"),
                EvidenceRef("associated_symptoms", aches.fact_id, "酸痛"),
            ],
            status="supported",
            prior="high",
        )
        directions2, _ = select(state2, hyp2)
        self.assertEqual(directions2[0]["certainty"], "more_likely")
        self.assertEqual(directions2[0]["support_quotes"], ["发烧", "酸痛"])

    def test_against_note_untouched_by_citation_gate(self):
        state = closed_state(chief="肚子疼两天")
        chief_fact = state.facts["chief_complaint"]
        denied = add_fact(state, "gi_details", None, Assertion.ABSENT, "没有便血")
        hyp = hypothesis(
            "appendicitis",
            [EvidenceRef("chief_complaint", chief_fact.fact_id, "肚子疼")],
            category="serious_condition",
        )
        hyp.evidence_against = [
            EvidenceRef("gi_details", denied.fact_id, "没有便血")
        ]
        directions, abstained = select(state, hyp)
        self.assertFalse(abstained)
        self.assertEqual(directions[0]["support_quotes"], ["肚子疼"])
        self.assertEqual(
            directions[0]["against_note"], "你否认了腹部细节，这一点不支持它"
        )


class SheetGroupTests(unittest.TestCase):
    def _patched_sheet(self, workspace):
        class StubProposer:
            def propose(self, _state):
                return workspace

        with mock.patch.object(
            conclusion_module, "RuleBasedProposer", StubProposer
        ):
            return compose_conclusion(self.state)

    def test_single_evidence_respiratory_direction_drives_nothing(self):
        # K-12 shape: a urinary chief complaint with a single-evidence
        # respiratory direction. The respiratory group must not drive
        # outlook, self-care, narrative, or the safety net.
        self.state = closed_state(chief="小便时尿道疼，尿频")
        add_fact(self.state, "urinary_details", "小便时尿道疼，尿频尿急")
        fever = add_fact(self.state, "associated_symptoms", "有点发烧")
        workspace = ReasoningWorkspace()
        workspace.hypotheses["influenza_like"] = hypothesis(
            "influenza_like",
            [EvidenceRef("associated_symptoms", fever.fact_id, "发烧")],
        )
        sheet = self._patched_sheet(workspace)
        self.assertEqual(len(sheet["directions"]), 1)
        self.assertEqual(sheet["self_care"]["do"], ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"])
        self.assertIn("数天内逐渐缓解", sheet["outlook"])
        self.assertIn("身体", sheet["narrative"]["framing"])
        self.assertNotIn("呼吸道", sheet["narrative"]["framing"])
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_two_valid_quotes_drive_their_group(self):
        self.state = closed_state(chief="小便时尿道疼，尿频")
        add_fact(self.state, "urinary_details", "小便时尿道疼，尿频尿急")
        symptoms = add_fact(
            self.state, "associated_symptoms", "有点发烧，全身酸痛"
        )
        workspace = ReasoningWorkspace()
        workspace.hypotheses["influenza_like"] = hypothesis(
            "influenza_like",
            [
                EvidenceRef("associated_symptoms", symptoms.fact_id, "发烧"),
                EvidenceRef("associated_symptoms", symptoms.fact_id, "酸痛"),
            ],
        )
        sheet = self._patched_sheet(workspace)
        self.assertIn("多喝水", sheet["self_care"]["do"][0])
        self.assertIn("3–5 天", sheet["outlook"])
        self.assertIn("呼吸道", sheet["narrative"]["framing"])
        self.assertEqual(
            sheet["return_now_if"], ["呼吸困难加重", "嘴唇发紫", "高热不退"]
        )


if __name__ == "__main__":
    unittest.main()
