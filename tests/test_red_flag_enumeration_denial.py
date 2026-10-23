"""Regression tests: trailing enumeration denials (live-eval case6).

Chinese symptom lists put the negation *after* the items — “发烧、腰痛、
呕吐、尿血这些都没有” — and the 、-separated items must not be stitched
together by pattern gaps into a fake finding （呕吐+尿血 → “呕血”). These
tests pin both directions: enumerated denials suppress hits in all three
layers (phrase / pattern / combo), while real positives — including ones
where a denial-looking tail carries real content （咳血，没有停） — keep
firing, because red-flag recall is a hard release constraint.
"""
from __future__ import annotations

import unittest

from medagent.red_flags import RedFlagScanner, span_trailing_denied
from medagent.triage import TriageService

CASE6_TEXT = (
    "还有尿急，老是憋不住的感觉，每次去又尿不多，尿的颜色有点黄。"
    "发烧、腰痛、呕吐、尿血这些都没有。"
)


def flag_ids(text: str) -> set:
    return {hit.flag_id for hit in RedFlagScanner().scan(text)}


class EnumerationDenialTests(unittest.TestCase):
    def test_case6_text_produces_no_emergency_hit(self):
        self.assertEqual(RedFlagScanner().scan(CASE6_TEXT), [])
        self.assertEqual(TriageService().scan_text(CASE6_TEXT), [])

    def test_enumerated_bleeding_denial_is_suppressed(self):
        self.assertNotIn(
            "major_bleeding", flag_ids("发烧、腰痛、呕吐、尿血这些都没有")
        )

    def test_pattern_gap_cannot_stitch_across_enumeration_comma(self):
        # Without the 、 exclusion the gap in (吐|呕|咳|咯)...血 stitched
        # 呕吐、尿血 into a fake “呕血” even outside a denial context.
        self.assertNotIn("major_bleeding", flag_ids("呕吐、尿血，先观察一下"))

    def test_combo_hit_inside_enumerated_denial_is_suppressed(self):
        self.assertNotIn("chest_pain", flag_ids("胸闷、气短这些都没有"))
        self.assertNotIn("chest_pain", flag_ids("发烧、咳嗽、胸痛以上都没有"))

    def test_phrase_hit_inside_enumerated_denial_is_suppressed(self):
        self.assertNotIn("major_bleeding", flag_ids("发烧、大出血这些都没有"))
        self.assertNotIn("major_bleeding", flag_ids("头晕、恶心、呕血都没有"))


class RangeWordScopeTests(unittest.TestCase):
    """Defect 1: a trailing item led by a range word （其他/别的/其余/
    其它） scopes the denial to symptoms *other than* the affirmed ones,
    so it must not suppress a hit stated before it."""

    def test_affirmed_bleeding_survives_range_word_tail(self):
        for text in (
            "吐血，其他症状都没有。",
            "我吐血，其他的都没有。",
            "咳血，其余都没有。",
            "吐血，其它都没有。",
        ):
            self.assertIn("major_bleeding", flag_ids(text), text)

    def test_affirmed_bleeding_phrase_survives_range_word_tail(self):
        self.assertIn("major_bleeding", flag_ids("大出血，其他都没有"))

    def test_affirmed_combo_survives_range_word_tail(self):
        self.assertIn("chest_pain", flag_ids("胸口疼，其他症状都没有"))

    def test_bleeding_with_severity_context_survives_range_word_tail(self):
        # 便血 only reaches major_bleeding with a severity context (the
        # curated pattern requires 全是/大量/…); with that hit present, a
        # range-word tail must not suppress it either.
        self.assertIn(
            "major_bleeding", flag_ids("便血，便池里全是血，别的症状都没有")
        )

    def test_bare_bianxue_tail_is_not_a_suppression_case(self):
        # Bare 便血/尿血 without severity context has no major_bleeding
        # candidate hit at all (by definition design — cf. case6's bare
        # 尿血）, so there is nothing for the denial to suppress. Pin the
        # helper: the trailing denial does not apply to this sentence.
        self.assertFalse(span_trailing_denied("便血，别的都没有。", 2))

    def test_range_word_does_not_rescue_genuine_enumerated_denials(self):
        # No range word anywhere: plain enumerated denials still suppress.
        self.assertNotIn(
            "major_bleeding", flag_ids("发烧、腰痛、呕吐、尿血这些都没有")
        )
        self.assertNotIn("chest_pain", flag_ids("胸闷、气短这些都没有"))


