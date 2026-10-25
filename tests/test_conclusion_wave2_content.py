"""Wave-2 content/conclusion gaps from the 9-case live evaluation:
colloquial pathway/question triggers, radiculopathy surfacing, pathway-
specific return_now_if, the spoken close line / emergency explanation
helpers, and the new question-bank entries (sex, pregnancy_status,
recent_exposures, reported_temperature)."""
from __future__ import annotations

import json
import unittest
from importlib import resources
from pathlib import Path

from medagent import conclusion as conclusion_module
from medagent.conclusion import (
    RETURN_NOW_IF,
    compose_conclusion,
    emergency_explanation,
    spoken_close_line,
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
from medagent.reasoning import RuleBasedProposer

# Verbatim back_details from live-eval case5: documented radicular
# features (radiating pain, numbness, electric-shock sensation) with
# cauda-equina negatives — the conclusion showed mechanical_back only.
CASE5_BACK_DETAILS = (
    "疼会往右腿窜，窜到大腿后面，右腿外侧还有点麻，像过电一样偶尔窜一下，"
    "不过腿有力气，走路没问题。大小便都正常，会阴不麻，也没发烧。"
)


def closed_state(chief, facts=()):
    state = CaseState(session_id="wave2-test")
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
    for key, value in facts:
        state.record(
            Fact(
                key=key,
                value=value,
                assertion=Assertion.PRESENT,
                source=Source.PATIENT,
                turn_id="t1",
                evidence=value if isinstance(value, str) else "",
            )
        )
        if isinstance(value, str):
            state.transcript.append({"role": "user", "content": value})
    return state


def load_content(name):
    return json.loads(
        resources.files("medagent.content").joinpath(name).read_text(encoding="utf-8")
    )


class RadiculopathySurfacingTests(unittest.TestCase):
    def test_proposer_enters_radiculopathy_on_colloquial_wording(self):
        state = closed_state(
            "腰痛三天，搬东西扭了一下",
            [("back_details", CASE5_BACK_DETAILS), ("trajectory", "stable"), ("severity", 7)],
        )
        workspace = RuleBasedProposer().propose(state)
        self.assertIn("radiculopathy", workspace.hypotheses)
        quotes = [ref.quote for ref in workspace.hypotheses["radiculopathy"].evidence_for]
        self.assertTrue(quotes)
        for quote in quotes:
            self.assertIn(quote, CASE5_BACK_DETAILS)

    def test_conclusion_directions_include_radiculopathy(self):
        state = closed_state(
            "腰痛三天，搬东西扭了一下",
            [("back_details", CASE5_BACK_DETAILS), ("trajectory", "stable"), ("severity", 7)],
        )
        sheet = compose_conclusion(state)
        ids = [d["hypothesis_id"] for d in sheet["directions"]]
        self.assertIn("radiculopathy", ids)
        # Batch B citation gate: mechanical_back's only support in
        # this fixture was the encoded trajectory value "stable", which
        # must not pose as a patient quote — so that direction drops
        # instead of being propped up by an encoding value.
        self.assertNotIn("mechanical_back", ids)


class PathwayReturnNowIfTests(unittest.TestCase):
    def test_back_case_gets_back_specific_safety_net(self):
        # Batch B sheet_group gate: the old fixture's back directions
        # each carried at most one valid quote, so the safety net now
        # correctly falls back to generic. This fixture is a back case
        # meeting the >= 2-valid-quotes gate (two affirmed cauda
        # clues in the back details), so the back-specific list still
        # applies; the single-evidence generic fallback is pinned by
        # the batch-B sheet-group tests.
        state = closed_state(
            "腰痛三天，搬东西扭了一下",
            [
                (
                    "back_details",
                    "疼会往腿上窜，大小便费劲，前几天摔伤过腰。",
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

    def test_respiratory_case_gets_respiratory_safety_net(self):
        # Batch B sheet_group gate: the fixture carries a second
        # affirmed respiratory clue (sore throat in the chief
        # complaint, alongside the cough details) so a respiratory
        # direction reaches >= 2 valid quotes and drives the net.
        state = closed_state(
            "发烧两天，咳嗽，嗓子疼",
            [("resp_cough_details", "干咳为主"), ("associated_symptoms", "全身酸痛、乏力")],
        )
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["return_now_if"], ["呼吸困难加重", "嘴唇发紫", "高热不退"])

    def test_abdominal_safety_net_leads_with_pain_escalation(self):
        # The spoken close line voices only the first alert; for abdominal
        # cases that must be the near-universal escalation signal, not the
        # bleed-specific one (live-eval run3 case8: an appendicitis-shaped
        # picture was warned about hematemesis/melena only).
        state = closed_state(
            "肚子疼，右下腹疼",
            [("gi_details", "一开始肚脐周围疼，后来转移到右下腹，食欲差"), ("trajectory", "stable")],
        )
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["return_now_if"][0], "腹痛迅速加重")
        self.assertIn("呕血或大便发黑", sheet["return_now_if"])
        self.assertIn("腹痛迅速加重", spoken_close_line(sheet))

    def test_abstained_sheet_uses_explored_pathway_for_safety_net(self):
        # Live-eval run3 case3 shape: directions abstain, but the abdominal
        # pathway was explored (gi_details collected before a secondary
        # urinary probe). The safety net must be the abdominal list, not
        # the generic four — while self-care stays generic by design.
        state = closed_state(
            "最近老觉得累，肚子也不舒服",
            [
                (
                    "gi_details",
                    "肚脐周围隐隐地疼，断断续续，位置没什么变化；食欲还行；"
                    "没吐过；大便有时候有点偏稀，没有便血也没有黑便；没有发烧",
                ),
                ("urinary_details", "小便正常，没有尿频尿急"),
                ("trajectory", "stable"),
                ("severity", 4),
            ],
        )
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["directions"], [])
        self.assertEqual(sheet["return_now_if"][0], "腹痛迅速加重")
        self.assertIn("呕血或大便发黑", sheet["return_now_if"])
        self.assertEqual(
            sheet["self_care"]["do"],
            ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
        )

    def test_uncovered_complaint_falls_back_to_generic_four(self):
        sheet = compose_conclusion(closed_state("膝盖疼一周"))
        self.assertEqual(sheet["return_now_if"], RETURN_NOW_IF)

    def test_summary_list_used_when_no_pathway(self):
        summary = {"return_now_if": ["自定义警示"]}
        sheet = compose_conclusion(closed_state("膝盖疼一周"), summary=summary)
        self.assertEqual(sheet["return_now_if"], ["自定义警示"])

    def test_pathway_lists_have_review_metadata_and_pass_banned_lint(self):
        data = load_content("self_care.json")["return_now_if"]
        self.assertEqual(data["reviewed_by"], "engineering-v1")
        expected = {
            "respiratory",
            "chest",
            "abdominal",
            "headache",
            "dizziness",
            "back",
            "urinary",
            "skin",
        }
        self.assertEqual(set(data["by_pathway"]), expected)
        for items in data["by_pathway"].values():
            self.assertTrue(items)
            for item in items:
                for phrase in conclusion_module.BANNED_PHRASES:
                    self.assertNotIn(phrase, item)


class SpokenCloseLineTests(unittest.TestCase):
    def _sheet(self, level, window=None, alerts=("大小便异常",)):
        return {
            "disposition": {"level": level, "window_hours": window},
            "return_now_if": list(alerts),
        }

    def test_no_conclusion_no_line(self):
        self.assertEqual(spoken_close_line(None), "")
        self.assertEqual(spoken_close_line({}), "")
        self.assertEqual(spoken_close_line({"disposition": {}}), "")

    def test_routine_visit_mentions_window_and_first_alert(self):
        line = spoken_close_line(self._sheet("routine_visit", 72))
        self.assertIn("72 小时内", line)
        self.assertIn("大小便异常", line)

    def test_self_care_line(self):
        line = spoken_close_line(self._sheet("self_care"))
        self.assertIn("在家观察", line)
        self.assertIn("大小便异常", line)

    def test_urgent_line(self):
        line = spoken_close_line(self._sheet("urgent_today", 24))
        self.assertIn("今天内", line)

    def test_lines_pass_banned_lint(self):
        for sheet in (
            self._sheet("routine_visit", 72),
            self._sheet("self_care"),
            self._sheet("urgent_today", 24),
            self._sheet("routine_visit", None, alerts=()),
        ):
            line = spoken_close_line(sheet)
            self.assertTrue(line)
            for phrase in conclusion_module.BANNED_PHRASES:
                self.assertNotIn(phrase, line)


class EmergencyExplanationTests(unittest.TestCase):
    def test_names_triggers_and_gives_waiting_guidance(self):
        line = emergency_explanation(["大量或无法控制的出血"])
        self.assertIn("大量或无法控制的出血", line)
        self.assertIn("拨打 120", line)
        self.assertIn("不要自行开车", line)
        self.assertIn("陪同", line)

    def test_dict_triggers_use_patient_label(self):
        line = emergency_explanation([{"id": "bleeding", "label": "大量出血"}])
        self.assertIn("大量出血", line)

    def test_empty_triggers_fall_back_to_generic_wording(self):
        line = emergency_explanation([])
        self.assertIn("危险信号", line)
        self.assertIn("拨打 120", line)

    def test_lines_pass_banned_lint(self):
        for triggers in (["持续压榨样胸痛"], [], None):
            line = emergency_explanation(triggers)
            for phrase in conclusion_module.BANNED_PHRASES:
                self.assertNotIn(phrase, line)


class QuestionBankWave2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_content("questions.json")
        cls.by_id = {q["id"]: q for q in cls.raw["questions"]}

    def test_sex_question_is_non_blocking_opening_choice(self):
        sex = self.by_id["sex"]
        self.assertEqual(sex["phase"], "opening")
        self.assertEqual(sex["fact_key"], "sex")
        self.assertIs(sex["blocking"], False)
        self.assertEqual(
            {o["value"] for o in sex["options"]}, {"male", "female"}
        )

    def test_pregnancy_status_metadata_for_next_engine_wave(self):
        preg = self.by_id["pregnancy_status"]
        self.assertEqual(preg["fact_key"], "pregnancy_status")
        self.assertEqual(preg["excludes_fact_equals"], {"sex": ["male"]})
        self.assertEqual(preg["age_between_when_known"], [12, 55])
        terms = preg["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("腹痛", terms)
        self.assertIn("尿痛", terms)

    def test_reported_temperature_is_distinct_from_observation_field(self):
        item = self.by_id["reported_temperature"]
        self.assertEqual(item["fact_key"], "reported_temperature")
        self.assertIs(item["blocking"], False)
        self.assertIn("temperature_c", item["note"])
        terms = item["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("发烧", terms)

    def test_recent_exposures_question_exists(self):
        item = self.by_id["recent_exposures"]
        self.assertEqual(item["fact_key"], "recent_exposures")
        terms = item["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("皮疹", terms)
        self.assertIn("过敏", terms)

    def test_detail_prompts_cover_the_missed_probes(self):
        self.assertIn("位置有没有变化", self.by_id["gi_details"]["prompt"])
        self.assertIn("食欲", self.by_id["gi_details"]["prompt"])
        self.assertIn("怕光", self.by_id["head_details"]["prompt"])
        self.assertIn("怕吵", self.by_id["head_details"]["prompt"])
        self.assertIn("洗澡", self.by_id["skin_details"]["prompt"])
        self.assertIn("持续多久", self.by_id["dizzy_details"]["prompt"])

    def test_colloquial_triggers_present(self):
        gi = self.by_id["gi_details"]["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("肚子不舒服", gi)
        urinary = self.by_id["urinary_details"]["applies_when"][
            "chief_complaint_contains_any"
        ]
        for term in ("老想上厕所", "尿的时候疼", "尿不尽", "小便不舒服"):
            self.assertIn(term, urinary)
        back = self.by_id["back_details"]["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("闪腰", back)
        dizzy = self.by_id["dizzy_details"]["applies_when"]["chief_complaint_contains_any"]
        self.assertIn("天旋地转", dizzy)


class DifferentialsWave2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_content("differentials.json")
        cls.by_id = {p["id"]: p for p in cls.raw["pathways"]}

    def test_version_binding_with_conclusion_fixture(self):
        fixture_path = (
            Path(__file__).resolve().parent / "fixtures" / "conclusion_cases_v0.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["differentials_version"], self.raw["version"])

    def test_pathway_colloquial_triggers(self):
        abdominal = self.by_id["abdominal"]["chief_complaint_contains_any"]
        self.assertIn("肚子不舒服", abdominal)
        urinary = self.by_id["urinary"]["chief_complaint_contains_any"]
        self.assertIn("老想上厕所", urinary)
        self.assertIn("尿的时候疼", urinary)
        back = self.by_id["low_back"]["chief_complaint_contains_any"]
        self.assertIn("闪腰", back)
        headache = self.by_id["headache"]["chief_complaint_contains_any"]
        self.assertIn("脑袋疼", headache)

    def test_radiculopathy_support_covers_patient_wording(self):
        hypothesis = next(
            h for h in self.by_id["low_back"]["hypotheses"] if h["id"] == "radiculopathy"
        )
        terms = hypothesis["support"][0]["contains_any"]
        for term in ("窜到大腿", "过电", "放射痛", "腿发麻"):
            self.assertIn(term, terms)


if __name__ == "__main__":
    unittest.main()
