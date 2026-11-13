"""摘要卡里的取值必须是患者语言（使用体验评测 R3-05）。

抽屉已用 displayFact 本地化，摘要卡走的是后端的 VALUE_LABELS；这张表缺
male/female 时，"性别"一行会直接显示 male。这里从摘要载荷本身钉住。
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
    state.record(_fact("sex", "male"))
    state.record(_fact("chief_complaint", "发烧"))
    state.record(_fact("emergency_screen", None, Assertion.ABSENT))
    return engine, state


class SummaryValueLabelTests(unittest.TestCase):
    def test_sex_renders_as_a_word_not_an_enum(self):
        engine, state = build_engine()
        payload = engine.replies._summary_payload(state, review_status="pending")
        values = {item["key"]: item["value"] for item in payload["known"]}
        self.assertEqual(values["sex"], "男")

    def test_labels_and_values_do_not_leak_keys(self):
        engine, state = build_engine()
        payload = engine.replies._summary_payload(state, review_status="pending")
        for item in payload["known"]:
            self.assertNotIn(item["value"], {"male", "female", "self", "child"})
            self.assertFalse(str(item["label"]).startswith("warning_"))


if __name__ == "__main__":
    unittest.main()
