"""Batch A scan layer (W1): uncertainty gating, chest-combo clarification,
confirmed-trigger helper, severe_breathing colloquial coverage, and MISS
combination probes.

Reproduction-first tests for R-01, R-03, G-08, C-01, C-04, F-12, R-05 and
the MISS review (N-07, O-09, P-13, Y-12, T-12). The MISS tests pin the
*current* scan result (no hit) on purpose: those combinations have no
deterministic scan signal today, and inventing one is a clinical decision,
not an engineering fix -- see the W1 report.
"""
from __future__ import annotations

import unittest

from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.red_flags import FlagHit, RedFlagScanner
from medagent.triage import TriageService, confirmed_trigger_labels


def flag_ids(text: str) -> set:
    return {hit.flag_id for hit in RedFlagScanner().scan(text)}


def _fact(key: str, assertion: Assertion, evidence: str) -> Fact:
    return Fact(
        key=key,
        value=None,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t",
        evidence=evidence,
    )


class UncertaintyGateTests(unittest.TestCase):
    def test_r01_hemoptysis_unnoticed_does_not_trigger(self):
        self.assertEqual(
            RedFlagScanner().scan("就是咳嗽，没有胸痛；有没有痰、咳血没注意。"),
            [],
        )

    def test_r03_chest_pain_unnoticed_does_not_trigger(self):
        self.assertEqual(
            RedFlagScanner().scan(
                "是有痰的咳嗽，没有咳血。胸痛和夜间加重没注意。"
                "另外，我这是不是肺炎？"
            ),
            [],
        )

    def test_g08_uncertain_major_bleeding_echo_does_not_trigger(self):
        self.assertEqual(
            RedFlagScanner().scan(
                "不确定算不算大出血，我就是拉黑便两次、"
                "吐了一次暗红色的东西，还头晕、心慌。"
            ),
            [],
        )

    def test_uncertainty_variants_do_not_trigger(self):
        for text in [
            "胸痛我没注意",
            "胸痛我没留意",
            "咳血不清楚",
            "咳血不知道有没有",
            "不清楚有没有咳血",
            "没留意有没有胸痛",
            "算不算大出血，我不太清楚",
        ]:
            with self.subTest(text=text):
                self.assertEqual(RedFlagScanner().scan(text), [])

    def test_affirmed_counterparts_still_trigger(self):
        cases = {
            "我咳血了，咳了好几口血。": "major_bleeding",
            "我大出血了，血止不住。": "major_bleeding",
            "持续胸痛十几分钟": "chest_pain",
            "吐了好多血，吐了两回了": "major_bleeding",
        }
        for text, flag_id in cases.items():
            with self.subTest(text=text):
                self.assertIn(flag_id, flag_ids(text))

    def test_uncertainty_about_cause_does_not_suppress_affirmed_symptom(self):
        # "不清楚是什么原因" scopes to the cause, in its own clause; the
        # affirmed 咳血 in the earlier clause must still trigger.
        self.assertIn(
            "major_bleeding",
            flag_ids("我咳血三天了，不清楚是什么原因"),
        )
        # Uncertainty about *why* a symptom occurs is not uncertainty
        # about whether it occurred.
        self.assertIn("major_bleeding", flag_ids("我不知道为什么会咳血"))

    def test_uncertain_first_match_does_not_hide_later_affirmation(self):
        self.assertIn(
            "major_bleeding",
            flag_ids("咳血没注意，后来咳了很多血"),
        )


class ChestComboClarificationTests(unittest.TestCase):
    def test_c01_combo_hit_requires_clarification(self):
        hits = RedFlagScanner().scan(
            "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心。大概3天前开始的。"
        )
        self.assertEqual([h.flag_id for h in hits], ["chest_pain"])
        self.assertEqual(hits[0].layer, "semantic_combo")
        self.assertTrue(hits[0].requires_clarification)

    def test_c04_bare_chest_tightness_requires_clarification(self):
        hits = RedFlagScanner().scan(
            "我快走一段路就胸闷，停下来歇会儿就好。开始大概近一个月了。"
        )
        self.assertEqual([h.flag_id for h in hits], ["chest_pain"])
        self.assertTrue(hits[0].requires_clarification)

    def test_explicit_phrase_hit_does_not_require_clarification(self):
        hits = RedFlagScanner().scan("持续胸痛十几分钟")
        self.assertEqual(hits[0].layer, "rule_phrase")
        self.assertFalse(hits[0].requires_clarification)

    def test_scan_text_and_assess_still_include_combo_labels(self):
        # Deliberate compatibility contract for this batch: scan_text /
        # assess keep escalating on combo hits so the red-flag corpus and
        # release gate stay green. The controller worker gates on
        # scan_detailed + requires_clarification before the emergency path.
        service = TriageService()
        self.assertIn("高风险胸痛描述", service.scan_text("我胸闷"))
        hits = service.scan_detailed("我胸闷")
        self.assertTrue(hits[0].requires_clarification)


