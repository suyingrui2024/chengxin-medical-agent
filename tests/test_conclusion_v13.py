"""v1.3 conclusion composer tests (docs/问诊结论单设计文档.md §13).

Covers the composer package only:

- directions mode tri-state (off | restricted | on, default restricted)
  and the two visibility gates: directions_visible() stays full-only
  (on), directions_restricted_visible() is restricted-or-on;
- department variable tri-state: on always shows, off never shows,
  unset follows the directions mode;
- visit_notes derivation (§13.4): bound / unbound / abstained shapes,
  80-char chief truncation, evidence dedup cap 3, total cap 8, and
  lint treatment (patient verbatim never voids the sheet);
- oral self-care routing (§13.7): only the self-care selection key
  may become "oral"; narrative group and safety net stay generic.
  The oral group itself is stubbed here on purpose — the content
  worker's oral_routine group is verified at integration level, not
  depended on by this file.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from datetime import datetime
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent.conclusion import (
    RETURN_NOW_IF,
    _build_visit_notes,
    _self_care_group_key,
    compose_conclusion,
    department_visible,
    directions_mode,
    directions_restricted_visible,
    directions_visible,
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
from medagent.reasoning import EvidenceRef, Hypothesis, ReasoningWorkspace


def closed_state(chief="发烧两天"):
    state = CaseState(session_id="v13-test")
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


@contextlib.contextmanager
def _clear_mode_env():
    """Context manager clearing both v1.3 visibility variables."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
        os.environ.pop("MEDAGENT_CONCLUSION_DEPARTMENT", None)
        yield


class DirectionsModeTests(unittest.TestCase):
    def test_mode_tristate_and_default_restricted(self):
        cases = [
            ("off", "off"),
            ("restricted", "restricted"),
            ("on", "on"),
            (None, "restricted"),  # unset defaults to restricted (§13.3)
            ("bogus", "restricted"),  # unrecognised falls back to default
            ("  ON  ", "on"),  # surrounding whitespace / case tolerated
        ]
        for env_value, expected in cases:
            with self.subTest(env_value=env_value):
                with mock.patch.dict(os.environ, {}, clear=False):
                    if env_value is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = env_value
                    self.assertEqual(directions_mode(), expected)

    def test_directions_visible_is_full_only(self):
        for env_value, expected in [
            ("off", False),
            ("restricted", False),
            ("on", True),
            (None, False),  # default restricted is NOT full visibility
        ]:
            with self.subTest(env_value=env_value):
                with mock.patch.dict(os.environ, {}, clear=False):
                    if env_value is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = env_value
                    self.assertEqual(directions_visible(), expected)

    def test_directions_restricted_visible(self):
        for env_value, expected in [
            ("off", False),
            ("restricted", True),
            ("on", True),
            (None, True),  # default restricted
        ]:
            with self.subTest(env_value=env_value):
                with mock.patch.dict(os.environ, {}, clear=False):
                    if env_value is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = env_value
                    self.assertEqual(
                        directions_restricted_visible(), expected
                    )

    def test_sheet_carries_mode_and_both_flags(self):
        state = closed_state(chief="膝盖疼一周")
        with _clear_mode_env():
            sheet = compose_conclusion(state)
            self.assertEqual(sheet["directions_mode"], "restricted")
            self.assertFalse(sheet["directions_visible"])
            self.assertTrue(directions_restricted_visible())
        with mock.patch.dict(
            os.environ, {"MEDAGENT_CONCLUSION_DIRECTIONS": "off"}
        ):
            sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
            self.assertEqual(sheet["directions_mode"], "off")
            self.assertFalse(sheet["directions_visible"])
        with mock.patch.dict(
            os.environ, {"MEDAGENT_CONCLUSION_DIRECTIONS": "on"}
        ):
            sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
            self.assertEqual(sheet["directions_mode"], "on")
            self.assertTrue(sheet["directions_visible"])


class DepartmentVisibleTests(unittest.TestCase):
    def test_department_on_always_shows(self):
        for directions in ("off", "restricted", "on", None):
            with self.subTest(directions=directions):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ["MEDAGENT_CONCLUSION_DEPARTMENT"] = "on"
                    if directions is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = directions
                    self.assertTrue(department_visible())

    def test_department_off_never_shows(self):
        for directions in ("off", "restricted", "on", None):
            with self.subTest(directions=directions):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ["MEDAGENT_CONCLUSION_DEPARTMENT"] = "off"
                    if directions is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = directions
                    self.assertFalse(department_visible())

    def test_department_unset_follows_directions_mode(self):
        for directions, expected in [
            ("off", False),
            ("restricted", True),
            ("on", True),
            (None, True),  # default mode is restricted
        ]:
            with self.subTest(directions=directions):
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("MEDAGENT_CONCLUSION_DEPARTMENT", None)
                    if directions is None:
                        os.environ.pop("MEDAGENT_CONCLUSION_DIRECTIONS", None)
                    else:
                        os.environ["MEDAGENT_CONCLUSION_DIRECTIONS"] = directions
                    self.assertEqual(department_visible(), expected)

    def test_sheet_department_flag_follows_default(self):
        with _clear_mode_env():
            sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
            self.assertTrue(sheet["department_visible"])


