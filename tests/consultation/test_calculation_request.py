"""计算请求不能被当成当前题的答案（使用体验评测 R3-06，2026-11-05）。

规则引擎（demo 后端）没有计算插件：BMI、单位换算这类公式只挂在自适应引擎
的工具上。所以患者说"帮我算一下 BMI，身高175，体重70"时，系统既算不出来，
也不该把这句当成主诉记下——那会让患者等一个数值，等到的却是下一个问题。
现在的行为是：说明这个版本不能算，并把原问题再问一次。
"""

import unittest

from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation import ConsultationEngine
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService
from medagent.shared_types import UserInput
from tests.support.reports_case import _fact


def build_engine():
    """An engine parked on the chief-complaint question with intake answered."""
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
    state.record(_fact("sex", "male"))
    state.record(_fact("age_years", 32))
    state.active_question = "chief_complaint"
    return engine, state


class CalculationRequestTests(unittest.TestCase):
    def test_the_request_is_not_booked_as_the_answer(self):
        engine, state = build_engine()
        reply = engine.submit(
            state.session_id,
            UserInput(text="帮我算一下 BMI，身高175，体重70"),
        )
        self.assertNotIn("chief_complaint", state.facts)
        self.assertEqual(state.active_question, "chief_complaint")
        self.assertEqual(reply.question_id, "chief_complaint")

    def test_the_reply_says_it_cannot_compute_and_returns_to_the_question(self):
        engine, state = build_engine()
        reply = engine.submit(
            state.session_id,
            UserInput(text="帮我算一下 BMI，身高175，体重70"),
        )
        self.assertEqual(reply.kind, "clarification")
        self.assertIn("还不能替你算", reply.message)
        self.assertIn("回到刚才的问题", reply.message)
        # 选项要带回去，否则患者只能重打一遍
        self.assertTrue(reply.quick_replies is not None)

    def test_the_words_are_kept_in_the_trace(self):
        engine, state = build_engine()
        engine.submit(
            state.session_id,
            UserInput(text="帮我算一下 BMI，身高175，体重70"),
        )
        payloads = [
            event.to_dict()["payload"]
            for event in engine.events.for_session(state.session_id)
            if event.to_dict()["event_type"] == "calculation.unavailable"
        ]
        self.assertEqual(len(payloads), 1)
        self.assertIn("身高175", payloads[0]["evidence"])

    def test_an_ordinary_answer_mentioning_a_number_still_answers(self):
        # "我算了一下大概38度" 是估出来的体温，不是计算请求——它要被正常收下。
        engine, state = build_engine()
        reply = engine.submit(state.session_id, UserInput(text="我算了一下大概38度"))
        self.assertIn("chief_complaint", state.facts)
        # 正常收下并进入下一题（安全筛查），而不是被计算守卫拦在原地。
        self.assertNotEqual(reply.kind, "clarification")
        self.assertNotEqual(state.active_question, "chief_complaint")


if __name__ == "__main__":
    unittest.main()
