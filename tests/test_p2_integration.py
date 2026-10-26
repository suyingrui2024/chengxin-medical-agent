"""P2 integration seams: attach-gate resolution (P2d), the adult age
prompt wording (P1 legacy item), the conclusion allergy hint (P2c),
the summary-payload archive reference (P2c), and the adaptive
engine's profile_baseline context key (P2c).

Store-level measurement generalization lives in test_patient_records.py
and the HTTP routes in test_patient_records_api.py; this file covers
the seams that neither of those reaches.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from medagent.agent import AdaptiveConsultationEngine
from medagent.conclusion import compose_conclusion
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
from medagent.knowledge import ADULT_AGE_PROMPT, ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.server import _consultation_patient_id
from medagent.triage import TriageService


def _state_with_consult_for(value):
    state = CaseState(session_id="p2-test")
    state.record(
        Fact(
            key="consult_for",
            value=value,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="我自己" if value == "self" else "我的孩子",
        )
    )
    return state


def _closed_state():
    state = CaseState(session_id="p2-conclusion")
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(RiskLevel.ROUTINE)
    state.record(
        Fact(
            key="chief_complaint",
            value="发烧两天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="发烧两天",
        )
    )
    state.transcript.append({"role": "user", "content": "发烧两天"})
    return state


class AttachGateTests(unittest.TestCase):
    def test_bound_session_resolves_to_the_bound_member(self):
        state = _state_with_consult_for("child")
        state.runtime_snapshot["consultation_patient_id"] = "member-1"
        self.assertEqual(
            _consultation_patient_id(state, "default-1"), "member-1"
        )

    def test_unbound_self_session_keeps_default_semantics(self):
        state = _state_with_consult_for("self")
        self.assertEqual(
            _consultation_patient_id(state, "default-1"), "default-1"
        )

    def test_unbound_proxy_session_resolves_to_nothing(self):
        state = _state_with_consult_for("child")
        self.assertIsNone(_consultation_patient_id(state, "default-1"))

    def test_session_without_consult_for_resolves_to_nothing(self):
        state = CaseState(session_id="p2-test")
        self.assertIsNone(_consultation_patient_id(state, "default-1"))


class AdultAgePromptTests(unittest.TestCase):
    def test_adult_age_prompt_is_second_person(self):
        self.assertNotIn("患者", ADULT_AGE_PROMPT)
        self.assertTrue(ADULT_AGE_PROMPT.startswith("你"))
        self.assertEqual(
            AdaptiveConsultationEngine._ADULT_AGE_PROMPT, ADULT_AGE_PROMPT
        )


class ConclusionAllergyHintTests(unittest.TestCase):
    def test_hint_lands_in_its_own_field_not_in_avoid(self):
        # 通路测试（mock 文案）: the avoid line below is invented for
        # this test. It proves the wiring (hint field, not avoid list,
        # no gap leak) — it does NOT prove the hint fires on real
        # self_care 1.3 content. The real-content test below pins that
        # the hint is dormant on 1.3 (0 of 19 groups trigger).
        state = _closed_state()
        fake_care = {
            "do": ["多休息"],
            "avoid": ["不要自行服用抗生素，过敏史者用药前请核对"],
            "measure": [],
            "course": None,
        }
        with mock.patch(
            "medagent.conclusion._self_care_group", return_value=fake_care
        ):
            sheet = compose_conclusion(
                state,
                baseline_allergies=[{"kind": "drug", "name": "青霉素"}],
            )
        self.assertIsNotNone(sheet)
        self.assertEqual(
            sheet["self_care"]["allergy_hint"],
            "你档案里记有青霉素过敏，涉及用药请先核对",
        )
        self.assertEqual(
            sheet["self_care"]["avoid"],
            ["不要自行服用抗生素，过敏史者用药前请核对"],
        )
        self.assertFalse(
            any("青霉素" in gap.get("label", "") for gap in sheet["gaps"])
        )

    def test_real_self_care_1_3_triggers_zero_of_19_groups(self):
        # Real-content count (M1): run build_allergy_hint over every
        # self_care 1.3 group's actual avoid list. 19 groups exist and
        # several avoid lines mention medication, but none mentions
        # allergy, so the both-words trigger fires 0 times. If a future
        # content release adds an allergy line, this test must be
        # updated alongside the clinical review — not silently passed
        # by editing self_care wording here.
        from medagent.baseline_projection import build_allergy_hint
        from medagent.conclusion import _load_json

        data = _load_json("self_care.json")
        groups = data.get("groups", [])
        self.assertEqual(len(groups), 19)
        allergies = [{"kind": "drug", "name": "青霉素"}]
        triggered = [
            group
            for group in groups
            if build_allergy_hint(allergies, group.get("avoid", [])) is not None
        ]
        medication_groups = [
            group
            for group in groups
            if any("药" in str(line) for line in group.get("avoid", []))
        ]
        self.assertGreater(len(medication_groups), 0)
        self.assertEqual(triggered, [])

    def test_current_content_has_no_allergy_avoid_line_so_hint_stays_dormant(self):
        # Honest pin: no self_care 1.3 avoid line mentions allergy, so
        # build_allergy_hint's both-words trigger cannot fire on real
        # content today. The wiring above proves the path; this test
        # documents that the hint needs a future content line to fire.
        sheet = compose_conclusion(
            _closed_state(),
            baseline_allergies=[{"kind": "drug", "name": "青霉素"}],
        )
        self.assertIsNotNone(sheet)
        self.assertNotIn("allergy_hint", sheet["self_care"])


class BaselineReferenceConsumptionTests(unittest.TestCase):
    def _adaptive_engine(self):
        return AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
            backend=SimpleNamespace(name="demo"),
        )

    def test_adaptive_context_gates_profile_baseline_on_the_switch(self):
        engine = self._adaptive_engine()
        state, _reply = engine.create_session()
        projection = {
            "chronic_conditions": ["高血压"],
            "allergies": [{"name": "青霉素", "kind": "drug"}],
        }
        state.profile_baseline = dict(projection)
        state.baseline_reference_enabled = True
        context = engine._context(state, "你好")
        self.assertEqual(context["profile_baseline"], projection)
        self.assertNotIn("profile_baseline", state.facts)
        state.baseline_reference_enabled = False
        self.assertNotIn("profile_baseline", engine._context(state, "你好"))

    def test_summary_payload_carries_baseline_reference_without_facts(self):
        from medagent.consultation import ConsultationEngine

        engine = ConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=EventStore(),
            ranker=OrderedQuestionRanker(),
        )
        state, _reply = engine.create_session()
        state.profile_baseline = {"chronic_conditions": ["高血压"]}
        state.baseline_reference_enabled = True
        payload = engine._summary_payload(state, review_status="pending_review")
        self.assertEqual(
            payload["profile_baseline"], {"chronic_conditions": ["高血压"]}
        )
        self.assertNotIn("chronic_conditions", state.facts)
        state.baseline_reference_enabled = False
        payload = engine._summary_payload(state, review_status="pending_review")
        self.assertEqual(payload["profile_baseline"], {})


if __name__ == "__main__":
    unittest.main()
