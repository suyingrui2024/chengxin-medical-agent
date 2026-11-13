"""紧急"延迟门"的解释（使用体验评测 R3-04）。

患者说疼到 8 分，系统判定 urgent 但为了把建议说具体而继续追问——这个决定
本身合理，问题是从头到尾没有一句话解释"为什么还问、现在到底该不该去"。
提示只应该出现一次，并且跟在问题之后。
"""

import unittest

from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation import ConsultationEngine
from medagent.core.domain import Assertion
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService
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
    state.record(_fact("consult_for", "self"))
    state.record(_fact("age_years", 35))
    state.record(_fact("chief_complaint", "肚子疼"))
    state.record(_fact("emergency_screen", None, Assertion.ABSENT))
    state.record(_fact("severity", 8))
    state.record(_fact("trajectory", "worsening"))
    state.risk = engine.triage.assess(state, "肚子疼得厉害，两天了")
    return engine, content, state


class DeferredUrgentNoticeTests(unittest.TestCase):
    def test_the_next_question_explains_why_it_keeps_asking(self):
        engine, content, state = build_engine()
        deferred = engine.planning._urgent_or_defer(state, "t1")
        self.assertIsNone(deferred, "单一线索应当先补最小事实，而不是立刻关闭")

        question = content.question("associated_symptoms")
        reply = engine._ask(state, question, "补最小事实")

        self.assertIn("不必等问完", reply.message)
        self.assertIn("今天就去线下看", reply.message)
        # 问题本身还在，提示不能盖掉要回答的内容。
        self.assertTrue(reply.message.startswith(question.prompt))

    def test_the_notice_appears_only_once(self):
        engine, content, state = build_engine()
        engine.planning._urgent_or_defer(state, "t1")
        engine._ask(state, content.question("associated_symptoms"), "补最小事实")
        second = engine._ask(state, content.question("onset"), "继续")

        self.assertNotIn("不必等问完", second.message)

    def test_no_notice_when_nothing_was_deferred(self):
        engine, content, state = build_engine()
        reply = engine._ask(state, content.question("onset"), "普通提问")
        self.assertNotIn("不必等问完", reply.message)


if __name__ == "__main__":
    unittest.main()
