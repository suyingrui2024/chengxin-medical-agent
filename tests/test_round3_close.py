"""Third-round close-layer fixes (W1 package).

Each test pins a third-round issue row with its own verbatim goal /
wording — never invented clinical content:

- N-11 / O-20 / D-16 / Y-17 / K-18 (same mechanism): an urgent
  close must not render the graded destination answer ("要不要马上
  去急诊：要") next to the goal_response mitigation sentence
  ("不足以直接回答"). Suppression is render-level only: the
  sheet's goal_response is untouched, and closes without a graded
  answer (the O4 routine shape) keep the mitigation sentence.
- O-19 / D-14 / D-15 (urgent walkthrough): the collected-facts echo
  is not a response, so the walk must run against echo-stripped
  text; examination parts (检查) are walked in urgent closes even
  though the shared walk skips them as visit parts.
- Y-16 (closed walkthrough): a visit part with residual content
  ("去医院挑开" -> "挑开") is walked as its own part.
- O-17 (closed walkthrough): a negated mention ("症状持续不缓解")
  does not count as a relief-method response.
- N-12 / T-04 (safety net): a denial recorded inside a PRESENT
  fact's evidence ("没有肢体无力说话不清") qualifies the matching
  return item with "新出现", as the grouped emergency_screen
  ABSENT fact carries no text of its own.
- E-09 (outlook): a chronic gradual onset ("一年内慢慢出现")
  replaces a worsening-class short-term outlook ("数小时内加重")
  with the existing chronic observation sentence.
- D-12 / F-13 / O-17 method limbs are deliberately NOT asserted
  here: self_care.json holds no matching entry reachable through
  the existing goal_part_handling path (热敷 0 hits; 停用/回避
  and 饮食缓解 have no sheet-item keyword match), so those limbs
  stay reported residuals and no content is invented to pass a
  test. The walkthrough sentences for those goals are covered by
  the O-17 / Y-16 / closed-part tests below where they exist.
"""

import unittest

