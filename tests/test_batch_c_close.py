"""Batch C CLOSE-COPY worker: emergency/urgent close copy.

One test per assigned issue (17): R-05, R-06, C-05, E-07, U-06, U-12,
U-13, U-15, U-16, F-03, F-10, F-11, P-12, K-13, K-15, J-04, O-14.

Each test is a shape re-test of the close text, not a clinical
judgement: the close must carry (1) a soothing acknowledgement
sentence, (2) a restatement of PRESENT facts in the patient's own
words (batch-A ``_confirmed_facts_summary``, evidence preferred,
never only a mapped signal name), and (3) a destination answer keyed
only to the disposition level (EMERGENCY -> call 120 now;
urgent_today -> go to emergency assessment today). A close that
promises collected information must actually deliver it in the same
visible reply; with no facts it must not promise it at all (the
batch-A rule for ``_urgent`` is preserved, not restored).

Hard constraints honoured here and in the implementation:
- the 120 instruction and waiting guidance are never weakened or
  trimmed;
- no department field is read or rendered: urgent closes say
  "去急诊" as level wording, never the named department; the named
  department part of the items stays residual until the C12 sign-off
  (asserted below as "急诊科" absent from urgent closes);
- the dental-emergency routing wording "口腔急诊/急诊科" (K-15) is
  keyed to the patient's own tooth/oral words, never to a department
  field;
- window wording is not invented on this side: ``_close_window_text``
  calls conclusion.py's ``format_window_text`` when it exists and
  otherwise falls back to the urgent_today level wording ("今天内");
  the monkeypatch tests below prove the call path.
"""

import unittest

import medagent.conclusion as conclusion_module
from medagent.consultation import ConsultationEngine
from medagent.domain import (
    AgentReply,
    Phase,
    RiskAssessment,
    RiskLevel,
)
from tests.test_agent import fact
from tests.test_agent_wave2_engine import make_engine, record


SOOTHING = "我听到你说的这些了，先别慌，按下面的步骤来就可以。"
WAITING = ("保持安静休息", "不要自行开车", "有家人陪同更好")


def _emergency_case(engine, state, turn, chief_evidence, triggers):
    record(
        engine,
        state,
        turn,
        [
            fact("consult_for", "self", "我自己"),
            fact("chief_complaint", chief_evidence, chief_evidence),
        ],
    )
    return engine._emergency_reply(state, triggers)


def _urgent_case(engine, state, turn, facts, trigger):
    record(engine, state, turn, facts)
    state.risk = RiskAssessment(RiskLevel.URGENT, [trigger])
    return engine._urgent(state)


def _urgent_facts(chief, associated, goal="想知道要不要马上去急诊"):
    return [
        fact("consult_for", "self", "我自己"),
        fact("chief_complaint", chief, chief),
        fact("emergency_screen", None, "没有危险信号", "absent"),
        fact("trajectory", "worsening", "发得勤了"),
        fact("severity", 6, "6分"),
        fact("associated_symptoms", associated, associated),
        fact("patient_goal", goal, goal),
    ]


class EmergencyCloseTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def _assert_emergency_shape(self, reply, *original_words):
        self.assertEqual(reply.kind, "emergency")
        self.assertIn(SOOTHING, reply.message)
        self.assertIn("请现在拨打 120", reply.message)
        for phrase in WAITING:
            self.assertIn(phrase, reply.message)
        self.assertTrue(reply.message.endswith("不要继续等待本次对话。"))
        for word in original_words:
            self.assertIn(word, reply.message)

    def test_R05_asthma_emergency_has_soothing_and_original_words(self):
        # R-05: pure-instruction emergency close, anxiety 4/5.
        reply = _emergency_case(
            self.engine,
            self.state,
            "我现在喘得说不出整句话，吸入药用了也不缓解。",
            "喘得说不出整句话，吸入药用了也不缓解",
            ["患者确认急症筛查项"],
        )
        self._assert_emergency_shape(
            reply, "喘得说不出整句话", "吸入药用了也不缓解"
        )

    def test_R06_lung_infection_emergency_prefers_patient_words(self):
        # R-06: must quote "呼吸费劲、胸闷", not only the mapped
        # signal name "严重呼吸困难".
        reply = _emergency_case(
            self.engine,
            self.state,
            "我自己咨询，我高烧四天，咳黄痰，呼吸费劲、胸闷。",
            "高烧四天，咳黄痰，呼吸费劲、胸闷",
            ["严重呼吸困难", "高风险胸痛描述"],
        )
        self._assert_emergency_shape(reply, "呼吸费劲", "胸闷")

    def test_C05_heart_failure_emergency_prefers_patient_words(self):
        # C-05: must quote "喘不上气", not only "严重呼吸困难".
        reply = _emergency_case(
            self.engine,
            self.state,
            "我自己咨询，我平躺就喘不上气，坐起来才好点，脚也肿了。",
            "平躺就喘不上气，坐起来才好点，脚也肿了",
            ["严重呼吸困难"],
        )
        self._assert_emergency_shape(reply, "喘不上气", "脚也肿了")

    def test_J04_panic_emergency_restates_facts_not_only_signal(self):
        # J-04: must restate "心跳特别快、手脚发麻", not only the
        # mapped signal "严重呼吸困难".
        reply = _emergency_case(
            self.engine,
            self.state,
            "我自己咨询，我突然心跳特别快、喘不上气、手脚发麻，觉得自己要死了。",
            "心跳特别快、喘不上气、手脚发麻",
            ["严重呼吸困难"],
        )
        self._assert_emergency_shape(reply, "心跳特别快", "手脚发麻")

    def test_K15_dental_trauma_emergency_restates_and_routes(self):
        # K-15: must restate the tooth/bleeding facts and give the
        # item's routing wording (口腔急诊/急诊科), keyed to the
        # patient's tooth words, with the 120 instruction intact.
        reply = _emergency_case(
            self.engine,
            self.state,
            "我自己咨询，我摔倒把牙磕掉了，满嘴是血，牙还松动。",
            "摔倒把牙磕掉了，满嘴是血，牙还松动",
            ["患者确认急症筛查项"],
        )
        self._assert_emergency_shape(
            reply, "牙磕掉", "满嘴是血", "牙还松动"
        )
        self.assertIn("口腔急诊/急诊科", reply.message)


class UrgentCloseTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def _assert_urgent_shape(self, reply, *original_words):
        self.assertEqual(reply.kind, "urgent")
        self.assertIn(SOOTHING, reply.message)
        # Direct destination answer by disposition level only:
        # the question itself, a "yes", today, the emergency
        # destination, and an in-person assessment action. The exact
        # join differs between the fallback wording ("今天内去急诊")
        # and the conclusion helper's window sentence (specialised
        # to "今天内到急诊"), so assert components, not one brittle
        # contiguous string.
        self.assertIn("要不要马上去急诊", reply.message)
        self.assertIn("今天内", reply.message)
        self.assertIn("急诊", reply.message)
        self.assertIn("当面评估", reply.message)
        self.assertIn("建议今天联系医疗机构", reply.message)
        # C12 residual: the named department is NOT rendered here.
        self.assertNotIn("急诊科", reply.message)
        self.assertNotIn("department", reply.message)
        for word in original_words:
            self.assertIn(word, reply.message)

    def _assert_facts_delivered(self, reply, *words):
        # Promise-delivery consistency: "已采集的信息：" is only
        # ever followed by the facts themselves in the same reply.
        self.assertIn("已采集的信息：", reply.message)
        self.assertNotIn("下面是已采集的信息", reply.message)
        tail = reply.message.split("已采集的信息：", 1)[1]
        for word in words:
            self.assertIn(word, tail)

    def test_U06_stone_urgent_has_soothing(self):
        # U-06: pure-instruction urgent close, anxiety 4/5.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己腰疼，没有危险信号，发得勤了，6分，腰痛，想知道要不要马上去急诊",
            _urgent_facts("腰疼", "腰痛"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "腰疼")
        self._assert_facts_delivered(reply, "腰疼")

    def test_F03_urgent_has_soothing(self):
        # F-03: pure-instruction urgent close, anxiety 4/5.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己月经量多，没有危险信号，发得勤了，6分，头晕，想知道要不要马上去急诊",
            _urgent_facts("月经量多", "头晕"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "月经量多")

    def test_F10_urgent_has_soothing(self):
        # F-10: pure-instruction urgent close, anxiety 4/5, empathy 2/5.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己下腹痛，没有危险信号，发得勤了，6分，阴道出血，想知道要不要马上去急诊",
            _urgent_facts("下腹痛", "阴道出血"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "下腹痛")

    def test_P12_paediatric_urgent_has_soothing(self):
        # P-12: urgent close with no soothing for the parent's anxiety.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己孩子高热，没有危险信号，发得勤了，6分，抽搐，想知道要不要马上去急诊",
            _urgent_facts("孩子高热", "抽搐"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "孩子高热")

    def test_O14_back_urgent_answers_destination_directly(self):
        # O-14: goal "要不要马上去急诊" got no direct answer.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己腰背剧痛，没有危险信号，发得勤了，6分，腿麻，想知道要不要马上去急诊",
            _urgent_facts("腰背剧痛", "腿麻"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "腰背剧痛")

    def test_U12_pyelonephritis_urgent_answers_destination_directly(self):
        # U-12: goal "要不要马上去医院" got no direct answer.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己腰痛发热，没有危险信号，发得勤了，6分，尿痛，想知道要不要马上去医院",
            _urgent_facts(
                "腰痛发热", "尿痛", goal="想知道要不要马上去医院"
            ),
            "症状持续加重",
        )
        self._assert_urgent_shape(reply, "腰痛发热")

    def test_U16_retention_urgent_answers_destination_directly(self):
        # U-16: goal "要不要马上去急诊" got no direct answer.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己尿不出来，没有危险信号，发得勤了，6分，下腹胀，想知道要不要马上去急诊",
            _urgent_facts("尿不出来", "下腹胀"),
            "症状持续加重",
        )
        self._assert_urgent_shape(reply, "尿不出来")

    def test_K13_orofacial_urgent_answers_destination_directly(self):
        # K-13: urgent close never even contained "急诊", and the
        # goal question itself was skipped before closing. The level
        # direct answer is unconditional, so the skipped-goal case
        # still gets it; the named department stays residual (C12).
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己面部肿痛，没有危险信号，发得勤了，6分，张口困难，想知道要不要马上去急诊",
            _urgent_facts("面部肿痛", "张口困难"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "面部肿痛")
        self.assertIn("急诊", reply.message)

    def test_E07_urgent_delivers_promised_information(self):
        # E-07: "下面是已采集的信息" promised but nothing followed.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己体重增加，没有危险信号，发得勤了，6分，乏力，想知道要不要马上去急诊",
            _urgent_facts("体重增加", "乏力"),
            "症状持续加重",
        )
        self._assert_urgent_shape(reply, "体重增加")
        self._assert_facts_delivered(reply, "体重增加", "乏力")

    def test_U13_urgent_delivers_promised_information(self):
        # U-13: soothing missing AND promised information undelivered.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己腰痛发热，没有危险信号，发得勤了，6分，尿痛，想知道要不要马上去急诊",
            _urgent_facts("腰痛发热", "尿痛"),
            "症状持续加重",
        )
        self._assert_urgent_shape(reply, "腰痛发热")
        self._assert_facts_delivered(reply, "腰痛发热", "尿痛")

    def test_U15_urgent_delivers_promised_information(self):
        # U-15: same promise-delivery failure as U-13, after 1 round.
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己尿不出来，没有危险信号，发得勤了，6分，下腹胀，想知道要不要马上去急诊",
            _urgent_facts("尿不出来", "下腹胀"),
            "症状持续加重",
        )
        self._assert_urgent_shape(reply, "尿不出来")
        self._assert_facts_delivered(reply, "尿不出来", "下腹胀")

    def test_F11_urgent_delivers_promised_information_and_destination(self):
        # F-11: destination missing AND promised information
        # undelivered (goal question also skipped before closing).
        reply = _urgent_case(
            self.engine,
            self.state,
            "我自己下腹痛，没有危险信号，发得勤了，6分，阴道出血，想知道要不要马上去急诊",
            _urgent_facts("下腹痛", "阴道出血"),
            "症状影响评分较高",
        )
        self._assert_urgent_shape(reply, "下腹痛")
        self._assert_facts_delivered(reply, "下腹痛", "阴道出血")

    def test_urgent_without_facts_makes_no_information_promise(self):
        # Batch-A rule preserved by batch C: with no facts at all,
        # the close must not promise collected information.
        self.state.risk = RiskAssessment(RiskLevel.URGENT, ["症状持续加重"])
        reply = self.engine._urgent(self.state)
        self.assertEqual(reply.kind, "urgent")
        self.assertIn(SOOTHING, reply.message)
        self.assertIn("要不要马上去急诊", reply.message)
        self.assertNotIn("已采集的信息", reply.message)
        self.assertNotIn("下面是已采集的信息", reply.message)


class RequestedHelpAndSpokenLineTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.backend, self.events = make_engine()
        self.state, _ = self.engine.create_session()

    def test_requested_help_emergency_keeps_instructions_and_facts(self):
        record(
            self.engine,
            self.state,
            "我自己咨询，我喘不上气。",
            [
                fact("consult_for", "self", "我自己"),
                fact("chief_complaint", "喘不上气", "喘不上气"),
            ],
        )
        reply = self.engine._emergency_reply(
            self.state, ["严重呼吸困难"], requested_help=True
        )
        self.assertIn(SOOTHING, reply.message)
        self.assertIn("请现在拨打 120", reply.message)
        self.assertIn("喘不上气", reply.message)
        self.assertTrue(reply.message.endswith("不要继续等待本次对话。"))

    def _speak(self, sheet):
        state, _ = self.engine.create_session()
        state.closed_reason = "confirmed_by_user"
        reply = AgentReply(
            "原收尾。",
            Phase.CLOSED,
            kind="closed",
            conclusion=sheet,
        )
        self.engine._speak_close_line(state, reply)
        return reply.message

    def test_speak_urgent_today_adds_direct_answer_after_line(self):
        message = self._speak(
            {
                "disposition": {"level": "urgent_today", "window_hours": 24},
                "goal_line": "想知道要不要马上去急诊",
                "worry": None,
                "goal_response": None,
            }
        )
        self.assertIn("要不要马上去急诊", message)
        self.assertIn("今天内", message)
        self.assertIn("原收尾。", message)

    def test_speak_self_care_worry_goal_order_unchanged(self):
        # Batch B order must not move: spoken line, then worry, then
        # goal, then the original close (batch C adds nothing here).
        message = self._speak(
            {
                "disposition": {"level": "self_care"},
                "goal_line": "想整理就医记录",
                "worry": {"object": "食物中毒", "response": "关于食物中毒，目前还不能确定。"},
                "goal_response": "关于平时怎么护眼，建议线下复查时一并确认。",
            }
        )
        self.assertLess(
            message.index("关于食物中毒"), message.index("关于平时怎么护眼")
        )
        self.assertTrue(message.endswith("原收尾。"))
        self.assertNotIn("要不要马上去急诊", message)

    def test_window_helper_fragment_is_used_when_present(self):
        # Proves the closing side calls format_window_text when the
        # conclusion worker provides it (fragment form).
        had = hasattr(conclusion_module, "format_window_text")
        original = getattr(conclusion_module, "format_window_text", None)
        conclusion_module.format_window_text = (
            lambda disposition, window_hours: "今天内"
        )
        try:
            self.state.risk = RiskAssessment(
                RiskLevel.URGENT, ["症状持续加重"]
            )
            reply = self.engine._urgent(self.state)
            self.assertIn("今天内去急诊", reply.message)
        finally:
            if had:
                conclusion_module.format_window_text = original
            else:
                delattr(conclusion_module, "format_window_text")

    def test_window_helper_full_sentence_is_used_when_present(self):
        # Full-sentence form: used verbatim after the direct "要".
        had = hasattr(conclusion_module, "format_window_text")
        original = getattr(conclusion_module, "format_window_text", None)
        conclusion_module.format_window_text = (
            lambda disposition, window_hours: (
                "建议今天内到线下医疗机构当面评估，不要在家观察等待。"
            )
        )
        try:
            self.state.risk = RiskAssessment(
                RiskLevel.URGENT, ["症状持续加重"]
            )
            reply = self.engine._urgent(self.state)
            self.assertIn("要不要马上去急诊：要，建议今天内", reply.message)
        finally:
            if had:
                conclusion_module.format_window_text = original
            else:
                delattr(conclusion_module, "format_window_text")


if __name__ == "__main__":
    unittest.main()