class ExemptPatternTrailingDenialTests(unittest.TestCase):
    """Defect 2: negation-exempt patterns (negation inside the match is
    the symptom itself) must still yield to a trailing enumeration
    denial that denies the match from outside."""

    def test_enumerated_denial_suppresses_exempt_pattern(self):
        text = "叫不醒、抽搐、胸痛、大出血这些都没有。"
        self.assertNotIn("unresponsive", flag_ids(text))
        self.assertEqual(flag_ids(text), set())

    def test_exempt_pattern_with_content_tail_still_fires(self):
        # The new trailing check must not over-suppress: a tail that is
        # not pure denial scaffolding leaves exempt hits alone.
        self.assertIn("unresponsive", flag_ids("叫不醒，赶紧打120"))
        self.assertIn("unresponsive", flag_ids("他喊了半天没有反应，家里人急坏了"))

    def test_negation_as_symptom_sentences_still_fire(self):
        # The exempt patterns' raison d'être: the match runs to the end
        # of the sentence, so no trailing denial can follow it.
        self.assertIn("unresponsive", flag_ids("反应都没有"))
        self.assertIn("unresponsive", flag_ids("喊了半天没有反应"))


class TruePositivePreservationTests(unittest.TestCase):
    def test_bleeding_positives_still_fire(self):
        for text in (
            "呕血，量很多",
            "吐血止不住",
            "尿血，全是血，很多",
            "呕血",
            "大出血",
        ):
            self.assertIn("major_bleeding", flag_ids(text), text)

    def test_denial_lookalike_tails_with_content_still_fire(self):
        # The trailing text is not pure denial scaffolding: 没有停 /
        # 没有止住 / 都没有停下来 describe an *ongoing* bleed.
        for text in ("咳血，没有停", "呕血没有止住", "呕血、咳血都没有停下来"):
            self.assertIn("major_bleeding", flag_ids(text), text)

    def test_affirmed_item_after_leading_denial_still_fires(self):
        # The denial comes first and scopes itself; the affirmed chest
        # pain after 只有 must still hit.
        self.assertIn("chest_pain", flag_ids("都没有，只有胸口疼"))

    def test_completed_bleeding_with_range_word_tail_still_fires(self):
        # 了 right after 吐血 already defeated the scaffold match before
        # the range-word rule; pin that it keeps firing.
        self.assertIn("major_bleeding", flag_ids("吐血了，别的症状都没有"))

    def test_enumeration_neighbour_with_negation_word_does_not_poison(self):
        # 无力 contains 无 but lives in its own 、-item; the affirmed
        # 呕血 next to it must survive (clause split on 、).
        self.assertIn("major_bleeding", flag_ids("他浑身无力、后来开始呕血"))


class ExistingNegationBehaviourTests(unittest.TestCase):
    def test_in_clause_negation_still_suppresses(self):
        self.assertEqual(flag_ids("嘴唇没有发紫"), set())
        self.assertEqual(flag_ids("没有大出血，就是擦破点皮"), set())
        self.assertEqual(flag_ids("胸口不疼"), set())

    def test_negation_as_symptom_patterns_still_fire(self):
        self.assertIn("unresponsive", flag_ids("反应都没有"))
        self.assertIn("unresponsive", flag_ids("他喊了半天没有反应"))
        self.assertIn("unresponsive", flag_ids("叫不醒"))


class TrailingDeniedHelperTests(unittest.TestCase):
    def test_pure_scaffolds(self):
        for rest in ("这些都没有", "都没有", "以上都没有了", "、气短这些都没有"):
            text = "X" + rest
            self.assertTrue(span_trailing_denied(text, 1), rest)

    def test_content_tails_are_not_scaffolds(self):
        for rest in (
            "，量很多",
            "没有停",
            "没有止住",
            "都没有停下来",
            "",
            "，其他症状都没有",
            "，别的都没有",
            "，其余都没有",
        ):
            text = "X" + rest
            self.assertFalse(span_trailing_denied(text, 1), rest)

    def test_denial_in_next_sentence_does_not_reach_back(self):
        text = "呕血。其他症状这些都没有"
        end = text.index("。")
        self.assertFalse(span_trailing_denied(text, end))


if __name__ == "__main__":
    unittest.main()
