"""P2 validation reform: actionable errors, per-item verdicts, cross-turn quotes.

Covers the live-evaluation failure modes (cases 5/7/8/9): rejections whose
messages named no field and no fix, atomic batches that hid which items
passed, and correction merges that could not quote the fact's own source
turn. The verbatim-quote and assertion contracts themselves are unchanged.
"""

import unittest

from medagent.agent_tools import ProposalError, validate_command, validate_proposals
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.knowledge import ContentRepository
from tests.test_agent import fact


class ActionableErrorTests(unittest.TestCase):
    def setUp(self):
        self.content = ContentRepository()

    def propose(self, raw, items, state=None):
        return validate_proposals(
            self.content, state or CaseState("synthetic"), raw, items
        )

    def test_absent_without_clear_denial_names_field_and_fix(self):
        # Case 8: natural phrasing with no machine-recognizable denial cue.
        raw = "都没有。小便正常，不疼，也没尿频尿急，尿色也没发红"
        with self.assertRaises(ProposalError) as caught:
            self.propose(
                raw,
                [
                    fact(
                        "urinary_details",
                        None,
                        "小便正常，不疼，也没尿频尿急，尿色也没发红",
                        "absent",
                    )
                ],
            )
        message = str(caught.exception)
        self.assertIn("urinary_details", message)
        self.assertIn("阴性事实缺少明确否定证据", message)
        self.assertIn("扩大引文", message)
        self.assertIn("明确否认", message)
        # Following the guidance (widen the quote to the explicit denial) passes.
        result = self.propose(
            raw,
            [fact("urinary_details", None, "都没有", "absent")],
        )
        self.assertEqual(result[0]["assertion"], Assertion.ABSENT)

    def test_absent_narrative_without_cue_offers_present_route(self):
        # Case 7: "还没给吃过药…还没给他用" is a clear denial to a human but
        # carries no cue the checker recognizes; guidance must offer the
        # verbatim present-narrative route, which does pass.
        raw = "还没给吃过药。家里有炉甘石洗剂，但还没给他用"
        with self.assertRaises(ProposalError) as caught:
            self.propose(raw, [fact("medications_taken", None, raw, "absent")])
        message = str(caught.exception)
        self.assertIn("medications_taken", message)
        self.assertIn("阴性事实缺少明确否定证据", message)
        self.assertIn("改记 present", message)
        self.assertIn("原样保留", message)
        result = self.propose(raw, [fact("medications_taken", raw, raw, "present")])
        self.assertEqual(result[0]["assertion"], Assertion.PRESENT)

    def test_uncertain_absence_guidance_points_to_unknown(self):
        with self.assertRaises(ProposalError) as caught:
            self.propose(
                "过敏不知道",
                [fact("allergies", None, "过敏不知道", "absent")],
            )
        message = str(caught.exception)
        self.assertIn("allergies", message)
        self.assertIn("不确定的原话不能记为明确否认", message)
        self.assertIn("unknown", message)
        self.assertIn("澄清", message)

    def test_present_disguised_denial_names_field_and_two_routes(self):
        # Case 5: the model normalized the patient's words into "无发热"; the
        # quote itself has no recognizable denial cue, so the denial in the
        # value is unconfirmed. Verbatim value (no cue words) passes.
        raw = "腰扭伤了，疼会往右腿窜，大小便都正常，会阴不麻，也没发烧"
        with self.assertRaises(ProposalError) as caught:
            self.propose(
                raw,
                [fact("back_details", "腰扭伤，右腿放射痛，无发热", raw, "present")],
            )
        message = str(caught.exception)
        self.assertIn("back_details", message)
        self.assertIn("不能借 present 文本写入未确认的否认", message)
        self.assertIn("改记 absent", message)
        self.assertIn("删去引文不支持的否认结论", message)
        result = self.propose(raw, [fact("back_details", raw, raw, "present")])
        self.assertEqual(result[0]["value"], raw)

    def test_qualifier_loss_names_field_and_fix(self):
        raw = "昨晚用过药物甲，用量不清楚"
        with self.assertRaises(ProposalError) as caught:
            self.propose(
                raw, [fact("medications_taken", "用过药物甲", raw, "present")]
            )
        message = str(caught.exception)
        self.assertIn("medications_taken", message)
        self.assertIn("不确定限定", message)
        self.assertIn("保留原话限定词", message)
        self.assertIn("unknown", message)

    def test_batch_reports_per_item_verdicts(self):
        raw = "32岁。没有吃药。腰扭伤了，大小便都正常，会阴不麻，也没发烧"
        items = [
            fact("age_years", 32, "32岁"),
            fact("medications_taken", None, "没有吃药", "absent"),
            fact(
                "back_details",
                "腰扭伤，无发热",
                "腰扭伤了，大小便都正常，会阴不麻，也没发烧",
                "present",
            ),
        ]
        with self.assertRaises(ProposalError) as caught:
            self.propose(raw, items)
        message = str(caught.exception)
        self.assertIn("通过 2 项", message)
        self.assertIn("未通过 1 项", message)
        self.assertIn("「age_years」✓", message)
        self.assertIn("「medications_taken」✓", message)
        self.assertIn("「back_details」✗", message)
        self.assertIn("不能借 present 文本写入未确认的否认", message)
        verdicts = caught.exception.verdicts
        self.assertEqual([v["ok"] for v in verdicts], [True, True, False])
        self.assertEqual(verdicts[2]["key"], "back_details")
        self.assertIn("guidance", verdicts[2])
        self.assertIn("reason", verdicts[2])

    def test_validate_command_reports_missing_and_extra_arguments(self):
        with self.assertRaises(ProposalError) as caught:
            validate_command({"tool": "record_facts", "arguments": {"factz": []}})
        message = str(caught.exception)
        self.assertIn("缺少参数：facts", message)
        self.assertIn("未注册的参数：factz", message)
        with self.assertRaises(ProposalError) as caught:
            validate_command({"tool": "nope", "arguments": {}})
        self.assertIn("工具未注册", str(caught.exception))


