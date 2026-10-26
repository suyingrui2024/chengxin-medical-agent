"""P2 close layer (hypothesis-driven implementation, package P2).

Covers, with second-round case texts as fixtures (never invented):
- urgent closes render the sheet's worry response verbatim
  (mechanism ①, 18 cases; the six named here are K-11 / O-18 / U-19 /
  S-03 / S-08 / S-10, the rest share the one wiring);
- goal parts are answered part by part (mechanism ②: K-16 / P-18 /
  T-16 / F-13), with drug / treatment parts always taking the fixed
  insufficient-information sentence, never a dosage or promise;
- the worry / goal slot collision renders the worry exactly once
  (mechanism ③: O-15 / U-17 shape);
- the deterministic urgent gate runs only after the closing turn's
  answer is recorded (mechanism ④: U-18 shape);
- the G-12 engineering exit (UNDETERMINED + unknown screen):
  uncertainty note + patient-derived worsening items, disposition
  mapping untouched;
- P-21 close-list punctuation / identity-echo hygiene;
- P-22 infant adaptation by supplementation, not replacement only;
- P1 group-bridge wiring and P3 scope-note wiring in _record_reply;
- byte regression for the conventional confirmed close.

AUDIT NOTES (consultation.py goal write path, read-only findings —
no code change here; agent.py findings go to the coordinator):
- consultation.py _capture_worry writes ONLY patient_worry, never
  patient_goal (first-wins, worry sentences cannot overwrite the
  goal through that path).
- consultation.py _h_intake -> _record_fact for the patient_goal
  question has NO is_worry_sentence diversion: a worry sentence
  given as the goal answer still lands in patient_goal. The P2
  composition guard (_build_goal_response returns None for worry
  sentences) stops the double render, but the real goal is then
  never re-asked — that fact-level diversion is a residual for the
  coordinator, not something this package's file list covers.
- agent.py L1612-1632 (read-only audit): the adaptive record_facts
  path tracks only whether the patient_goal item landed
  (goal_answered); no is_worry_sentence check diverts a worry
  sentence proposed for patient_goal, and no separate patient_worry
  write happens there. Same residual, adaptive side.
- agent.py urgent gates (L732 / L759 / L778 / L950 / L1708 / L2759,
  read-only audit): unlike the deterministic engine's three sites,
  the adaptive _submit gate runs BEFORE the model turn's
  record_facts proposals are executed, so a closing-turn answer in
  the adaptive engine is still unrecorded when the gate fires.
  That is the U-18 loss mechanism on the adaptive side; it lives in
  agent.py and is out of this package's file list.
"""

import unittest

