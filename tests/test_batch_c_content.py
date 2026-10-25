"""Batch C CONTENT worker: conclusion narrative / outlook / framing /
return_now_if / window wording, plus the two additive questions.

Scope discipline (batch-C brief): only conclusion.py's narrative /
outlook / framing / return_now_if / window layer, content JSON and
questions.json additive items, and this test file. Worry/goal slot
logic and the close-line code are NOT touched here; direction and
department visibility still rest on the C3/C12 gates (directions
stay in the sheet, invisible, when the neutral fallback neutralises
the patient-visible layer). No new clinical thresholds or diagnostic
wording are introduced.
"""
from __future__ import annotations

import json
import unittest
from importlib import resources

from medagent.conclusion import (
    RETURN_NOW_IF,
    apply_neutral_fallback,
    compose_conclusion,
    format_window_text,
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
from medagent.knowledge import ContentRepository, question_fact_gates_pass


def closed_state(chief, extra=None):
    state = CaseState(session_id="batch-c-content")
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
    for key, value, assertion, evidence in extra or []:
        state.record(
            Fact(
                key=key,
                value=value,
                assertion=assertion,
                source=Source.PATIENT,
                turn_id="t1",
                evidence=evidence if evidence is not None else value,
            )
        )
    return state


def sheet_for(chief, extra=None, summary=None):
    sheet = compose_conclusion(closed_state(chief, extra), summary)
    assert sheet is not None
    return sheet


def gap_summary():
    # temperature_c is a MEASUREMENT_FACT, so this gap is significant
    # and upgrades a self_care disposition to routine_visit.
    return {"unknown_details": [{"key": "temperature_c", "label": "体温"}]}


class WindowSingleSourceTests(unittest.TestCase):
    def test_g02_upgrade_window_sheet_and_helper_agree(self):
        sheet = sheet_for(
            "我拉肚子，肚子疼。",
            [("gi_details", "拉肚子，水样便", Assertion.PRESENT, None)],
            summary=gap_summary(),
        )
        disposition = sheet["disposition"]
        self.assertEqual(disposition["level"], "routine_visit")
        self.assertIn("后还没有好转", disposition["text"])
        self.assertNotIn("小时内到线下", disposition["text"])
        self.assertEqual(format_window_text(disposition), disposition["text"])
        self.assertEqual(
            format_window_text(disposition, disposition["window_hours"]),
            disposition["text"],
        )

    def test_u02_upgrade_window_48h_single_source(self):
        sheet = sheet_for(
            "我小便时不舒服，尿频。",
            [("urinary_details", "尿频", Assertion.PRESENT, None)],
            summary=gap_summary(),
        )
        disposition = sheet["disposition"]
        if disposition["level"] == "routine_visit" and "后还没有好转" in disposition["text"]:
            self.assertEqual(format_window_text(disposition), disposition["text"])
        # The helper itself is the single source for the upgrade
        # variant at the 48-hour window seen in U-02.
        self.assertEqual(
            format_window_text(
                {
                    "level": "routine_visit",
                    "window_hours": 48,
                    "window_kind": "after_if_not_improved",
                },
                48,
            ),
            "可以在家先观察；把下方缺的信息补上，"
            "如果 48 小时后还没有好转，请到线下医疗机构就诊。",
        )

    def test_format_window_text_rule_out_and_plain_variants(self):
        self.assertEqual(
            format_window_text(
                {
                    "level": "routine_visit",
                    "window_hours": 48,
                    "rule_out_label": "某种急症",
                },
                48,
            ),
            "可以在家先观察；请在 48 小时内到线下医疗机构就诊，排除 某种急症。",
        )
        self.assertEqual(
            format_window_text("routine_visit", 48),
            "可以在家先观察；请在 48 小时内到线下医疗机构就诊评估。",
        )
        self.assertEqual(
            format_window_text("urgent_today"),
            "建议今天内到线下医疗机构当面评估，不要在家观察等待。",
        )
        self.assertEqual(
            format_window_text("self_care"),
            "可以先在家观察；如果提前加重或出现下方任何警示信号，请及时就医。",
        )
        # A composed rule-out sheet round-trips through the helper.
        sheet = sheet_for(
            "我拉肚子，肚子疼，右下腹转移痛。",
            [
                ("gi_details", "右下腹转移痛", Assertion.PRESENT, None),
                ("trajectory", "worsening", Assertion.PRESENT, None),
                ("severity", 8, Assertion.PRESENT, "8分"),
            ],
        )
        disposition = sheet["disposition"]
        if disposition["level"] == "routine_visit":
            self.assertEqual(format_window_text(disposition), disposition["text"])


class WorryReuseTests(unittest.TestCase):
    def test_e01_diabetes_worry_uses_batch_b_slot(self):
        sheet = sheet_for(
            "我最近总口渴，一天喝好多水，夜里也要起来上厕所好几次。",
            [("patient_worry", "糖尿病", Assertion.PRESENT, "我这是不是糖尿病")],
        )
        worry = sheet["worry"]
        self.assertIsNotNone(worry)
        # Reuse, not a parallel slot: the batch-B shape is unchanged.
        self.assertEqual(set(worry.keys()), {"object", "response"})
        self.assertEqual(worry["object"], "糖尿病")
        self.assertIn("糖尿病", worry["response"])
        # The worry answer is present even on a neutral off-pathway sheet.
        self.assertIn("身体不适", sheet["narrative"]["framing"])


class OffPathwayFramingTests(unittest.TestCase):
    def test_e02_polyuria_as_recorded_is_neutral_not_urinary(self):
        # Round-1 E-02 facts as recorded: polydipsia/polyuria plus
        # weight loss, fatigue and increased appetite — no dysuria
        # tokens, so no urinary direction is selected and the batch-A
        # neutral fallback (reused, not duplicated) governs.
        sheet = sheet_for(
            "我最近总口渴，一天喝好多水，夜里也要起来上厕所好几次。",
            [
                ("urinary_details", "夜里起来上厕所好几次，白天也总想上厕所", Assertion.PRESENT, None),
                ("associated_symptoms", "体重掉了几斤，还容易乏力，饭量比以前大了", Assertion.PRESENT, None),
            ],
        )
        self.assertTrue(sheet["directions_abstained"])
        self.assertTrue(sheet.get("neutral_fallback"))
        self.assertIn("身体不适", sheet["narrative"]["framing"])
        self.assertNotIn("泌尿", sheet["narrative"]["framing"])
        self.assertNotIn("泌尿", sheet["narrative"]["severity"])
        self.assertEqual(sheet["return_now_if"], list(RETURN_NOW_IF))
        self.assertNotIn("腰痛伴高热", sheet["return_now_if"])
        self.assertNotIn("完全尿不出", sheet["return_now_if"])
        self.assertEqual(
            sheet["self_care"]["do"],
            ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
        )
        self.assertFalse(sheet["directions_visible"])

    def test_k09_gum_bleeding_as_recorded_is_neutral_not_skin(self):
        # Round-1 K-09 facts as recorded: the skin probe was denied
        # (no rash), so no skin direction is selected.
        sheet = sheet_for(
            "我刷牙总出血，牙龈红肿，咬苹果也出血。",
            [
                ("skin_details", "身上皮肤没有出现皮疹、红疹或起疙瘩", Assertion.PRESENT, "身上皮肤没有出现皮疹、红疹或起疙瘩"),
                ("associated_symptoms", "牙龈红肿", Assertion.PRESENT, None),
            ],
        )
        self.assertTrue(sheet.get("neutral_fallback"))
        self.assertIn("身体不适", sheet["narrative"]["framing"])
        self.assertNotIn("皮肤", sheet["narrative"]["framing"])
        self.assertNotIn("皮肤", sheet["narrative"]["severity"])
        self.assertNotIn("皮疹迅速扩散伴发热", sheet["return_now_if"])
        self.assertNotIn("皮疹", " ".join(sheet["self_care"]["measure"]))
        self.assertFalse(sheet["directions_visible"])

    def test_y07_eyelid_as_recorded_is_neutral_not_skin(self):
        # Round-1 Y-07 facts as recorded, minus the uncertain support
        # word (covered by the next test): an eyelid lump with pain,
        # no skin-pathway direction.
        sheet = sheet_for(
            "我眼皮上长了个红肿的小包，碰着疼。",
            [
                ("skin_details", "眼皮上红肿的小包，碰着疼", Assertion.PRESENT, None),
            ],
        )
        self.assertTrue(sheet.get("neutral_fallback"))
        self.assertIn("身体不适", sheet["narrative"]["framing"])
        self.assertNotIn("皮肤不适", sheet["narrative"]["framing"])
        self.assertNotIn("皮疹", sheet["narrative"]["framing"])
        self.assertNotIn("皮疹", sheet["narrative"]["severity"])
        self.assertNotIn("皮疹迅速扩散伴发热", sheet["return_now_if"])
        self.assertFalse(sheet["directions_visible"])

    def test_neutral_fallback_mechanism_reused_not_reinvented(self):
        # Direct proof that the batch-A mechanism itself strips a
        # pathway sheet (framing, self-care, outlook, safety net)
        # down to the generic group — the composer reuses it.
        sheet = {
            "tier": "routine",
            "disposition": {"level": "self_care"},
            "narrative": {
                "framing": "先给你一个整体判断：这次的皮肤不适，从目前信息看属于可以先在家观察处理的类型。",
                "reasoning": None,
                "severity": "严重程度判断：目前看程度较轻，重点是观察变化。",
            },
            "self_care": {
                "do": ["保持患处清洁干燥，避免抓挠"],
                "avoid": ["不要自行涂抹激素药膏或乱用偏方"],
                "measure": ["记录皮疹范围变化，可拍照留存对比"],
            },
            "outlook": "这类皮疹在避开可疑诱因后，多数数天内开始消退",
            "return_now_if": ["皮疹迅速扩散伴发热"],
        }
        neutral = apply_neutral_fallback(sheet)
        self.assertIn("身体不适", neutral["narrative"]["framing"])
        self.assertNotIn("皮肤", neutral["narrative"]["framing"])
        self.assertEqual(neutral["return_now_if"], list(RETURN_NOW_IF))
        self.assertNotIn("皮疹", " ".join(neutral["self_care"]["measure"]))
        self.assertTrue(neutral.get("neutral_fallback"))

    def test_y07_uncertain_single_word_is_not_direction_support(self):
        sheet = sheet_for(
            "我眼皮上长了个红肿的小包，碰着疼。",
            [
                (
                    "skin_details",
                    "没有用新药，吃新食物或接触新东西不清楚",
                    Assertion.PRESENT,
                    "没有用新药，吃新食物或接触新东西不清楚",
                ),
            ],
        )
        for direction in sheet["directions"]:
            self.assertNotIn("接触", direction.get("support_quotes") or [])
            self.assertNotEqual(direction.get("hypothesis_id"), "dermatitis")
        self.assertIn("身体不适", sheet["narrative"]["framing"])

    def test_neutral_fallback_still_idempotent(self):
        sheet = sheet_for("膝盖疼一周")
        neutral = apply_neutral_fallback(sheet)
        self.assertEqual(apply_neutral_fallback(neutral), neutral)


class OutlookAndSafetyNetTests(unittest.TestCase):
    def test_e04_two_month_gradual_course_has_no_short_term_relief(self):
        sheet = sheet_for(
            "我最近总觉得乏力，体重轻了，怕热多汗。",
            [("onset", "近两个月逐渐出现", Assertion.PRESENT, None)],
        )
        self.assertNotIn("数天内", sheet["outlook"] or "")
        self.assertIn("持续一段时间", sheet["outlook"] or "")

    def test_u09_two_week_gradual_course_has_no_short_term_relief(self):
        sheet = sheet_for(
            "我最近夜里总起来上厕所，白天也多。",
            [("onset", "近两周逐渐出现", Assertion.PRESENT, None)],
        )
        self.assertNotIn("数天内", sheet["outlook"] or "")
        self.assertIn("持续一段时间", sheet["outlook"] or "")

    def test_t04_present_symptom_not_written_as_new_and_denied_is_qualified(self):
        sheet = sheet_for(
            "我头晕，一转头就天旋地转。",
            [
                ("dizzy_details", "转头就天旋地转，有恶心想吐", Assertion.PRESENT, None),
                ("associated_symptoms", "没有说话不清", Assertion.ABSENT, "没有说话不清"),
            ],
        )
        outlook = sheet["outlook"] or ""
        self.assertNotIn("新出现恶心", outlook)
        self.assertNotIn("新出现耳鸣、恶心", outlook)
        # The not-yet-present token stays as a new-appearance watch item.
        self.assertIn("新出现耳鸣", outlook)
        self.assertIn("言语不清新出现", sheet["return_now_if"])
        self.assertNotIn("言语不清", sheet["return_now_if"])
        # Already-qualified items are untouched.
        self.assertIn("肢体无力新出现", sheet["return_now_if"])
        self.assertIn("走路不稳明显加重", sheet["return_now_if"])


class InfantAndEyeQuestionTests(unittest.TestCase):
    def _questions(self):
        raw = json.loads(
            resources.files("medagent.content")
            .joinpath("questions.json")
            .read_text(encoding="utf-8")
        )
        return {item["id"]: item for item in raw["questions"]}

    def test_p17_infant_observable_question_is_additive_and_observable_only(self):
        questions = self._questions()
        item = questions["infant_observable_signs"]
        for term in ("哭闹", "拒食", "精神", "尿量", "排便性状"):
            self.assertIn(term, item["prompt"])
        self.assertNotIn("什么样的痛", item["prompt"])
        self.assertNotIn("疼痛的位置", item["prompt"])
        self.assertEqual(item["age_between_when_known"], [0, 3])
        self.assertEqual(item["fact_key"], "infant_observable_signs")
        # Existing gi_details is untouched (id and text preserved).
        self.assertEqual(
            questions["gi_details"]["prompt"],
            "腹痛在哪个位置、是什么样的痛（绞痛、胀痛还是隐痛）？"
            "疼痛的位置有没有变化，一开始在哪、现在在哪？"
            "和吃饭、排便有关系吗？食欲有没有变化，有没有吃不下？"
            "有没有呕吐、腹泻、便血或发热？",
        )

    def test_p17_infant_question_gates_on_age(self):
        repo = ContentRepository()
        question = repo.question("infant_observable_signs")
        infant = closed_state(
            "宝宝拉肚子",
            [
                ("consult_for", "child", Assertion.PRESENT, None),
                ("age_years", 1, Assertion.PRESENT, None),
            ],
        )
        self.assertTrue(repo.question_applies_in_state(question, infant))
        self.assertTrue(question_fact_gates_pass(question, infant.facts))
        older = closed_state(
            "宝宝拉肚子",
            [
                ("consult_for", "child", Assertion.PRESENT, None),
                ("age_years", 10, Assertion.PRESENT, None),
            ],
        )
        self.assertFalse(question_fact_gates_pass(question, older.facts))

    def test_p17_infant_sheet_uses_observable_worsening_not_pain_reporting(self):
        sheet = sheet_for(
            "宝宝拉肚子",
            [
                ("consult_for", "child", Assertion.PRESENT, None),
                ("age_years", 1, Assertion.PRESENT, None),
                ("gi_details", "拉肚子，水样便", Assertion.PRESENT, None),
                ("associated_symptoms", "拉肚子", Assertion.PRESENT, None),
            ],
        )
        self.assertNotIn("疼痛的部位", " ".join(sheet["self_care"]["do"]))
        self.assertIn(
            "把哭闹、拒食、精神、尿量和排便性状的变化记下来，就诊时告诉医生",
            sheet["self_care"]["do"],
        )
        self.assertNotIn("腹痛迅速加重", sheet["return_now_if"])
        self.assertIn("哭闹明显加重、精神变差或尿量明显减少", sheet["return_now_if"])

    def test_p17_older_child_keeps_existing_wording(self):
        sheet = sheet_for(
            "宝宝拉肚子",
            [
                ("consult_for", "child", Assertion.PRESENT, None),
                ("age_years", 10, Assertion.PRESENT, None),
                ("gi_details", "拉肚子，水样便", Assertion.PRESENT, None),
                ("associated_symptoms", "拉肚子", Assertion.PRESENT, None),
            ],
        )
        self.assertIn("把疼痛的部位、程度和变化记下来", sheet["self_care"]["do"])
        self.assertIn("腹痛迅速加重", sheet["return_now_if"])

    def test_y03_eye_warning_question_is_additive(self):
        questions = self._questions()
        item = questions["eye_warning_signs"]
        for term in ("视力下降", "剧烈疼痛", "外伤", "发烧"):
            self.assertIn(term, item["prompt"])
        self.assertEqual(item["fact_key"], "eye_warning_signs")
        repo = ContentRepository()
        question = repo.question("eye_warning_signs")
        state = closed_state("眼睛红，痒")
        self.assertTrue(repo.question_applies_in_state(question, state))

    def test_y03_eye_sheet_uses_generic_worsening_not_invented_specific_items(self):
        sheet = sheet_for("眼睛红，痒")
        self.assertEqual(
            sheet["return_now_if"],
            list(RETURN_NOW_IF) + ["眼睛不适明显加重"],
        )
        joined = " ".join(sheet["return_now_if"])
        self.assertNotIn("视力下降", joined)
        self.assertNotIn("剧烈疼痛", joined)
        self.assertNotIn("外伤", joined)


class SpokenCloseReworkTests(unittest.TestCase):
    """Batch C rework: spoken close line must single-source G-02/U-02
    and de-duplicate P-10. Framing and the first-alert suffix are
    untouched; only the main sentence is under test here."""

    def _line(self, level, window=None, text=None, alerts=()):
        disposition = {"level": level, "window_hours": window}
        if text is not None:
            disposition["text"] = text
        return spoken_close_line(
            {"disposition": disposition, "return_now_if": list(alerts)}
        )

    def test_g02_u02_upgrade_spoken_matches_sheet(self):
        sheet_text = (
            "可以在家先观察；把下方缺的信息补上，"
            "如果 48 小时后还没有好转，请到线下医疗机构就诊。"
        )
        line = self._line("routine_visit", 48, sheet_text)
        self.assertIn(sheet_text, line)
        self.assertNotIn("48 小时内到线下", line)

    def test_g02_u02_rule_out_visible_kept_hidden_dropped(self):
        visible = (
            "可以在家先观察；请在 48 小时内到线下医疗机构就诊，"
            "排除 阑尾炎。"
        )
        self.assertIn("排除 阑尾炎", self._line("routine_visit", 48, visible))
        hidden = "可以在家先观察；请在 48 小时内到线下医疗机构就诊评估。"
        line = self._line("routine_visit", 48, hidden)
        self.assertNotIn("排除", line)
        self.assertIn(hidden, line)

    def test_g02_u02_plain_window_unchanged(self):
        line = self._line("routine_visit", 72)
        self.assertIn("72 小时内到线下医疗机构就诊评估", line)
        self.assertIn(
            "请尽快到线下医疗机构就诊评估",
            self._line("routine_visit", None),
        )

    def test_p10_self_care_no_alert_home_mentioned_once(self):
        line = self._line("self_care", 72)
        self.assertTrue(
            line.startswith("先说结论：这次的情况可以在家先观察处理。")
        )
        self.assertIn("暂时不需要专门跑一趟医院", line)
        self.assertEqual(line.count("在家"), 1)
        self.assertNotIn("可以先在家观察，", line)


if __name__ == "__main__":
    unittest.main()
