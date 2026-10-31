"""P1 conclusion upgrades (docs/阿福对标改造设计文档.md §3.1/§3.4/§3.5):
the narrative triple, the department hint chain, the generalized
against_note, and the spoken framing opener.

Department data is injected through the composer's content cache (the
same seam _load_json uses in production) so these tests do not depend
on whether content/departments.json has landed yet.
"""
from __future__ import annotations

import json
import os
import unittest
from copy import deepcopy
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent.conclusion import compose_conclusion, spoken_close_line
from medagent.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.reasoning import EvidenceRef, Hypothesis, ReasoningWorkspace

DEPT_DATA = {
    "version": "1.0",
    "reviewed_by": "engineering-v1",
    "urgent": {"department": "急诊科", "reason": "紧急程度偏高，先到急诊评估"},
    "by_group": {
        "respiratory": {"department": "呼吸内科", "reason": "呼吸道问题先看呼吸内科"},
        "generic": {"department": "全科医疗科", "reason": "先由全科评估再分诊"},
    },
    "by_hypothesis": {
        "influenza_like": {
            "department": "发热门诊",
            "reason": "需要确认{label}的可能性",
        }
    },
}


def closed_state(chief="发烧两天", risk=RiskLevel.ROUTINE, reason="confirmed_by_user"):
    state = CaseState(session_id="unit-test")
    state.phase = Phase.CLOSED
    state.closed_reason = reason
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
        evidence=evidence if evidence is not None else value,
    )
    state.record(fact)
    return fact


class NarrativeTests(unittest.TestCase):
    def test_self_care_sheet_has_full_narrative(self):
        state = closed_state(chief="身上起疹子很痒")
        add_fact(state, "skin_details", "一片片风团，很痒")
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["disposition"]["level"], "self_care")
        narrative = sheet["narrative"]
        self.assertIsNotNone(narrative)
        self.assertIn("皮肤", narrative["framing"])
        self.assertIn("程度较轻", narrative["severity"])
        self.assertIsNotNone(narrative["reasoning"])
        label = sheet["directions"][0]["patient_label"]
        self.assertIn(label, narrative["reasoning"])
        self.assertIn("更像是", narrative["reasoning"])

    def test_routine_visit_narrative(self):
        # Batch B sheet_group gate: the narrative group now needs a
        # direction with >= 2 valid support quotes, so the fixture
        # carries two affirmed clues (a pressing chest pain in the
        # chief complaint, cold sweats as an associated symptom).
        state = closed_state(chief="胸痛半小时，有压榨感")
        add_fact(state, "associated_symptoms", "出冷汗")
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["disposition"]["level"], "routine_visit")
        narrative = sheet["narrative"]
        self.assertIn("胸部", narrative["framing"])
        self.assertIn("不是最紧急", narrative["severity"])
        label = sheet["directions"][0]["patient_label"]
        self.assertIn(label, narrative["reasoning"])

    def test_urgent_today_narrative_uses_urgent_variant(self):
        sheet = compose_conclusion(closed_state(risk=RiskLevel.URGENT))
        self.assertEqual(sheet["disposition"]["level"], "urgent_today")
        narrative = sheet["narrative"]
        self.assertEqual(
            narrative["framing"],
            "先给你一个整体判断：这次不适需要按紧急情况处理，请尽快完成当面评估。",
        )
        self.assertIn("程度偏高", narrative["severity"])

    def test_abstained_sheet_reasoning_is_none_but_framing_remains(self):
        sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
        self.assertTrue(sheet["directions_abstained"])
        narrative = sheet["narrative"]
        self.assertIsNotNone(narrative)
        self.assertTrue(narrative["framing"])
        self.assertTrue(narrative["severity"])
        self.assertIn("身体", narrative["framing"])
        self.assertIsNone(narrative["reasoning"])

    def test_abstained_with_gaps_reasoning_comes_from_gaps(self):
        # Batch B: the old template rendered every unknown gap as
        # "还没有确认", conflating asked-and-unclear with never-asked.
        # That wording is removed; an asked-unknown gap now renders as
        # "你提到…还不清楚".
        summary = {"unknown_details": [{"key": "temperature_c", "label": "体温"}]}
        sheet = compose_conclusion(closed_state(chief="膝盖疼一周"), summary)
        reasoning = sheet["narrative"]["reasoning"]
        self.assertIsNotNone(reasoning)
        self.assertIn("体温", reasoning)
        self.assertIn("你提到体温还不清楚", reasoning)
        self.assertNotIn("还没有确认", reasoning)

    def test_narrative_never_embeds_patient_quotes(self):
        sheet = compose_conclusion(closed_state())
        rendered = json.dumps(sheet["narrative"], ensure_ascii=False)
        self.assertNotIn("发烧两天", rendered)
        self.assertNotIn("发烧", rendered)

    def test_missing_narrative_asset_fails_open(self):
        original = conclusion_module.sheet._load_json

        def flaky(name):
            if name == "conclusion_narrative.json":
                raise FileNotFoundError(name)
            return original(name)

        with mock.patch.object(conclusion_module.sheet, "_load_json", flaky):
            sheet = compose_conclusion(closed_state())
        self.assertIsNotNone(sheet)
        self.assertIsNone(sheet["narrative"])

    def test_narrative_templates_pass_banned_lint(self):
        templates = conclusion_module._narrative_templates()
        self.assertEqual(templates.get("version"), "1.0")
        self.assertEqual(templates.get("reviewed_by"), "engineering-v1")
        texts = []
        for section in ("framing", "severity", "spoken_framing"):
            texts.extend((templates.get(section) or {}).values())
        self.assertTrue(texts)
        for text in texts:
            rendered = text.replace("{group}", "呼吸道")
            for phrase in conclusion_module.BANNED_PHRASES:
                self.assertNotIn(phrase, rendered)


