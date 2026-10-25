"""Tests for medagent.text_match: negation scope, adjacent negations,
and the uncertainty-aware lookup.

Regression anchors are the disease-test round-1 EVIDENCE items:
T-06 (denial list), N-03 (不是…), S-09 (不敢…), N-05 / O-13
(uncertain mentions), G-06 / T-02 (denial-list support quotes).
"""

from medagent.text_match import (
    UNCERTAINTY_MARKERS,
    contains_unnegated,
    find_unnegated,
    find_unnegated_certain,
)


class TestDenialListScope:
    """'、' does not cut a negation's scope (T-06, G-06, T-02)."""

    def test_denied_list_items_all_negated(self):
        assert not contains_unnegated("没有气短、喘", "喘")
        assert not contains_unnegated("没有气短、喘", "气短")
        assert not contains_unnegated("没有呕吐、腹泻", "呕吐")
        assert not contains_unnegated("没有呕吐、腹泻", "腹泻")

    def test_denied_list_in_longer_sentence(self):
        text = "没有耳鸣、听力下降，也没有走路不稳、说话不清"
        assert not contains_unnegated(text, "说话不清")
        assert not contains_unnegated(text, "走路不稳")
        assert not contains_unnegated(text, "耳鸣")

    def test_unnegated_list_items_still_affirmed(self):
        assert contains_unnegated("头疼、发热", "发热")
        assert contains_unnegated("有点恶心、呕吐", "呕吐")

    def test_later_affirmed_occurrence_still_found(self):
        assert find_unnegated("没有气短、喘，后来喘起来了", "喘") > 0


class TestAdjacentNegation:
    """Negation immediately before the term (N-03, S-09)."""

    def test_bushi_denies(self):
        assert not contains_unnegated("不是天旋地转", "天旋地转")
        assert not contains_unnegated("不是天旋地转", "天旋")

    def test_bugan_is_avoidance_not_affirmation(self):
        assert not contains_unnegated("不敢咳嗽", "咳")
        assert not contains_unnegated("不敢咳嗽", "咳嗽")

    def test_bare_bu_denies(self):
        assert not contains_unnegated("不发热", "发热")
        assert not contains_unnegated("说话不费力", "费力")

    def test_bushi_with_intervening_hen_stays_affirmed(self):
        # "不是很疼" is a real pain: 很 intervenes, so 不是 is not
        # immediately before the term.
        assert contains_unnegated("不是很疼", "疼")

    def test_buduan_stays_affirmed(self):
        # "不断咳嗽": the window ends with 断, not 不.
        assert contains_unnegated("不断咳嗽", "咳嗽")
        assert contains_unnegated("不断咳嗽", "咳")

    def test_counterexamples_preserved(self):
        # 没 not immediately before the term: still affirmed.
        assert contains_unnegated("没睡好胸口疼", "胸口")
        # Clause break cuts the negation: still affirmed.
        assert contains_unnegated("感冒没好，又发热了", "发热")
        # 不是 scoped to the first clause only.
        assert contains_unnegated("我不是发热，是头疼", "头疼")
        assert not contains_unnegated("我不是发热，是头疼", "发热")


class TestUncertainty:
    """find_unnegated_certain (N-05, O-13, Y-07 style cases)."""

    def test_marker_after_term(self):
        assert find_unnegated_certain("走路不稳不清楚", "走路不稳") == -1
        assert find_unnegated_certain("发热没注意过", "发热") == -1

    def test_marker_before_term_same_clause(self):
        assert find_unnegated_certain("不清楚有没有发热", "发热") == -1
        assert find_unnegated_certain("没注意过发热", "发热") == -1
        assert find_unnegated_certain("不知道是不是咳嗽", "咳嗽") == -1
        assert find_unnegated_certain("接触新东西不清楚", "接触") == -1

    def test_marker_in_denial_sentence(self):
        text = "腿有点麻，大小便没有失禁，会阴发麻和发热没注意过"
        assert find_unnegated_certain(text, "发热") == -1

    def test_affirmed_mentions_are_certain(self):
        assert find_unnegated_certain("发热38度", "发热") == 0
        assert find_unnegated_certain("昨天发热，今天好多了", "发热") >= 0
        assert find_unnegated_certain("有点发热", "发热") >= 0

    def test_later_certain_occurrence_is_returned(self):
        text = "发热没注意过，后来又发热了"
        assert find_unnegated_certain(text, "发热") == text.rfind("发热")

    def test_negated_occurrences_still_skipped(self):
        assert find_unnegated_certain("没有发热", "发热") == -1
        text = "没有发热，后来发热了"
        assert find_unnegated_certain(text, "发热") == text.rfind("发热")

    def test_empty_inputs(self):
        assert find_unnegated_certain("", "发热") == -1
        assert find_unnegated_certain("发热", "") == -1

    def test_marker_inventory(self):
        assert UNCERTAINTY_MARKERS == (
            "不清楚",
            "不知道",
            "没注意",
            "没留意",
            "不确定",
            "说不清",
            "不记得",
        )


class TestContainsUnnegatedSemanticsUnchanged:
    """Uncertainty does NOT change contains_unnegated: it judges
    negation only. Callers that need certainty must opt into
    find_unnegated_certain explicitly."""

    def test_uncertain_mention_still_counts_as_unnegated(self):
        assert contains_unnegated("走路不稳不清楚", "走路不稳")
        assert contains_unnegated("发热没注意过", "发热")