from medagent import closing
from medagent.conclusion import (
    _CHRONIC_OUTLOOK,
    _adjust_chronic_outlook,
    _visit_part_residual,
    compose_conclusion,
    goal_part_handling,
    unaddressed_goal_parts,
    unaddressed_urgent_goal_parts,
)
from medagent.consultation import UserInput  # noqa: F401  (parity import)
from medagent.domain import (
    AgentReply,
    Assertion,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.runtime import build_runtime

FIXED_HANDLING = "这一点这次问诊的信息还不足以直接回答，需当面评估后决定"


def _fact(key, value, evidence, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t1",
        evidence=evidence,
    )


def _closed_state(engine, facts, risk_level=RiskLevel.ROUTINE, reason="confirmed_by_user"):
    state, _ = engine.create_session()
    for item in facts:
        state.record(item)
    state.phase = Phase.CLOSED
    state.closed_reason = reason
    state.risk = RiskAssessment(risk_level, [])
    return state


def _speak(engine, state, sheet):
    reply = AgentReply(
        "原收尾。", Phase.CLOSED, kind="closed", conclusion=sheet
    )
    closing.render_confirmed_close(state, reply)
    return reply.message


class ContradictionSuppressionTests(unittest.TestCase):
    """N-11 / O-20 / D-16 / Y-17 / K-18 at the urgent close."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _urgent_close(self, chief, goal, worry_sentence=None):
        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", chief, chief),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
            _fact("trajectory", "worsening", "越来越重"),
            _fact("severity", 9, "9分"),
            _fact("associated_symptoms", "伴随不适", "伴随不适"),
            _fact("patient_goal", goal, goal),
        ):
            state.record(item)
        if worry_sentence is not None:
            self.engine._capture_worry(state, worry_sentence, "t-worry")
        state.risk = RiskAssessment(RiskLevel.URGENT, ["症状影响评分较高"])
        reply = self.engine._urgent(state)
        self.engine._record_reply(state, reply, "t-close", "test urgent close")
        return reply

    def test_graded_answer_suppresses_mitigation_but_keeps_worry(self):
        reply = self._urgent_close(
            "头突然炸一样疼，是这辈子最疼的一次",
            "想知道要不要去急诊",
            "我这是不是脑出血？",
        )
        self.assertEqual(reply.kind, "urgent")
        self.assertIn("要不要马上去急诊：要", reply.message)
        # The sheet still carries the mitigation sentence; only the
        # render suppresses it next to the graded answer.
        self.assertIsNotNone(reply.conclusion["goal_response"])
        self.assertIn("不足以直接回答", reply.conclusion["goal_response"])
        self.assertNotIn("不足以直接回答", reply.message)
        self.assertIn(reply.conclusion["worry"]["response"], reply.message)

    def test_no_graded_answer_keeps_mitigation(self):
        # O4 shape, pinned at the append level: a close with no
        # direct sentence must not lose the goal_response.
        sheet = {
            "worry": {"response": "你问过是不是‘腰椎间盘突出’：目前还不能仅凭这次问诊确定这一点。"},
            "goal_response": (
                "你问到‘想知道要不要去医院’：这次问诊的信息还不足以直接回答这一点，"
                "需要当面评估后决定；请按结论单的处置建议行动，出现警示信号及时就医。"
            ),
        }
        reply = AgentReply("先说结论：这次的情况建议近期当面就诊评估一次。", Phase.CLOSED, kind="closed")
        closing._append_sheet_worry_goal(reply, sheet)
        self.assertIn("不足以直接回答", reply.message)


class UrgentWalkthroughTests(unittest.TestCase):
    """O-19 / D-14 / D-15: urgent closes walk every goal part."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _urgent_close(self, chief, goal):
        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", chief, chief),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
            _fact("trajectory", "worsening", "越来越重"),
            _fact("severity", 7, "7分"),
            _fact("associated_symptoms", "伴随不适", "伴随不适"),
            _fact("patient_goal", goal, goal),
        ):
            state.record(item)
        state.risk = RiskAssessment(RiskLevel.URGENT, ["症状持续加重"])
        reply = self.engine._urgent(state)
        self.engine._record_reply(state, reply, "t-close", "test urgent close")
        return reply

    def test_o19_examination_part_gets_offline_evaluation_sentence(self):
        goal = "想知道要不要去医院，要不要做检查"
        reply = self._urgent_close("我腰疼还往腿上窜，走到小腿发麻", goal)
        self.assertEqual(reply.conclusion["goal_line"], goal)
        self.assertIn("要不要马上去急诊：要", reply.message)
        self.assertIn("关于‘要不要做检查’：", reply.message)
        self.assertIn(FIXED_HANDLING, reply.message)

    def test_d14_care_and_medication_parts_are_walked_despite_echo(self):
        # The facts echo quotes the whole goal; the walk must still
        # render a sentence for each non-destination part.
        reply = self._urgent_close(
            "我手上胳膊上反复起疹子，痒得晚上睡不着",
            "想知道平时怎么护理，要不要去医院开药",
        )
        self.assertIn("关于‘想知道平时怎么护理’：", reply.message)
        self.assertIn("关于‘开药’：", reply.message)

    def test_d15_sequelae_part_is_walked_despite_echo(self):
        reply = self._urgent_close(
            "我一侧肋骨那一片针扎样疼，起了成簇的水疱",
            "想知道要不要马上去医院，会不会留后遗症",
        )
        self.assertIn("要不要马上去急诊：要", reply.message)
        self.assertIn("关于‘会不会留后遗症’：", reply.message)

    def test_echo_strip_helper_only_cuts_the_facts_list(self):
        strip = closing._strip_close_facts_echo
        self.assertEqual(strip("没有信息清单的文本。"), "没有信息清单的文本。")
        self.assertEqual(
            strip("头。已采集的信息：主要问题：腰疼；期望与担心：想知道要不要去医院，不必等问诊补齐再求助。尾。"),
            "头。尾。",
        )