class VisitNotesTests(unittest.TestCase):
    def test_unbound_non_abstained_shape(self):
        state = closed_state(chief="咳嗽三天，喉咙疼")
        directions = [
            {"hypothesis_id": "uri", "support_quotes": ["咳嗽三天", "喉咙疼"]},
            {"hypothesis_id": "uri", "support_quotes": ["咳嗽三天", "流鼻涕"]},
        ]
        notes = _build_visit_notes(state, directions, False, None)
        # ① chief, then ④ deduped evidence (max 3): no worry, no baseline.
        self.assertEqual(
            notes, ["咳嗽三天，喉咙疼", "咳嗽三天", "喉咙疼", "流鼻涕"]
        )

    def test_bound_adds_age_and_chronic_names_verbatim(self):
        state = closed_state(chief="头晕两天")
        state.profile_baseline = {
            "birth_year": datetime.now().year - 62,
            "chronic_conditions": ["高血压", "糖尿病"],
        }
        notes = _build_visit_notes(state, [], True, {"object": "脑梗"})
        self.assertEqual(
            notes, ["头晕两天", "脑梗", "年龄：62岁", "高血压", "糖尿病"]
        )

    def test_abstained_has_no_evidence_items(self):
        state = closed_state(chief="膝盖疼一周")
        state.profile_baseline = {"birth_year": datetime.now().year - 40}
        directions = [{"hypothesis_id": "uri", "support_quotes": ["咳嗽"]}]
        notes = _build_visit_notes(state, directions, True, None)
        self.assertEqual(notes, ["膝盖疼一周", "年龄：40岁"])

    def test_chief_truncated_at_80_chars(self):
        long_chief = "疼" * 100
        state = closed_state(chief=long_chief)
        notes = _build_visit_notes(state, [], True, None)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0], "疼" * 80 + "…")

    def test_total_cap_eight(self):
        state = closed_state(chief="咳嗽")
        state.profile_baseline = {
            "birth_year": datetime.now().year - 30,
            "chronic_conditions": ["高血压", "糖尿病", "高血脂", "冠心病"],
        }
        directions = [
            {"hypothesis_id": "uri", "support_quotes": ["咳", "痰", "喘"]},
        ]
        notes = _build_visit_notes(state, directions, False, {"object": "肺炎"})
        self.assertLessEqual(len(notes), 8)
        self.assertEqual(notes[0], "咳嗽")
        self.assertEqual(notes[1], "肺炎")

    def test_compose_abstained_sheet_visit_notes(self):
        state = closed_state(chief="膝盖疼一周")
        add_fact(state, "patient_worry", "关节炎")
        state.profile_baseline = {
            "birth_year": datetime.now().year - 55,
            "chronic_conditions": ["高血压"],
        }
        sheet = compose_conclusion(state)
        self.assertTrue(sheet["directions_abstained"])
        self.assertEqual(
            sheet["visit_notes"],
            ["膝盖疼一周", "关节炎", "年龄：55岁", "高血压"],
        )

    def test_compose_non_abstained_sheet_visit_notes_include_quotes(self):
        state = closed_state(chief="身上起疹子很痒")
        add_fact(state, "skin_details", "一片片风团，很痒")
        sheet = compose_conclusion(state)
        self.assertFalse(sheet["directions_abstained"])
        self.assertTrue(sheet["directions"])
        self.assertEqual(sheet["visit_notes"][0], "身上起疹子很痒")
        all_quotes = set()
        for direction in sheet["directions"]:
            all_quotes.update(direction.get("support_quotes") or [])
        evidence_items = sheet["visit_notes"][1:]
        self.assertLessEqual(len(evidence_items), 3)
        for item in evidence_items:
            self.assertIn(item, all_quotes)

    def test_visit_notes_patient_verbatim_survives_lint(self):
        # "没事" is a banned phrase for system wording; as a patient
        # verbatim visit-notes entry it must not void the sheet
        # (same treatment as support_quotes, §13.4).
        state = closed_state(chief="牙痛，感觉没事，就是疼")
        sheet = compose_conclusion(state)
        self.assertIn("牙痛，感觉没事，就是疼", sheet["visit_notes"])