class ChestComboDiscriminatorTests(unittest.TestCase):
    """Narrowing of the W1 blanket clarification rule (coordinator ruling).

    Discriminating rule (see red_flags._combo_hit): a semantic_combo hit
    stays deterministic (requires_clarification=False) only when the
    sentence containing the hit carries an explicit affirmed
    discriminating feature beyond the bare location x signal pair --
    diaphoresis (出汗/冒汗/冷汗/大汗), radiation to a named site
    (往左胳膊窜 / 放射 + target), a pressing/constricting quality
    phrase (像压/压榨/压迫/勒), a severe-intensity statement
    (疼得厉害/痛得厉害/剧烈), or a compound symptom term made of two
    adjacent signal tokens (闷痛/疼痛). A merely recurrent wording
    (一阵阵), exertion/relief, or a duration statement is NOT a
    discriminator, so bare co-occurrence still requires clarification.
    No new clinical threshold is introduced: this restores the
    pre-batch-A deterministic behaviour for explicitly affirmed
    statements and keeps the clarification layer for bare co-occurrence.
    """

    def test_a_discriminated_statements_stay_deterministic(self):
        # The 5 dual-engine safety sentences that W1 wrongly routed to
        # clarification ('clarification' != 'emergency').
        for text in [
            "胸口像压了块石头一样疼，还出冷汗",
            "心口窝疼得厉害，一阵一阵冒汗",
            "前胸疼，还往左胳膊窜",
            "我老公说胸口发紧，像有绳子勒着",
            "左胸那边一阵一阵地闷痛",
        ]:
            with self.subTest(text=text):
                hits = RedFlagScanner().scan(text)
                self.assertEqual([h.flag_id for h in hits], ["chest_pain"])
                self.assertEqual(hits[0].layer, "semantic_combo")
                self.assertFalse(hits[0].requires_clarification)

    def test_b_bare_cooccurrence_still_requires_clarification(self):
        for text in [
            "胸闷",
            # C-01 full original form: location + generic pain word,
            # plus tenderness/reflux context, no discriminator.
            "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心。大概3天前开始的。",
            # C-04 full original form: bare chest tightness, exertion
            # and relief and a duration statement are not discriminators.
            "我快走一段路就胸闷，停下来歇会儿就好。开始大概近一个月了。",
        ]:
            with self.subTest(text=text):
                hits = RedFlagScanner().scan(text)
                self.assertEqual([h.flag_id for h in hits], ["chest_pain"])
                self.assertEqual(hits[0].layer, "semantic_combo")
                self.assertTrue(hits[0].requires_clarification)

    def test_recurrence_wording_alone_is_not_a_discriminator(self):
        # 一阵阵 on its own does not flip the hit: C-01 stays
        # clarification even though it is recurrent, and the A5 sentence
        # is deterministic because of its compound symptom term 闷痛,
        # not because of its recurrence wording.
        hits = RedFlagScanner().scan("我胸口一阵阵刺痛")
        self.assertEqual(hits[0].layer, "semantic_combo")
        self.assertTrue(hits[0].requires_clarification)

    def test_compound_symptom_term_alone_is_deterministic(self):
        hits = RedFlagScanner().scan("胸口闷痛")
        self.assertEqual(hits[0].layer, "semantic_combo")
        self.assertFalse(hits[0].requires_clarification)

    def test_negated_discriminator_does_not_flip(self):
        # A discriminator that is itself denied/uncertain must not
        # restore the deterministic path.
        hits = RedFlagScanner().scan("胸口疼，没有出汗")
        self.assertEqual(hits[0].layer, "semantic_combo")
        self.assertTrue(hits[0].requires_clarification)