from medagent.bridging import GROUP_BRIDGES, lint_group_bridge
from medagent.conclusion import (
    UNCERTAINTY_NOTE,
    compose_conclusion,
    goal_part_handling,
    split_goal_parts,
    spoken_close_line,
    unaddressed_goal_parts,
)
from medagent.consultation import UserInput
from medagent.domain import (
    AgentReply,
    Assertion,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.out_of_pathway import SCOPE_NOTE
from medagent.runtime import build_runtime

FIXED_HANDLING = "这一点这次问诊的信息还不足以直接回答，需当面评估后决定"

# Worry sentences verbatim from the second-round issue rows.
WORRY_CASES = (
    # (issue, worry sentence, expected worry object)
    ("K-11", "我这是不是感染扩散了？", "感染扩散"),
    ("O-18", "我这是不是腰椎骨折了？", "腰椎骨折"),
    ("U-19", "我这是不是前列腺出问题了？", "前列腺出问题"),
    ("S-03", "我这是不是疝气？会不会卡住？", "疝气"),
    ("S-08", "我这是不是肚子里穿孔了？", "肚子里穿孔"),
    ("S-10", "我这是不是疝气卡住了？", "疝气卡住"),
)

# Goal lines verbatim from the second-round issue rows.
GOAL_CASES = (
    # (issue, goal line, parts that must each get a handling sentence)
    (
        "K-16",
        "想知道要不要去医院，还是先吃止痛药观察。",
        ("还是先吃止痛药观察",),
    ),
    (
        "P-18",
        "最想知道孩子这种情况要不要去医院、要不要吃退烧药。",
        ("要不要吃退烧药",),
    ),
    (
        "T-16",
        "想知道要不要去医院，是不是需要做复位治疗。",
        ("是不是需要做复位治疗",),
    ),
    (
        "F-13",
        "我就想知道这样正不正常、要不要去医院，还有吃什么能缓解一点。",
        ("我就想知道这样正不正常", "还有吃什么能缓解一点"),
    ),
)


def _fact(key, value, evidence, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t1",
        evidence=evidence,
    )


def _closed_state(engine, facts, risk_level=RiskLevel.ROUTINE):
    state, _ = engine.create_session()
    for item in facts:
        state.record(item)
    state.phase = Phase.CLOSED
    state.closed_reason = "confirmed_by_user"
    state.risk = RiskAssessment(risk_level, [])
    return state


def _speak(engine, state, sheet):
    reply = AgentReply(
        "原收尾。", Phase.CLOSED, kind="closed", conclusion=sheet
    )
    engine._speak_close_line(state, reply)
    return reply.message


class UrgentWorryTests(unittest.TestCase):
    """Mechanism ①: the urgent close must carry the worry section."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _urgent_close(self, state, worry_sentence=None):
        if worry_sentence is not None:
            self.engine._capture_worry(state, worry_sentence, "t-worry")
        state.risk = RiskAssessment(RiskLevel.URGENT, ["症状影响评分较高"])
        reply = self.engine._urgent(state)
        self.engine._record_reply(state, reply, "t-close", "test urgent close")
        return reply

    def _base_state(self, chief, goal="想知道要不要马上去急诊"):
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
        return state

    def test_six_named_cases_render_worry_verbatim(self):
        chiefs = {
            "K-11": "一颗牙疼了两天",
            "O-18": "摔了一跤后腰疼得动不了",
            "U-19": "想尿尿不出来，小肚子胀",
            "S-03": "肚子胀，站久了鼓出包块",
            "S-08": "全肚子疼",
            "S-10": "肚子剧痛，包块回不去",
        }
        for issue, sentence, obj in WORRY_CASES:
            with self.subTest(issue=issue):
                state = self._base_state(chiefs[issue])
                reply = self._urgent_close(state, sentence)
                self.assertEqual(reply.kind, "urgent")
                sheet = reply.conclusion
                self.assertIsNotNone(sheet)
                self.assertIsNotNone(sheet.get("worry"))
                self.assertEqual(sheet["worry"]["object"], obj)
                # Verbatim: the close carries the sheet's own response.
                self.assertIn(sheet["worry"]["response"], reply.message)

    def test_worry_absent_produces_no_empty_worry_shell(self):
        state = self._base_state("腰疼")
        reply = self._urgent_close(state)
        self.assertIsNone(reply.conclusion.get("worry"))
        self.assertNotIn("你问过是不是", reply.message)

    def test_urgent_close_names_unconfirmed_background_items(self):
        # ④-4 note option: background never asked is named
        # generically (names only), the gate itself untouched.
        state = self._base_state("腰疼")
        reply = self._urgent_close(state)
        self.assertIn("以下背景信息本次尚未确认", reply.message)
        self.assertIn("用药", reply.message)
        self.assertIn("过敏", reply.message)
        self.assertIn("病史", reply.message)

    def test_urgent_close_with_background_asked_names_nothing(self):
        state = self._base_state("腰疼")
        for item in (
            _fact("medications_taken", "没有", "没有"),
            _fact("allergies", "不知道", "不知道", Assertion.UNKNOWN),
            _fact("relevant_history", "没有", "没有"),
        ):
            state.record(item)
        reply = self._urgent_close(state)
        self.assertNotIn("以下背景信息本次尚未确认", reply.message)


class GoalPartUnitTests(unittest.TestCase):
    """Mechanism ② at the helper level, on real goal lines."""

    def test_split_goal_parts(self):
        self.assertEqual(
            split_goal_parts("想知道要不要去医院，还是先吃止痛药观察。"),
            ["想知道要不要去医院", "还是先吃止痛药观察"],
        )
        self.assertEqual(
            split_goal_parts(
                "我就想知道这样正不正常、要不要去医院，还有吃什么能缓解一点。"
            ),
            ["我就想知道这样正不正常", "要不要去医院", "还有吃什么能缓解一点"],
        )
        self.assertEqual(split_goal_parts(""), [])
        self.assertEqual(split_goal_parts("要不要去医院、要不要去医院"), ["要不要去医院"])

    def test_unaddressed_parts_skip_visit_parts(self):
        # The batch-A visit sentence owns the visit part; only the
        # other parts are unaddressed.
        for _issue, goal, expected in GOAL_CASES:
            with self.subTest(issue=_issue):
                self.assertEqual(
                    unaddressed_goal_parts(goal, "先说结论：暂时不需要专门跑一趟医院。"),
                    list(expected),
                )

    def test_unaddressed_parts_honor_real_handling(self):
        # A part whose keywords already appear in visible text (a
        # real handling, not the goal_response quote) is addressed.
        self.assertEqual(
            unaddressed_goal_parts(
                "想知道要不要去医院，还是先吃止痛药观察。",
                "关于止痛药观察：请按医嘱使用。",
            ),
            [],
        )

    def test_drug_and_treatment_parts_take_fixed_sentence(self):
        sheet = {
            "self_care": {
                "do": ["多休息，避免劳累", "不要自行服用抗生素"],
                "avoid": [],
                "measure": [],
            }
        }
        for part in (
            "还是先吃止痛药观察",
            "要不要吃退烧药",
            "是不是需要做复位治疗",
            "能不能自己滴眼药水",
            "含片缓解",
        ):
            with self.subTest(part=part):
                self.assertEqual(
                    goal_part_handling(part, sheet), FIXED_HANDLING
                )

    def test_k17_falls_back_without_invented_ulcer_content(self):
        # K-17 residual, pinned: self_care.json has no ulcer entry,
        # so the handling must be the fixed sentence — never an
        # invented ulcer-specific line.
        sheet = {
            "self_care": {
                "do": ["多休息，避免劳累", "适量喝水", "把症状变化记下来，就诊时告诉医生"],
                "avoid": ["不要自行服用抗生素"],
                "measure": [],
            }
        }
        for part in ("想知道怎么好得快", "要不要用药"):
            self.assertEqual(
                goal_part_handling(part, sheet, "嘴里长了溃疡"), FIXED_HANDLING
            )
        self.assertFalse(
            any("溃疡" in item or "辣" in item or "酸" in item for item in sheet["self_care"]["do"])
        )


class GoalPartCloseTests(unittest.TestCase):
    """Mechanism ② at the close level, via the composer."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _close_with_goal(self, chief, goal, worry=None):
        # Each real case also carried a separately captured worry
        # (K-16 牙神经发炎 / P-18 肺炎 / T-16 耳石症 / F-13 妊娠剧吐,
        # all from the issue rows). The worry fact matters: without
        # it, a goal containing "是不是…" would be consumed into
        # the worry slot by _build_worry and goal_line would be None
        # — the real runs kept goal_line precisely because the worry
        # fact already existed. See also the report's residual on
        # is_worry_sentence overreach for treatment questions.
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
        message = _speak(self.engine, state, sheet)
        return sheet, message

    def test_four_named_cases_each_part_gets_a_handling_sentence(self):
        chiefs = {
            "K-16": ("一颗牙疼了两天", "牙神经发炎"),
            "P-18": ("孩子发烧咳嗽流鼻涕", "肺炎"),
            "T-16": ("一躺下翻身就晕", "耳石症"),
            "F-13": ("天天恶心，吃不下东西", "妊娠剧吐"),
        }
        for issue, goal, parts in GOAL_CASES:
            with self.subTest(issue=issue):
                chief, worry = chiefs[issue]
                sheet, message = self._close_with_goal(chief, goal, worry)
                # Visit keyword in the whole goal nulls the single
                # goal_response; the per-part sentences must cover.
                self.assertIsNone(sheet["goal_response"])
                for part in parts:
                    self.assertIn(f"关于‘{part}’：", message)
                self.assertIn(FIXED_HANDLING, message)

    def test_f13_normal_and_relief_words_appear(self):
        _sheet, message = self._close_with_goal(
            "天天恶心，吃不下东西",
            "我就想知道这样正不正常、要不要去医院，还有吃什么能缓解一点。",
            "妊娠剧吐",
        )
        self.assertIn("正常", message)
        self.assertIn("缓解一点", message)

    def test_null_goal_response_with_landed_goal_never_ends_bare(self):
        sheet, message = self._close_with_goal(
            "一颗牙疼了两天", "想知道要不要去医院，还是先吃止痛药观察。", "牙神经发炎"
        )
        self.assertIsNotNone(sheet["goal_line"])
        self.assertIsNone(sheet["goal_response"])
        self.assertIn("关于‘还是先吃止痛药观察’：", message)
        # No dosage or promise wording is composed.
        self.assertNotIn("每次", message)
        self.assertNotIn("一片", message)


class SlotCollisionTests(unittest.TestCase):
    """Mechanism ③: a worry sentence in the goal slot renders once."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _sheet_with(self, goal_value, worry_value):
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", "腰疼", "腰疼"),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
            _fact("severity", 4, "4分"),
        ]
        if worry_value is not None:
            facts.append(_fact("patient_worry", worry_value, "想知道是不是腰椎间盘突出"))
        if goal_value is not None:
            facts.append(_fact("patient_goal", goal_value, goal_value))
        state = _closed_state(self.engine, facts)
        return state, compose_conclusion(state)

    def test_worry_sentence_goal_with_separate_worry_renders_once(self):
        state, sheet = self._sheet_with(
            "想知道是不是腰椎间盘突出", "腰椎间盘突出"
        )
        self.assertIsNotNone(sheet["worry"])
        self.assertIsNone(sheet["goal_response"])
        message = _speak(self.engine, state, sheet)
        self.assertEqual(message.count(sheet["worry"]["response"]), 1)

    def test_worry_sentence_goal_alone_is_consumed_into_worry(self):
        state, sheet = self._sheet_with("想知道是不是腰椎间盘突出", None)
        self.assertIsNotNone(sheet["worry"])
        self.assertIsNone(sheet["goal_line"])
        self.assertIsNone(sheet["goal_response"])
        message = _speak(self.engine, state, sheet)
        self.assertEqual(message.count(sheet["worry"]["response"]), 1)


class U18RegressionTests(unittest.TestCase):
    """Mechanism ④: deterministic close lands the closing turn first.

    The three deterministic _urgent_or_defer call sites (correction /
    restore / main turn) all run after the turn's fact recording:
    correction records before gating, restore records the restored
    fact before gating, and the main pipeline runs _h_intake
    (records) before _h_risk_escalation (gates). This drive proves
    the main-turn case end to end: the urgent close fires on the
    patient_goal turn itself, and the closing turn's answers are in
    the final facts and the sheet.
    """

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_urgent_close_on_goal_turn_keeps_closing_turn_facts(self):
        answers = {
            "consult_for": ("我自己", "self"),
            "sex": ("女", "female"),
            "pregnancy_status": ("没有怀孕，月经没有推迟", "not_pregnant"),
            "age": ("32岁", None),
            "chief_complaint": ("尿频尿急尿痛", None),
            "urinary_details": ("尿频尿急尿痛，小肚子胀", None),
            "emergency_screen": ("都没有", "no"),
            "onset": ("今天早上开始", None),
            "trajectory": ("越来越重", "worsening"),
            "severity": ("9分", None),
            "associated_symptoms": ("发烧39度，打寒战，一侧腰疼得厉害", None),
            "reported_temperature": ("没量过", None),
            "medications_taken": ("没有", None),
            "allergies": ("不知道", None),
            "relevant_history": ("没有", None),
            "patient_goal": ("想知道要不要马上去医院", None),
        }
        state, reply = self.engine.create_session()
        closing_qid = None
        for _ in range(40):
            if reply.kind in ("closed", "urgent", "emergency"):
                break
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = self.engine.submit(
                    state.session_id, UserInput("暂时跳过", "skip")
                )
                continue
            closing_qid = reply.question_id
            text, choice = answers[reply.question_id]
            reply = self.engine.submit(state.session_id, UserInput(text, choice))
        self.assertEqual(reply.kind, "urgent")
        # The close fired on the goal turn, not a later one.
        self.assertEqual(closing_qid, "patient_goal")
        # Closing-turn answers are in the final facts, not lost.
        self.assertIn("associated_symptoms", state.facts)
        self.assertIn("发烧", str(state.facts["associated_symptoms"].value))
        self.assertIn("patient_goal", state.facts)
        self.assertEqual(
            reply.conclusion["goal_line"], "想知道要不要马上去医院"
        )
        # The close's facts list is built from post-recording facts.
        self.assertIn("想知道要不要马上去医院", reply.message)


class G12ExitTests(unittest.TestCase):
    """G-12 engineering exit: uncertainty is stated, never silent."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _g12_state(self):
        state, _ = self.engine.create_session()
        # Canonical symptom words from the G-12 row (黑便两次 +
        # 暗红色呕吐物一次 + 头晕心慌并存): the worsening-item map
        # keys on the reported words themselves, so the fixture uses
        # 黑便 / 呕血 rather than paraphrases like 大便发黑.
        for item in (
            _fact("consult_for", "self", "我自己"),
            _fact(
                "chief_complaint",
                "黑便两次，呕血一次，头晕心慌",
                "黑便两次，呕血一次，头晕心慌",
            ),
            _fact("emergency_screen", None, "不知道有没有危险信号", Assertion.UNKNOWN),
            _fact("onset", "两天前", "两天前"),
            _fact("severity", 5, "5分"),
        ):
            state.record(item)
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.risk = RiskAssessment(RiskLevel.UNDETERMINED, [])
        return state

    def test_undetermined_unknown_screen_gets_note_and_targeted_items(self):
        state = self._g12_state()
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["uncertainty_note"], UNCERTAINTY_NOTE)
        # Only symptoms the patient actually reported.
        for item in (
            "再出现便血或黑便",
            "再出现呕血",
            "头晕加重",
            "心慌加重",
        ):
            self.assertIn(item, sheet["return_now_if"])
        # The disposition mapping itself is untouched: still a
        # routine-level sheet, not an invented escalation.
        self.assertIn(
            sheet["disposition"]["level"], ("self_care", "routine_visit")
        )
        message = _speak(self.engine, state, sheet)
        self.assertIn(UNCERTAINTY_NOTE, message)

    def test_undetermined_with_known_screen_gets_no_note(self):
        state = self._g12_state()
        state.record(
            Fact(
                key="emergency_screen",
                value=None,
                assertion=Assertion.ABSENT,
                source=Source.PATIENT,
                turn_id="t2",
                evidence="没有危险信号",
            )
        )
        self.assertEqual(
            state.facts["emergency_screen"].assertion, Assertion.ABSENT
        )
        sheet = compose_conclusion(state)
        self.assertNotIn("uncertainty_note", sheet)
        self.assertNotIn("再出现便血或黑便", sheet["return_now_if"])

    def test_no_reported_symptom_invents_no_item(self):
        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "self", "我自己"),
            _fact("chief_complaint", "肚子不舒服", "肚子不舒服"),
            _fact("emergency_screen", None, "不知道", Assertion.UNKNOWN),
        ):
            state.record(item)
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.risk = RiskAssessment(RiskLevel.UNDETERMINED, [])
        sheet = compose_conclusion(state)
        self.assertEqual(sheet["uncertainty_note"], UNCERTAINTY_NOTE)
        for item in (
            "再出现便血或黑便",
            "再出现呕血",
            "头晕加重",
            "心慌加重",
        ):
            self.assertNotIn(item, sheet["return_now_if"])