class CrossTurnCorrectionQuoteTests(unittest.TestCase):
    """Case 9: a correction merging new info may quote the fact's source turn."""

    def setUp(self):
        self.content = ContentRepository()
        self.state = CaseState("synthetic")
        self.state.transcript = [
            {"role": "user", "content": "这三天总是头晕，站起来明显，没有耳鸣"},
            {"role": "assistant", "content": "每次晕多久？"},
            {"role": "user", "content": "昨天吃了头孢"},
        ]
        self.state.record(
            Fact(
                "dizzy_details",
                "头晕三天，无耳鸣",
                Assertion.PRESENT,
                Source.PATIENT,
                "turn-1",
                evidence="没有耳鸣",
            )
        )

    def test_correction_may_quote_the_facts_own_source_turn(self):
        result = validate_proposals(
            self.content,
            self.state,
            "每次转头晕几十秒就好",
            [
                fact(
                    "dizzy_details",
                    "头晕三天，每次发作几十秒，无耳鸣",
                    "没有耳鸣",
                    "present",
                    correction=True,
                )
            ],
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["correction"])
        self.assertEqual(result[0]["evidence"], "没有耳鸣")

    def test_earlier_turn_quote_still_rejected_without_correction(self):
        with self.assertRaises(ProposalError) as caught:
            validate_proposals(
                self.content,
                self.state,
                "每次转头晕几十秒就好",
                [fact("dizzy_details", "头晕三天，无耳鸣", "没有耳鸣", "present")],
            )
        self.assertIn("本轮输入", str(caught.exception))

    def test_correction_cannot_quote_an_unrelated_earlier_turn(self):
        with self.assertRaises(ProposalError):
            validate_proposals(
                self.content,
                self.state,
                "每次转头晕几十秒就好",
                [
                    fact(
                        "dizzy_details",
                        "头晕三天，无耳鸣，昨天吃了头孢",
                        "昨天吃了头孢",
                        "present",
                        correction=True,
                    )
                ],
            )

    def test_correction_cannot_resurrect_a_superseded_facts_turn(self):
        state = CaseState("synthetic")
        state.transcript = [
            {"role": "user", "content": "没有过敏"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "现在有过敏反应，吃了药就起疹子"},
        ]
        state.record(
            Fact(
                "allergies",
                None,
                Assertion.ABSENT,
                Source.PATIENT,
                "turn-1",
                evidence="没有过敏",
            )
        )
        state.record(
            Fact(
                "allergies",
                "有过敏反应",
                Assertion.PRESENT,
                Source.PATIENT,
                "turn-2",
                evidence="现在有过敏反应",
            )
        )
        with self.assertRaises(ProposalError) as caught:
            validate_proposals(
                self.content,
                state,
                "继续核对",
                [fact("allergies", "没有过敏", "没有过敏", "present", correction=True)],
            )
        self.assertIn("本轮输入", str(caught.exception))
        self.assertEqual(state.facts["allergies"].value, "有过敏反应")


if __name__ == "__main__":
    unittest.main()