class DepartmentHintTests(unittest.TestCase):
    def _inject(self, data=DEPT_DATA):
        conclusion_module._cache["departments.json"] = deepcopy(data)
        self.addCleanup(conclusion_module._cache.pop, "departments.json", None)

    def test_urgent_entry_wins_over_hypothesis(self):
        self._inject()
        sheet = compose_conclusion(closed_state(risk=RiskLevel.URGENT))
        hint = sheet["disposition"]["department_hint"]
        self.assertEqual(hint["department"], "急诊科")

    def test_hypothesis_entry_beats_group_and_fills_label(self):
        self._inject()
        sheet = compose_conclusion(closed_state())
        hint = sheet["disposition"]["department_hint"]
        self.assertEqual(hint["department"], "发热门诊")
        self.assertEqual(hint["reason"], "需要确认流感样疾病的可能性")
        self.assertEqual(sheet["content_versions"]["departments"], "1.0")

    def test_group_entry_when_no_hypothesis_entry(self):
        data = deepcopy(DEPT_DATA)
        data["by_hypothesis"] = {}
        self._inject(data)
        sheet = compose_conclusion(closed_state())
        hint = sheet["disposition"]["department_hint"]
        self.assertEqual(hint["department"], "呼吸内科")

    def test_generic_group_fallback(self):
        self._inject()
        sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
        hint = sheet["disposition"]["department_hint"]
        self.assertEqual(hint["department"], "全科医疗科")

    def test_missing_departments_file_gives_none(self):
        conclusion_module._cache.pop("departments.json", None)
        original = conclusion_module.sheet._load_json

        def flaky(name):
            if name == "departments.json":
                raise FileNotFoundError(name)
            return original(name)

        with mock.patch.object(conclusion_module.sheet, "_load_json", flaky):
            sheet = compose_conclusion(closed_state())
        self.assertIsNone(sheet["disposition"]["department_hint"])
        self.assertIsNone(sheet["content_versions"]["departments"])

    def test_department_visible_defaults_restricted_and_env_on(self):
        # v1.3 (§13.3): the department variable defaults to unset, which
        # follows the directions mode — and the directions mode defaults
        # to restricted, so the department hint shows by default.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MEDAGENT_CONCLUSION_DEPARTMENT", None)
            os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
            self.assertEqual(conclusion_module.directions_mode(), "restricted")
            self.assertTrue(conclusion_module.directions_restricted_visible())
            self.assertTrue(conclusion_module.department_visible())
            sheet = compose_conclusion(closed_state())
            self.assertTrue(sheet["department_visible"])
        with mock.patch.dict(
            os.environ, {"MEDAGENT_CONCLUSION_DEPARTMENT": "on"}
        ):
            self.assertTrue(conclusion_module.department_visible())
            sheet = compose_conclusion(closed_state())
            self.assertTrue(sheet["department_visible"])


