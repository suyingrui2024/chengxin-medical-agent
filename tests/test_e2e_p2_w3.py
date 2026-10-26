"""诚信 E2E 修复批 W3（结论引擎侧）: P2-4 / P2-5 回归测试。

P2-4: 口腔组 course 为 "<症状>有没有新出现或加重" 倒序写法时，
composer 的 T-04 改写不得把限定词 "加重" 当成症状枚举处理——
患者原话含 "吃东西时加重" 时，旧逻辑会把枚举清空并落入
"原有症状有没有加重"，把明确否认的肿胀、未提及的发热说成
已有症状。本文件 pin 住修复：此时 outlook 原文不动。

P2-5: trajectory 新增 intermittent（阵发性/间歇）合法落账。
- 自适应校验接受 intermittent（旧行为：枚举值未注册而拒；
  改记 unknown 又因无不确定引文而拒；最终近似记 stable 失真）。
- 阵发原话仍不得记为 stable（波动 guard 扩展 + 指引改为 intermittent）。
- 处置档不变：intermittent 不触发 triage 的 worsening 单线索
  urgent（triage 只认 worsening），与修前 stable 的处置一致。

P2-6 在本批只做溯源、不改 sheet 契约，故本文件不含 P2-6 用例：
visit 目标不进 goal_response 已由 test_batch_b_worry pin 住
（关闭文本的 batch-A 句拥有该答案）。
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.conclusion import _adjust_assertion_wording
from medagent.consultation import VALUE_LABELS
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import EventStore
from medagent.interpreter import Interpreter
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

ORAL_COURSE = (
    "这类口腔不适多数会在数天内逐渐缓解或出现明确变化，"
    "观察期间重点看面部肿胀、张口困难或发热有没有新出现或加重。"
)


def _fact(key, value, evidence, assertion=Assertion.PRESENT):
    return Fact(
        key=key,
        value=value,
        assertion=assertion,
        source=Source.PATIENT,
        turn_id="t0",
        evidence=evidence,
    )


def _state_with(*facts):
    state = CaseState(session_id="e2e-p2-w3")
    for fact in facts:
        state.record(fact)
    return state


class ScriptBackend:
    name = "script"

    def decide(self, session_id, messages):
        raise RuntimeError("not used in W3 tests")


def _make_adaptive():
    engine = AdaptiveConsultationEngine(
        content=ContentRepository(),
        triage=TriageService(),
        self_checks=SelfCheckService(),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=ScriptBackend(),
    )
    state, _ = engine.create_session()
    return engine, state


def _record_cmd(facts):
    return {"tool": "record_facts", "arguments": {"facts": facts}}


def _proposal(key, value, evidence, assertion="present"):
    return {
        "key": key,
        "value": value,
        "assertion": assertion,
        "evidence": evidence,
    }


class P24OralOutlookTests(unittest.TestCase):
    """E2E-P2-4: 限定词枚举不得触发“原有症状”回落。"""

    def test_worsening_mention_does_not_create_preexisting_symptoms(self):
        # 牙痛旅程原话：进食加重（PRESENT 事实证据含“加重”），
        # 同时肿胀被否认、发热未提及——但否认/未提及不在本用例
        # 的 present_texts 内；关键是“加重”本身不是症状。
        state = _state_with(
            _fact("chief_complaint", "牙痛", "牙痛"),
            _fact("associated_symptoms", "吃东西时加重", "吃东西时加重"),
        )
        sheet = {"outlook": ORAL_COURSE}
        out = _adjust_assertion_wording(sheet, state)
        self.assertEqual(out["outlook"], ORAL_COURSE)
        self.assertNotIn("原有症状", out["outlook"])

    def test_genuine_present_symptom_still_falls_back(self):
        # T-04 原有语义保留：枚举全是真实症状且均已报告时，
        # 回落句仍生效（本用例与 P2-4 修复互不干扰）。
        state = _state_with(_fact("associated_symptoms", "发热", "发热"))
        sheet = {"outlook": "观察期间重点看有没有新出现发热。"}
        out = _adjust_assertion_wording(sheet, state)
        self.assertEqual(out["outlook"], "观察期间重点看原有症状有没有加重。")


class P25IntermittentTrajectoryTests(unittest.TestCase):
    """E2E-P2-5: 阵发性合法落账，且不改处置档。"""

    def test_adaptive_validation_accepts_intermittent(self):
        engine, state = _make_adaptive()
        result = engine._execute(
            state,
            _record_cmd(
                [_proposal("trajectory", "intermittent", "一阵一阵的，中间有缓解")]
            ),
            "一阵一阵的，中间有缓解",
            "t1",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(state.get("trajectory"), "intermittent")

    def test_paroxysmal_evidence_still_not_stable(self):
        engine, state = _make_adaptive()
        result = engine._execute(
            state,
            _record_cmd(
                [_proposal("trajectory", "stable", "一阵一阵的，中间有缓解")]
            ),
            "一阵一阵的，中间有缓解",
            "t1",
        )
        self.assertFalse(result["ok"])
        self.assertNotIn("trajectory", state.facts)
        rejected = {item["key"]: item for item in result["rejected"]}
        self.assertIn("波动交替", rejected["trajectory"]["reason"])
        self.assertIn("intermittent", rejected["trajectory"]["guidance"])

    def test_intermittent_does_not_trigger_urgent_single_clue(self):
        # 与修前 stable 的处置一致：triage 只认 worsening。
        state = _state_with(
            _fact("emergency_screen", None, "都没有", Assertion.ABSENT),
            _fact("trajectory", "intermittent", "一阵一阵的，中间有缓解"),
        )
        risk = TriageService().assess(state, "")
        self.assertNotEqual(risk.level.name, "URGENT")
        self.assertNotIn("症状持续加重", risk.triggers or [])

    def test_intermittent_registered_and_displayed(self):
        question = ContentRepository().question("trajectory")
        self.assertIn("intermittent", {o.value for o in question.options})
        # 确定性引擎：与新选项 label 逐字一致的回答直接映射。
        result = Interpreter().interpret(question, "一阵一阵的")
        self.assertEqual(result.value, "intermittent")
        self.assertEqual(result.assertion, Assertion.PRESENT)
        self.assertEqual(VALUE_LABELS["intermittent"], "阵发性")


if __name__ == "__main__":
    unittest.main()