class ConfirmedTriggerHelperTests(unittest.TestCase):
    F12_TEXT = (
        "出血是真的很多，突然就出好多血。其他几项没有，没有叫不醒，"
        "也没有抽搐、嘴唇发紫、严重呼吸困难和胸痛。"
    )

    def test_f12_denied_items_never_listed_as_triggers(self):
        # Scanner layer: the 也没有-led enumeration denial must not
        # produce the denied trio in the first place.
        self.assertNotIn("cyanosis", flag_ids(self.F12_TEXT))
        self.assertNotIn("severe_breathing", flag_ids(self.F12_TEXT))
        self.assertNotIn("chest_pain", flag_ids(self.F12_TEXT))

    def test_helper_drops_denied_and_clarification_hits(self):
        hits = [
            FlagHit("cyanosis", "口唇发紫", "rule_phrase", "嘴唇发紫"),
            FlagHit("severe_breathing", "严重呼吸困难", "rule_phrase", "严重呼吸困难"),
            FlagHit(
                "chest_pain", "高风险胸痛描述", "semantic_combo", "胸痛",
                requires_clarification=True,
            ),
            FlagHit("major_bleeding", "大量或无法控制的出血", "rule_phrase", "大出血"),
        ]
        facts = {
            "emergency_screen": _fact(
                "emergency_screen", Assertion.ABSENT, self.F12_TEXT
            ),
            "bleeding_note": _fact(
                "bleeding_note", Assertion.PRESENT, "出血是真的很多"
            ),
        }
        labels = confirmed_trigger_labels(hits, facts)
        self.assertEqual(labels, ["大量或无法控制的出血"])

    def test_helper_keeps_clarification_hit_only_when_explicitly_allowed(self):
        hits = [
            FlagHit(
                "chest_pain", "高风险胸痛描述", "semantic_combo", "胸闷",
                requires_clarification=True,
            ),
        ]
        self.assertEqual(confirmed_trigger_labels(hits, {}), [])
        self.assertEqual(
            confirmed_trigger_labels(hits, {}, include_clarification=True),
            ["高风险胸痛描述"],
        )

    def test_helper_accepts_case_state_and_does_not_whitelist(self):
        # A hit with no denial record on file passes through: the helper
        # is a denial filter, not an affirmation whitelist.
        state = CaseState(session_id="t")
        state.facts["note"] = _fact("note", Assertion.PRESENT, "患者说胸闷")
        hits = [FlagHit("seizure", "抽搐", "semantic_pattern", "正在抽搐")]
        self.assertEqual(
            confirmed_trigger_labels(hits, state), ["抽搐"]
        )
        # The service exposes the same helper.
        self.assertEqual(
            TriageService().confirmed_trigger_labels(hits, state), ["抽搐"]
        )


class SevereBreathingColloquialTests(unittest.TestCase):
    def test_r05_speech_interruption_variants_covered(self):
        for text in [
            "我喘得说不出整句话",
            "喘得说不出一句完整的话",
            "喘得一句话都说不完",
            "喘得说不了话",
        ]:
            with self.subTest(text=text):
                self.assertIn("severe_breathing", flag_ids(text))

    def test_r05_negated_speech_variant_still_suppressed(self):
        self.assertEqual(RedFlagScanner().scan("没有喘得说不出话"), [])

    def test_r05_evaluated_but_not_added(self):
        # Same-signal test was inconclusive / too broad for a
        # deterministic threshold, so these stay unadded (see W1 report
        # evaluation table): vaguer exertion or stuffiness phrasings.
        for text in [
            "说句话就喘",
            "憋得慌",
            "上气不接下气",
            "一动就喘得不行",
            "呼吸跟不上",
        ]:
            with self.subTest(text=text):
                self.assertNotIn("severe_breathing", flag_ids(text))


class MissCombinationProbeTests(unittest.TestCase):
    """No deterministic scan signal exists for these combinations today.
    These tests pin that fact so a future clinical decision to add a
    signal is explicit, not a silent scan change."""

    def test_n07_sudden_unilateral_weakness_plus_slurred_speech(self):
        self.assertEqual(
            RedFlagScanner().scan("我突然一边胳膊没劲，说话也不清楚，还头晕。"),
            [],
        )

    def test_o09_bowel_bladder_dysfunction_plus_saddle_numbness(self):
        self.assertEqual(
            RedFlagScanner().scan(
                "我腰疼还大小便费劲了，屁股那块发麻。"
                "小便解不干净，大便费劲，屁股那块发麻"
            ),
            [],
        )

    def test_p13_infant_dehydration_combination(self):
        self.assertEqual(
            RedFlagScanner().scan(
                "我家宝宝拉肚子一天了，嘴干，尿很少，精神蔫蔫的。"
                "拉的是水样便，多次；没有发烧，没有血便；喝水比平时少。"
            ),
            [],
        )

    def test_y12_sudden_flash_shadow_vision_loss(self):
        self.assertEqual(
            RedFlagScanner().scan("我眼前突然闪光，还感觉一块黑影挡着视野。"),
            [],
        )

    def test_t12_sudden_unilateral_hearing_loss(self):
        self.assertEqual(
            RedFlagScanner().scan("我一只耳朵突然听不见了，还嗡嗡响。"),
            [],
        )


if __name__ == "__main__":
    unittest.main()