class AgainstNoteTests(unittest.TestCase):
    def _state_with_denied(self, key, evidence):
        state = closed_state(chief="肚子疼两天")
        fact = add_fact(state, key, None, Assertion.ABSENT, evidence)
        return state, fact

    def _hypothesis(self, hid, against_ref=None, chief_fact=None):
        evidence_for = []
        if chief_fact is not None:
            evidence_for.append(
                EvidenceRef("chief_complaint", chief_fact.fact_id, "肚子疼")
            )
        return Hypothesis(
            id=hid,
            label="测试假设",
            category="serious_condition",
            prior_band="high",
            status="active",
            evidence_for=evidence_for,
            evidence_against=[against_ref] if against_ref else [],
        )

    def test_generalized_note_for_denied_non_respiratory_fact(self):
        state, denied = self._state_with_denied("gi_details", "没有便血")
        chief_fact = state.facts["chief_complaint"]
        hypothesis = self._hypothesis(
            "appendicitis",
            EvidenceRef("gi_details", denied.fact_id, "没有便血"),
            chief_fact,
        )
        workspace = ReasoningWorkspace()
        workspace.hypotheses[hypothesis.id] = hypothesis
        directions, abstained = conclusion_module._select_directions(
            workspace, state
        )
        self.assertFalse(abstained)
        self.assertEqual(
            directions[0]["against_note"], "你否认了腹部细节，这一点不支持它"
        )

    def test_resp_breathing_keeps_legacy_wording(self):
        state, denied = self._state_with_denied("resp_breathing", "呼吸不困难")
        hypothesis = self._hypothesis(
            "lower_respiratory",
            EvidenceRef("resp_breathing", denied.fact_id, "呼吸不困难"),
        )
        self.assertEqual(
            conclusion_module._against_note(hypothesis, state),
            "你否认了呼吸困难等表现，这一点不支持它",
        )

    def test_present_fact_counter_evidence_gives_no_note(self):
        state = closed_state()
        temp = add_fact(state, "temperature_c", 36.8, Assertion.PRESENT, "36.8")
        hypothesis = self._hypothesis(
            "influenza_like",
            EvidenceRef("temperature_c", temp.fact_id, "36.8"),
        )
        self.assertIsNone(conclusion_module._against_note(hypothesis, state))

    def test_no_counter_evidence_gives_no_note(self):
        state = closed_state()
        hypothesis = self._hypothesis("appendicitis")
        self.assertIsNone(conclusion_module._against_note(hypothesis, state))

    def test_superseded_fact_ref_gives_no_note(self):
        # The against ref points at a fact that has since been superseded
        # by a newer fact for the same key (different fact_id): the note
        # must not be generated from the stale ref.
        state, denied_old = self._state_with_denied("gi_details", "没有便血")
        add_fact(state, "gi_details", None, Assertion.ABSENT, "也没有黑便")
        self.assertNotEqual(
            state.facts["gi_details"].fact_id, denied_old.fact_id
        )
        hypothesis = self._hypothesis(
            "appendicitis",
            EvidenceRef("gi_details", denied_old.fact_id, "没有便血"),
        )
        self.assertIsNone(conclusion_module._against_note(hypothesis, state))


class SpokenFramingTests(unittest.TestCase):
    def _sheet(self, level, window=None, alerts=("大小便异常",)):
        return {
            "disposition": {"level": level, "window_hours": window},
            "return_now_if": list(alerts),
        }

    def test_routine_visit_line_opens_with_framing(self):
        line = spoken_close_line(self._sheet("routine_visit", 72))
        self.assertTrue(
            line.startswith("先说结论：这次的情况建议近期当面就诊评估一次。")
        )
        self.assertIn("72 小时内", line)
        self.assertIn("大小便异常", line)
        self.assertIn("120", line)

    def test_self_care_line_opens_with_framing(self):
        line = spoken_close_line(self._sheet("self_care"))
        self.assertTrue(line.startswith("先说结论：这次的情况可以在家先观察处理。"))
        # Batch C rework (P-10): home observation is spoken once, in
        # the framing opener; the main sentence no longer repeats it.
        self.assertIn("在家先观察", line)
        self.assertIn("暂时不需要专门跑一趟医院", line)

    def test_urgent_line_opens_with_framing(self):
        line = spoken_close_line(self._sheet("urgent_today", 24))
        self.assertTrue(
            line.startswith("先说结论：这次的情况比较紧急，需要今天就当面评估。")
        )
        self.assertIn("今天内", line)

    def test_composed_sheet_speaks_with_framing(self):
        sheet = compose_conclusion(closed_state())
        line = spoken_close_line(sheet)
        self.assertTrue(line.startswith("先说结论："))


if __name__ == "__main__":
    unittest.main()