class P21Tests(unittest.TestCase):
    """P-21: close-list punctuation dedup + chief echo without identity."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_emergency_list_has_no_double_period_and_no_identity(self):
        state, _ = self.engine.create_session()
        state.transcript.append(
            {
                "role": "user",
                "content": "我是替孩子咨询的。我家孩子发烧时突然抽搐了，眼往上翻，叫不应。",
            }
        )
        reply = self.engine._emergency_reply(state, ["意识明显异常"])
        self.assertNotIn("。。", reply.message)
        self.assertNotIn("我是替孩子咨询的", reply.message)
        self.assertIn(
            "主要问题：我家孩子发烧时突然抽搐了，眼往上翻，叫不应。",
            reply.message,
        )

    def test_close_facts_text_strips_trailing_period(self):
        text = self.engine._close_facts_text(
            [
                {
                    "label": "主要问题",
                    "value": "叫不应。",
                    "evidence": "叫不应。",
                }
            ]
        )
        self.assertEqual(text, "主要问题：叫不应")

    def test_identity_prefix_rule_keeps_real_statements(self):
        strip = self.engine._strip_identity_prefix
        self.assertEqual(strip("我自己咨询，我胸口一阵阵刺痛。"), "我胸口一阵阵刺痛。")
        # "咨询过医生" is a real statement, not an identity clause.
        self.assertEqual(
            strip("我自己咨询过医生，他说没事。"), "我自己咨询过医生，他说没事。"
        )
        self.assertEqual(strip("肚子疼了两天。"), "肚子疼了两天。")


class P22Tests(unittest.TestCase):
    """P-22: infant adaptation supplements, not only replaces."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_infant_generic_sheet_gains_infant_items(self):
        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "child", "替孩子咨询"),
            _fact("age_years", 1, "1岁"),
            _fact("chief_complaint", "宝宝不舒服", "宝宝不舒服"),
            _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
        ):
            state.record(item)
        state.phase = Phase.CLOSED
        state.closed_reason = "confirmed_by_user"
        state.risk = RiskAssessment(RiskLevel.ROUTINE, [])
        sheet = compose_conclusion(state)
        self.assertIn(
            "哭闹明显加重、精神变差或尿量明显减少", sheet["return_now_if"]
        )
        self.assertIn(
            "把哭闹、拒食、精神、尿量和排便性状的变化记下来，就诊时告诉医生",
            sheet["self_care"]["do"],
        )

    def test_infant_items_are_not_duplicated(self):
        from medagent.conclusion import _apply_infant_observable

        state, _ = self.engine.create_session()
        for item in (
            _fact("consult_for", "child", "替孩子咨询"),
            _fact("age_years", 1, "1岁"),
        ):
            state.record(item)
        sheet = {
            "self_care": {"do": [], "avoid": [], "measure": []},
            "return_now_if": ["意识异常或抽搐"],
        }
        sheet = _apply_infant_observable(sheet, state)
        sheet = _apply_infant_observable(sheet, state)
        self.assertEqual(
            sheet["return_now_if"].count("哭闹明显加重、精神变差或尿量明显减少"), 1
        )
        self.assertEqual(
            sheet["self_care"]["do"].count(
                "把哭闹、拒食、精神、尿量和排便性状的变化记下来，就诊时告诉医生"
            ),
            1,
        )