class GoalPartUnitTestsRound3(unittest.TestCase):
    """Y-16 residual parts, O-17 negated mentions, O-19 exam parts."""

    def test_visit_part_residual_keeps_embedded_content_only(self):
        self.assertEqual(_visit_part_residual("想知道要不要去医院挑开"), "挑开")
        self.assertEqual(_visit_part_residual("要不要去医院开药"), "开药")
        self.assertEqual(_visit_part_residual("要不要去医院"), "")
        self.assertEqual(_visit_part_residual("要不要马上去急诊"), "")
        self.assertEqual(_visit_part_residual("最想知道孩子这种情况要不要去医院"), "")
        self.assertEqual(_visit_part_residual("要不要去医院检查"), "")
        # Pinned record-organisation shape: 就医记录 is the record
        # itself, not a second question — no residual sentence.
        self.assertEqual(_visit_part_residual("想整理就医记录"), "")

    def test_y16_pick_part_is_unaddressed(self):
        parts = unaddressed_goal_parts(
            "想知道要不要去医院挑开，能不能自己热敷",
            "先说结论：暂时不需要专门跑一趟医院。",
        )
        self.assertIn("挑开", parts)
        self.assertIn("能不能自己热敷", parts)

    def test_o17_negated_relief_mention_is_not_a_response(self):
        visible = (
            "你问到要不要去检查：目前的建议是先在家观察；"
            "如果出现结论单安全网中列出的情况，或症状持续不缓解、"
            "加重，仍需线下评估，届时可凭本结论单到线下医疗机构就诊。"
        )
        self.assertEqual(
            unaddressed_goal_parts("想知道要不要去医院，平时怎么缓解", visible),
            ["平时怎么缓解"],
        )

    def test_o19_exam_part_walked_only_in_urgent_walk(self):
        goal = "想知道要不要去医院，要不要做检查"
        visible = "要不要马上去急诊：要，建议今天内到急诊当面评估。"
        self.assertEqual(unaddressed_goal_parts(goal, visible), [])
        self.assertEqual(
            unaddressed_urgent_goal_parts(goal, visible),
            ["要不要做检查"],
        )
        self.assertEqual(
            unaddressed_urgent_goal_parts(goal, visible + "要不要做检查由线下评估决定。"),
            [],
        )

    def test_method_limbs_still_fall_back_to_fixed_sentence(self):
        # D-12 / F-13 / O-17 method limbs stay residuals: no sheet
        # item matches, so handling must be the fixed sentence —
        # never an invented method line.
        sheet = {
            "self_care": {
                "do": ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
                "avoid": ["不要自行服用抗生素"],
                "measure": [],
            }
        }
        for part in (
            "想知道要不要停用这个护肤品",
            "还有吃什么能缓解一点",
            "能不能自己热敷",
        ):
            with self.subTest(part=part):
                self.assertEqual(goal_part_handling(part, sheet), FIXED_HANDLING)


