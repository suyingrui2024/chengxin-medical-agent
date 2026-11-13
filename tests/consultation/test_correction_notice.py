"""改主诉后的撤回提示（使用体验评测 R3-03）。

改主诉会让一批旧答案失效（开始时间、趋势、程度、急症筛查……）。撤回本身
是对的，但曾经完全静默：界面只剩一句 4.5 秒的"摘要已重新整理"，患者不
知道自己刚答过的内容去哪了。这条用例钉住：撤回必须跟着这一轮回复说出来，
并且用患者看得懂的字段名。
"""

import unittest

from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation import ConsultationEngine
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService
from medagent.core.domain import Assertion
from tests.support.reports_case import _fact


def build_engine():
    content = ContentRepository()
    engine = ConsultationEngine(
        content=content,
        events=EventStore(),
        triage=TriageService(),
        self_checks=SelfCheckService(content.registry),
        ranker=OrderedQuestionRanker(),
    )
    state, _opening = engine.create_session()
    return engine, state


class CorrectionNoticeTests(unittest.TestCase):
    def test_changing_the_chief_complaint_names_what_needs_redoing(self):
        engine, state = build_engine()
        state.record(_fact("consult_for", "self"))
        state.record(_fact("chief_complaint", "发烧"))
        state.record(_fact("emergency_screen", None, Assertion.ABSENT))
        state.record(_fact("onset", "两天"))
        state.record(_fact("trajectory", "stable"))
        state.record(_fact("severity", 3))

        reply = engine.correct_fact(state.session_id, "chief_complaint", "头痛")

        self.assertIn("不再算作已确认", reply.message)
        for label in ("开始时间", "变化趋势", "影响程度", "紧急信号筛查"):
            with self.subTest(label=label):
                self.assertIn(label, reply.message)
        # 提示是回复正文的一部分，落在对话里，不是一闪而过的 toast；
        # 位置在问题之后，患者先看到要答什么，再看到为什么重问。
        self.assertTrue(reply.message.endswith("）"))
        self.assertEqual(reply.kind, "question")

    def test_correcting_another_field_stays_quiet(self):
        engine, state = build_engine()
        state.record(_fact("consult_for", "self"))
        state.record(_fact("chief_complaint", "发烧"))
        state.record(_fact("age_years", 32))

        reply = engine.correct_fact(state.session_id, "age_years", "40岁")

        self.assertNotIn("不再算作已确认", reply.message)

    def test_notice_skips_labels_it_does_not_know(self):
        engine, _state = build_engine()
        notice = engine.subjects._correction_notice({"onset", "some_new_field"})
        self.assertIn("开始时间", notice)
        self.assertNotIn("some_new_field", notice)


if __name__ == "__main__":
    unittest.main()
