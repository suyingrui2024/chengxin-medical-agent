"""判断题的肯定识别（使用体验评测 R3-01）。

背景：旧实现用 ``raw.startswith(("有", "是", "对"))`` 判"是"，于是
"对了，帮我儿子也看一下" 会因为开头的"对"被记成"有危险信号"，直接
触发 120 并关闭会话。中文里"对了/对不起/对于"这类口头语极常见，
而判断题问的是"有没有"，句子开头撞上一个字不等于肯定。

这里的用例钉住三件事：真正的肯定仍然被认出来；口头语不再误判；
否认与不确定的语义没有被改动。
"""

import unittest

from medagent.clinical.interpreter import Interpreter
from medagent.clinical.knowledge import Question
from medagent.core.domain import Assertion, Phase, QuickReply


def yes_no_question() -> Question:
    """急症筛查题的形状：三个选项，free text 也可以回答。"""
    return Question(
        id="emergency_screen",
        phase=Phase.EMERGENCY_SCREEN,
        fact_key="emergency_screen",
        prompt="此刻有没有以下任一情况？",
        input="yes_no_unknown",
        options=[
            QuickReply("有任一项", "yes"),
            QuickReply("都没有", "no"),
            QuickReply("不确定", "unknown"),
        ],
        applies_when={},
    )


class AffirmativeAnswersTests(unittest.TestCase):
    def test_plain_yes_words_are_affirmative(self):
        for text in ("有", "是的", "对", "对的", "是啊", "确实有"):
            with self.subTest(text=text):
                result = Interpreter().interpret(yes_no_question(), text)
                self.assertEqual(result.assertion, Assertion.PRESENT)
                self.assertIs(result.value, True)

    def test_yes_with_a_qualifier_is_affirmative(self):
        for text in ("有这项", "有一项", "有这种情况", "出现了", "有点胸闷", "有些难受"):
            with self.subTest(text=text):
                result = Interpreter().interpret(yes_no_question(), text)
                self.assertEqual(result.assertion, Assertion.PRESENT)

    def test_yes_followed_by_a_comma_is_affirmative(self):
        result = Interpreter().interpret(yes_no_question(), "有，昨天开始的")
        self.assertEqual(result.assertion, Assertion.PRESENT)


class DiscourseMarkerTests(unittest.TestCase):
    """R3-01 的正例：这些句子都在说别的事，不能被读成"有"。"""

    def test_the_reviewers_sentence_no_longer_escalates(self):
        result = Interpreter().interpret(
            yes_no_question(), "对了，帮我儿子也看一下，他也咳嗽"
        )
        self.assertEqual(result.assertion, Assertion.UNKNOWN)

    def test_other_common_openers_are_not_affirmative(self):
        for text in ("对不起我想问下", "对于这个情况", "有时候会疼", "是不是要抽血"):
            with self.subTest(text=text):
                result = Interpreter().interpret(yes_no_question(), text)
                self.assertNotEqual(result.assertion, Assertion.PRESENT)


class UnchangedSemanticsTests(unittest.TestCase):
    def test_denials_stay_denials(self):
        for text in ("没有", "都没有", "无", "否认"):
            with self.subTest(text=text):
                result = Interpreter().interpret(yes_no_question(), text)
                self.assertEqual(result.assertion, Assertion.ABSENT)

    def test_unknown_stays_unknown(self):
        result = Interpreter().interpret(yes_no_question(), "不知道")
        self.assertEqual(result.assertion, Assertion.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