class OralRoutingTests(unittest.TestCase):
    def test_group_key_only_generic_plus_oral_term(self):
        for term in ("牙痛", "牙疼", "牙齿", "牙龈", "口腔"):
            with self.subTest(term=term):
                self.assertEqual(
                    _self_care_group_key("generic", f"最近{term}得厉害"),
                    "oral",
                )
        self.assertEqual(_self_care_group_key("generic", "头痛三天"), "generic")
        self.assertEqual(_self_care_group_key("generic", ""), "generic")
        # A non-generic sheet group is never rerouted, even on an oral
        # chief complaint: narrative / safety-net / department groups
        # keep sheet_group.
        self.assertEqual(
            _self_care_group_key("respiratory", "牙痛三天"), "respiratory"
        )

    def test_compose_oral_only_changes_self_care_selection(self):
        # Single-evidence direction: sheet_group stays generic and the
        # sheet is not abstained, so no neutral fallback rewrites the
        # self-care group after selection.
        def sheet_for(chief):
            state = closed_state(chief=chief)
            add_fact(state, "urinary_details", "小便时尿道疼，尿频尿急")
            fever = add_fact(state, "associated_symptoms", "有点发烧")
            workspace = ReasoningWorkspace()
            workspace.hypotheses["influenza_like"] = Hypothesis(
                id="influenza_like",
                label="测试假设",
                category="common_condition",
                prior_band="medium",
                status="active",
                evidence_for=[
                    EvidenceRef("associated_symptoms", fever.fact_id, "发烧")
                ],
            )

            class StubProposer:
                def propose(self, _state):
                    return workspace

            sentinel = {
                "do": ["口腔组条目"],
                "avoid": [],
                "measure": [],
                "course": "口腔组 course",
            }
            original = conclusion_module._self_care_group
            seen_keys = []

            def stub_group(key, tier):
                seen_keys.append(key)
                if key == "oral":
                    return sentinel
                return original(key, tier)

            with (
                mock.patch.object(
                    conclusion_module, "RuleBasedProposer", StubProposer
                ),
                mock.patch.object(
                    conclusion_module, "_self_care_group", stub_group
                ),
            ):
                sheet = compose_conclusion(state)
            return sheet, seen_keys

        oral_sheet, oral_keys = sheet_for("左下牙痛三天，咬东西疼")
        self.assertIn("oral", oral_keys)
        self.assertEqual(oral_sheet["self_care"]["do"], ["口腔组条目"])
        self.assertEqual(oral_sheet["outlook"], "口腔组 course")

        neutral_sheet, neutral_keys = sheet_for("身上不舒服三天")
        self.assertNotIn("oral", neutral_keys)
        # Narrative group and safety net are identical: the oral route
        # touched only the self-care selection.
        self.assertEqual(
            oral_sheet["narrative"], neutral_sheet["narrative"]
        )
        self.assertEqual(
            oral_sheet["return_now_if"], neutral_sheet["return_now_if"]
        )
        self.assertEqual(oral_sheet["return_now_if"], RETURN_NOW_IF)


class OralNeutralFallbackTests(unittest.TestCase):
    """D1: a pure-oral chief complaint on an abstained sheet.

    The neutral fallback still runs first (neutral narrative, generic
    safety net), but afterwards the oral self-care group and its
    course are restored — the oral content matches the chief
    complaint itself, not a probed pathway. Real self_care.json
    content throughout: no stubs, so this pins the end-to-end chain
    the supervisor evidence script caught.
    """

    def test_abstained_pure_oral_restores_oral_self_care(self):
        with _clear_mode_env():
            sheet = compose_conclusion(
                closed_state(chief="左下牙痛三天，咬东西疼")
            )
        self.assertTrue(sheet["directions_abstained"])
        self.assertFalse(sheet["directions"])
        self.assertTrue(sheet.get("neutral_fallback"))
        self.assertEqual(
            sheet["self_care"],
            {
                "do": [
                    "用温盐水漱口",
                    "换对侧咀嚼",
                    "睡觉时把枕头垫高",
                    "面部肿胀、张口困难或发热时立即就医",
                ],
                "avoid": ["避免过冷、过热、过硬、过甜的食物"],
                "measure": [],
            },
        )
        # The oral group's course as rendered: the assertion-wording
        # post-processing narrows "新出现或加重" to "新出现加重"
        # (pre-existing behaviour, applies to every sheet).
        self.assertEqual(
            sheet["outlook"],
            "这类口腔不适多数会在数天内逐渐缓解或出现明确变化，"
            "观察期间重点看面部肿胀、张口困难或发热有没有新出现加重。",
        )
        # Narrative stays neutral and the safety net stays generic.
        self.assertIn("身体不适", sheet["narrative"]["framing"])
        self.assertNotIn("口腔", sheet["narrative"]["framing"])
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_abstained_non_oral_stays_generic(self):
        # Control: a non-oral abstained sheet is untouched by the
        # restore — fallback behaviour is exactly as before.
        with _clear_mode_env():
            sheet = compose_conclusion(closed_state(chief="膝盖疼一周"))
        self.assertTrue(sheet["directions_abstained"])
        self.assertFalse(sheet["directions"])
        self.assertTrue(sheet.get("neutral_fallback"))
        self.assertEqual(
            sheet["self_care"],
            {
                "do": [
                    "多休息，避免劳累",
                    "适量喝水",
                    "把症状变化记下来，就诊时告诉医生",
                ],
                "avoid": ["不要自行服用抗生素"],
                "measure": [],
            },
        )
        self.assertEqual(
            sheet["outlook"],
            "多数这类不适会在数天内逐渐缓解或出现明确变化，"
            "观察期间重点看整体状态是好转还是加重。",
        )
        self.assertIn("身体不适", sheet["narrative"]["framing"])
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)


if __name__ == "__main__":
    unittest.main()