class GroupBridgeAndScopeTests(unittest.TestCase):
    """P1 wiring (question replies) and P3 wiring (off-pathway close)."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def _question_reply(self, question_id, chief=None):
        state, _ = self.engine.create_session()
        if chief is not None:
            state.record(_fact("chief_complaint", chief, chief))
        reply = AgentReply(
            "原问题？", Phase.HISTORY, kind="question", question_id=question_id
        )
        return state, reply

    def test_group_bridge_prepends_when_no_single_bridge(self):
        state, reply = self._question_reply("severity", "咳嗽两天")
        self.engine._apply_group_bridge(state, reply)
        self.assertTrue(reply.message.startswith(GROUP_BRIDGES["deep_dive"]))
        self.assertTrue(lint_group_bridge(GROUP_BRIDGES["deep_dive"]))
        self.assertEqual(
            reply.developer_trace["bridging"]["kind"], "group:deep_dive"
        )

    def test_group_bridge_groups(self):
        cases = (
            ("medications_taken", "咳嗽两天", "background"),
            ("associated_symptoms", "咳嗽两天", "proactive"),
            ("warning_意识", "体重一年涨了20斤", "proactive"),
        )
        for question_id, chief, group in cases:
            with self.subTest(question_id=question_id):
                state, reply = self._question_reply(question_id, chief)
                self.engine._apply_group_bridge(state, reply)
                self.assertTrue(reply.message.startswith(GROUP_BRIDGES[group]))

    def test_group_bridge_never_stacks_on_single_bridge(self):
        state, reply = self._question_reply("severity", "咳嗽两天")
        reply.developer_trace = {"bridging": {"applied": True, "kind": "single"}}
        self.engine._apply_group_bridge(state, reply)
        self.assertEqual(reply.message, "原问题？")

    def test_scope_note_appended_on_off_pathway_close_only(self):
        for chief, expect in (
            ("体重一年涨了20斤，越吃越多", True),
            ("咳嗽两天，咳黄痰", False),
        ):
            with self.subTest(chief=chief):
                state, _ = self.engine.create_session()
                for item in (
                    _fact("consult_for", "self", "我自己"),
                    _fact("chief_complaint", chief, chief),
                    _fact("emergency_screen", None, "没有危险信号", Assertion.ABSENT),
                    _fact("severity", 3, "3分"),
                ):
                    state.record(item)
                state.phase = Phase.CLOSED
                state.closed_reason = "confirmed_by_user"
                state.risk = RiskAssessment(RiskLevel.ROUTINE, [])
                reply = AgentReply(
                    "原收尾。",
                    Phase.CLOSED,
                    kind="closed",
                    summary=self.engine._summary_payload(
                        state, review_status="confirmed_by_user"
                    ),
                )
                self.engine._record_reply(state, reply, "t-close", "test close")
                if expect:
                    self.assertIn(SCOPE_NOTE, reply.message)
                else:
                    self.assertNotIn(SCOPE_NOTE, reply.message)


class ConfirmedCloseByteRegressionTests(unittest.TestCase):
    """Conventional confirmed close: worry/goal bytes unchanged."""

    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")

    def tearDown(self):
        self.host.stop()

    def test_worry_goal_extra_bytes_unchanged(self):
        state, _ = self.engine.create_session()
        state.closed_reason = "confirmed_by_user"
        sheet = {
            "disposition": {"level": "self_care"},
            "goal_line": "想知道要不要去检查",
            "worry": {"object": "食物中毒", "response": "关于食物中毒，目前还不能确定。"},
            "goal_response": "关于平时怎么护眼，建议线下复查时一并确认。",
        }
        message = _speak(self.engine, state, sheet)
        batch_a = (
            "你问到要不要去检查：目前的建议是先在家观察；"
            "如果出现结论单安全网中列出的情况，或症状持续不缓解、"
            "加重，仍需线下评估，届时可凭本结论单到线下医疗机构就诊。"
        )
        self.assertEqual(
            message,
            spoken_close_line(sheet)
            + batch_a
            + "关于食物中毒，目前还不能确定。"
            + "关于平时怎么护眼，建议线下复查时一并确认。"
            + "原收尾。",
        )


if __name__ == "__main__":
    unittest.main()