class ClosedWalkthroughTestsRound3(unittest.TestCase):
    """Y-16 / O-17 at the confirmed close level."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _close_with_goal(self, chief, goal, worry=None):
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", chief, chief),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
            _fact("onset", "两天前", "两天前"),
            _fact("severity", 5, "5分"),
        ]
        if worry is not None:
            facts.append(_fact("patient_worry", worry, "我这是不是" + worry))
        facts.append(_fact("patient_goal", goal, goal))
        state = _closed_state(self.engine, facts)
        sheet = compose_conclusion(state)
        return sheet, _speak(self.engine, state, sheet)

    def test_y16_pick_part_gets_named_sentence(self):
        _sheet, message = self._close_with_goal(
            "眼皮上长了个红肿的小包，碰着疼",
            "想知道要不要去医院挑开，能不能自己热敷",
            "麦粒肿",
        )
        self.assertIn("关于‘挑开’：", message)
        self.assertIn("关于‘能不能自己热敷’：", message)

    def test_o17_relief_part_gets_named_sentence(self):
        _sheet, message = self._close_with_goal(
            "我脖子肩膀僵得厉害，转头都费劲",
            "想知道要不要去医院，平时怎么缓解",
            "颈椎病",
        )
        self.assertIn("关于‘平时怎么缓解’：", message)


class DeniedSafetyNetTests(unittest.TestCase):
    """N-12 / T-04 live shape: denial inside PRESENT evidence."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_denial_in_present_evidence_qualifies_return_item(self):
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", "一躺下翻身就晕，天旋地转，不到一分钟就过去了", "一躺下翻身就晕"),
            _fact("emergency_screen", None, None, Assertion.ABSENT),
            _fact("onset", "昨天夜里翻身时开始", "昨天夜里翻身时开始"),
            _fact("severity", 5, "5分"),
            _fact(
                "associated_symptoms",
                "恶心想吐",
                "有恶心想吐。没有耳鸣、听力下降，没有肢体无力、说话不清，也没有发烧",
            ),
            _fact(
                "dizzy_details",
                "天旋地转，躺下翻身诱发，不到一分钟就过去",
                "是天旋地转的感觉。每次不到一分钟就过去了。和躺下、翻身有关。没有耳鸣、听力下降，也没有走路不稳、说话不清",
            ),
            _fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院"),
        ]
        state = _closed_state(self.engine, facts)
        sheet = compose_conclusion(state)
        self.assertIn("言语不清新出现", sheet["return_now_if"])
        self.assertNotIn("言语不清", sheet["return_now_if"])
        # Already-qualified items are untouched.
        self.assertIn("肢体无力新出现", sheet["return_now_if"])
        self.assertIn("走路不稳明显加重", sheet["return_now_if"])

    def test_unknown_phrasing_is_not_a_denial(self):
        # "说不清" (unknown) must not qualify anything: only a
        # clause starting with a negation marker counts.
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", "一躺下翻身就晕，天旋地转，不到一分钟就过去了", "一躺下翻身就晕"),
            _fact("emergency_screen", None, None, Assertion.ABSENT),
            _fact("onset", "昨天夜里翻身时开始", "昨天夜里翻身时开始"),
            _fact("severity", 5, "5分"),
            _fact("trajectory", None, "说不清", Assertion.UNKNOWN),
            _fact("associated_symptoms", "恶心想吐", "有恶心想吐"),
            _fact(
                "dizzy_details",
                "天旋地转，躺下翻身诱发，不到一分钟就过去",
                "是天旋地转的感觉。每次不到一分钟就过去了。和躺下、翻身有关",
            ),
            _fact("patient_goal", "想知道要不要去医院", "想知道要不要去医院"),
        ]
        state = _closed_state(self.engine, facts)
        sheet = compose_conclusion(state)
        self.assertIn("言语不清", sheet["return_now_if"])
        self.assertNotIn("言语不清新出现", sheet["return_now_if"])


class ChronicOutlookTests(unittest.TestCase):
    """E-09: worsening-class short-term outlooks on chronic onset."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _state_with_onset(self, onset):
        state, _ = self.engine.create_session()
        state.record(_fact("onset", onset, onset))
        return state

    def test_worsening_templates_are_replaced_on_chronic_onset(self):
        state = self._state_with_onset("一年内慢慢出现")
        for outlook in (
            "呼吸方面的问题可能在数小时内加重，重点观察呼吸是否越来越费力、说话是否越来越吃力。",
            "腹部急症进展通常以小时计，及时当面检查有助于尽早明确原因。",
            "这类头晕可能在短时间内加重，需要尽快明确原因。",
        ):
            with self.subTest(outlook=outlook):
                sheet = _adjust_chronic_outlook({"outlook": outlook}, state)
                self.assertEqual(sheet["outlook"], _CHRONIC_OUTLOOK)

    def test_acute_onset_keeps_worsening_outlook(self):
        state = self._state_with_onset("今天突然出现")
        outlook = "呼吸方面的问题可能在数小时内加重，重点观察呼吸是否越来越费力。"
        sheet = _adjust_chronic_outlook({"outlook": outlook}, state)
        self.assertEqual(sheet["outlook"], outlook)

    def test_e09_sheet_level_outlook_is_chronic(self):
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", "爬楼就喘，平时呼吸还好", "爬楼就喘"),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
            _fact("onset", "一年内慢慢出现", "一年内慢慢出现"),
            _fact("severity", 4, "4分"),
            _fact("resp_breathing", "爬楼就喘", "爬楼就喘"),
        ]
        state = _closed_state(
            self.engine, facts, risk_level=RiskLevel.URGENT, reason="urgent_route"
        )
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["outlook"], _CHRONIC_OUTLOOK)
        self.assertNotIn("数小时内", sheet["outlook"])
