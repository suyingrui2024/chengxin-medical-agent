"""任务卡在屏时口头"取消"（使用体验评测 R3-08）。

患者说"取消定位"曾被当成"用文字描述部位"而拒绝：任务还在屏上，人却被迫
再点一次按钮。取消与跳过是同一件事——不记录结果、回到原来的问题——所以
应当按跳过处理。
"""

import unittest

from medagent.clinical.knowledge import ContentRepository
from medagent.clinical.reasoner import OrderedQuestionRanker
from medagent.clinical.triage import TriageService
from medagent.consultation import ConsultationEngine
from medagent.core.domain import Assertion, ObservationQuality, Phase
from medagent.core.events import EventStore
from medagent.guards.self_checks import SelfCheckService


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
    state.active_task = "body_location"
    state.phase = Phase.OBSERVATION
    return engine, state


class TaskCancelTextTests(unittest.TestCase):
    def test_cancel_words_end_the_task_instead_of_rejecting_the_text(self):
        engine, state = build_engine()
        failure = engine._try_task_result(state, "取消定位", None, "t1")

        self.assertIsNone(failure, "取消不该以错误回复的形式退回")
        location = state.facts.get("symptom_location")
        self.assertIsNotNone(location)
        self.assertEqual(location.assertion, Assertion.UNKNOWN)
        self.assertEqual(location.quality, ObservationQuality.NOT_DONE)
        self.assertIsNone(state.active_task)

    def test_a_location_word_still_gets_the_pick_it_on_the_map_message(self):
        engine, state = build_engine()
        failure = engine._try_task_result(state, "头/面部", None, "t2")

        self.assertIsNotNone(failure, "用文字说部位仍应被拒绝，避免绕过图示")
        self.assertIn("图示", failure.message)
        self.assertEqual(state.active_task, "body_location")


if __name__ == "__main__":
    unittest.main()
