"""Agent-level tool-call tests.

The question here is not "does a scale compute" (engine tests) nor "does
the card render" (UI tests), but "when the model emits a tool call, does
the controller honour it correctly, and does it refuse a bad one".

Each case carries a recorded model decision. The decisions came from a
capable model given the real 34 KB system prompt and the real turn
context, so they are evidence of what a model does — but they are
recorded, not re-derived. What this suite actually protects is the
controller and the guard rails:

  * a valid ``run_capability`` produces the right card with the right
    number of items;
  * a capability the patient never saw is refused;
  * a call whose evidence does not contain the capability alias is
    refused;
  * answers pre-filled by the model are discarded, because only the
    patient may supply them.

Re-running the model against these cases is a separate, deliberate act:
``scripts/evaluation/medical/evaluate_capability_selection.py``.
"""

import json
import unittest
from pathlib import Path

from medagent.consultation import UserInput
from medagent.events import EventStore
from medagent.agent import AdaptiveConsultationEngine
from medagent.extensions.builtin import load_registry
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "agent-tool-calls.json"


class ScriptedBackend:
    name = "fixture"

    def __init__(self, commands):
        self.commands = list(commands)

    def decide(self, identity, messages):
        return self.commands.pop(0)


def build_engine(commands):
    registry = load_registry()
    return AdaptiveConsultationEngine(
        content=ContentRepository(registry),
        triage=TriageService(),
        self_checks=SelfCheckService(registry),
        events=EventStore(),
        ranker=OrderedQuestionRanker(),
        backend=ScriptedBackend(commands),
    )


class AgentToolCallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

    def run_case(self, case, commands):
        engine = build_engine(commands)
        state, _ = engine.create_session()
        return state, engine.submit(state.session_id, UserInput(case["utterance"]))

    def test_valid_calls_build_the_declared_card(self):
        for case in self.cases:
            expect = case["expect"]
            if expect["kind"] != "calculation_input":
                continue
            decision = case["decision"]
            with self.subTest(case["id"]):
                state, reply = self.run_case(
                    case,
                    [{"tool": decision["tool"], "arguments": decision["arguments"]}],
                )
                self.assertEqual(reply.kind, "calculation_input", reply.message)
                card = reply.calculation_input
                self.assertEqual(card["capability_id"], expect["capability"])
                size = len(card.get("items") or card.get("inputs") or [])
                self.assertEqual(size, expect.get("items") or expect.get("inputs"))
                # A score is a record, never a clinical fact.
                self.assertEqual(state.facts, {})

    def test_model_prefilled_answers_are_discarded(self):
        case = next(c for c in self.cases if c["id"] == "adv-03")
        decision = case["decision"]
        _, reply = self.run_case(
            case, [{"tool": decision["tool"], "arguments": decision["arguments"]}]
        )
        self.assertEqual(reply.kind, "calculation_input")
        # Only the patient may answer: the model's q1 must not survive.
        self.assertEqual(reply.calculation_input.get("input"), {})

    def test_clinician_only_capability_is_refused(self):
        case = next(c for c in self.cases if c["id"] == "adv-01")
        decision = case["decision"]
        _, reply = self.run_case(
            case, [{"tool": decision["tool"], "arguments": decision["arguments"]}]
        )
        self.assertNotEqual(reply.kind, "calculation_input", "医生专用能力不应生成患者卡片")

    def test_call_without_alias_in_evidence_is_refused(self):
        case = next(c for c in self.cases if c["id"] == "adv-02")
        decision = case["decision"]
        _, reply = self.run_case(
            case, [{"tool": decision["tool"], "arguments": decision["arguments"]}]
        )
        self.assertNotEqual(reply.kind, "calculation_input", "引文缺少别名时不应出卡")

    def test_declined_cases_record_a_reason(self):
        # The model chose not to call anything. That decision is evidence,
        # not an assertion about the controller; keep it visible so a
        # future re-record cannot silently turn a refusal into a call.
        for case in self.cases:
            if case["expect"]["kind"] != "declined":
                continue
            with self.subTest(case["id"]):
                self.assertIsNone(case["decision"]["tool"])
                self.assertTrue(
                    (case["decision"].get("reply") or "").strip(),
                    "拒绝调用时应给出对患者的说明",
                )


class ToolCallFixtureTests(unittest.TestCase):
    """The fixture is hand-maintained after a re-record, so it needs a
    shape check of its own — a typo in an expected id would make the
    suite pass against the wrong capability."""

    def setUp(self):
        self.cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
        self.known = {item["id"] for item in load_registry().descriptions()}

    def test_ids_are_unique(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_capabilities_match_the_decision(self):
        for case in self.cases:
            expect = case["expect"]
            if expect["kind"] != "calculation_input":
                continue
            with self.subTest(case["id"]):
                self.assertEqual(case["decision"]["arguments"]["id"], expect["capability"])

    def test_referenced_capabilities_exist(self):
        for case in self.cases:
            decision = case["decision"]
            identity = (decision.get("arguments") or {}).get("id")
            if identity is None:
                continue
            with self.subTest(case["id"]):
                self.assertIn(identity, self.known)


if __name__ == "__main__":
    unittest.main()


MULTITURN = ROOT / "tests" / "fixtures" / "agent-multiturn.json"


class AgentMultiturnTests(unittest.TestCase):
    """The turn the one-shot fixtures cannot reach: what the model does
    *after* it sees a score.

    Both decisions here are recorded from a model that received the real
    payload (including ``calculation_drafts`` with the confirmed answers).
    The assertions protect the invariants that matter once a number
    exists in the conversation: it must not become a fact, it must not be
    restated as a verdict, and it must not trigger a second calculation.
    """

    def setUp(self):
        self.turns = json.loads(MULTITURN.read_text(encoding="utf-8"))["turns"]

    def run_conversation(self):
        engine = build_engine(
            [
                dict(self.turns["turn1_decision"]),
                dict(self.turns["turn3_decision"]),
            ]
        )
        state, _ = engine.create_session()
        draft = engine.submit(
            state.session_id, UserInput(self.turns["turn1_utterance"])
        )
        self.assertEqual(draft.kind, "calculation_input")
        result = engine.submit(
            state.session_id,
            UserInput(
                "提交",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": self.turns["answers"],
                },
            ),
        )
        self.assertEqual(result.kind, "calculation_result")
        followup = engine.submit(
            state.session_id, UserInput(self.turns["turn3_utterance"])
        )
        return state, result, followup

    def test_score_is_computed_as_recorded(self):
        _, result, _ = self.run_conversation()
        self.assertEqual(result.calculation_result["data"]["value"], self.turns["expected_score"])

    def test_score_never_becomes_a_fact(self):
        state, _, _ = self.run_conversation()
        self.assertEqual(state.facts, {})
        self.assertEqual(len(state.calculations), 1)

    def test_followup_does_not_trigger_a_second_calculation(self):
        state, _, _ = self.run_conversation()
        self.assertEqual(len(state.calculations), 1, "追问解读不应再次调用能力")

    def test_followup_does_not_restate_a_diagnosis(self):
        from medagent.conclusion import BANNED_PHRASES

        message = self.turns["turn3_decision"]["arguments"].get("message", "")
        self.assertTrue(message.strip())
        for phrase in BANNED_PHRASES:
            self.assertNotIn(phrase, message, f"追问回答里出现禁用表述：{phrase}")


class PathwayGateTests(unittest.TestCase):
    """The summary gate: explore a triggered pathway, or say why not.

    Every browser fixture answers this gate by abstaining, so without this
    test the rule itself has no coverage at all — a gate that only exists
    in the fixtures' favour is not a gate.

    The chief complaint below triggers the respiratory pathway while its
    detail fields stay unrecorded, which is exactly the state the gate
    guards.
    """

    TEXT = (
        "我自己32岁，男，咳嗽发烧两天，变化不大，影响程度3分，没有其他不适，"
        "上述危险信号都没有。能正常喝水吃饭；过敏不知道；没有用药；"
        "没有相关既往病史。想整理信息去就医。"
    )

    @staticmethod
    def fact(key, value, evidence, assertion="present"):
        return {
            "key": key,
            "value": value,
            "evidence": evidence,
            "assertion": assertion,
        }

    def open_case(self):
        """A case with every blocking field recorded, pathway untouched."""
        engine = build_engine([])
        state, _ = engine.create_session()
        engine._execute(
            state,
            {
                "tool": "record_facts",
                "arguments": {
                    "facts": [
                        self.fact("consult_for", "self", "我自己"),
                        self.fact("sex", "male", "男"),
                        self.fact("age_years", 32, "32岁"),
                        self.fact("chief_complaint", "咳嗽发烧", "咳嗽发烧"),
                        self.fact("onset", "两天", "两天"),
                        self.fact("trajectory", "stable", "变化不大"),
                        self.fact("severity", 3, "影响程度3分"),
                        self.fact("associated_symptoms", None, "没有其他不适", "absent"),
                        self.fact("emergency_screen", None, "上述危险信号都没有", "absent"),
                        self.fact("resp_intake", "能正常喝水吃饭", "能正常喝水吃饭"),
                        self.fact("allergies", None, "过敏不知道", "unknown"),
                        self.fact("medications_taken", None, "没有用药", "absent"),
                        self.fact("relevant_history", None, "没有相关既往病史", "absent"),
                        self.fact("patient_goal", "整理信息去就医", "想整理信息去就医"),
                    ]
                },
            },
            self.TEXT,
            "t1",
        )
        warnings = [
            key
            for key in engine._context(state, self.TEXT)["missing_required"]
            if key.startswith("warning_")
        ]
        if warnings:
            engine._execute(
                state,
                {
                    "tool": "record_facts",
                    "arguments": {
                        "facts": [
                            self.fact(key, None, "上述危险信号都没有", "absent")
                            for key in warnings
                        ]
                    },
                },
                self.TEXT,
                "t1",
            )
        return engine, state

    def test_the_fixture_really_leaves_the_pathway_unexplored(self):
        engine, state = self.open_case()
        pathway = engine._context(state, self.TEXT)["pathway_exploration"]
        self.assertEqual([entry["pathway"] for entry in pathway], ["respiratory"])
        self.assertFalse(pathway[0]["explored"])

    def test_summary_is_refused_while_the_pathway_is_unexplored(self):
        from medagent.agent_tools import ProposalError

        engine, state = self.open_case()
        with self.assertRaises(ProposalError) as raised:
            engine._execute(
                state, {"tool": "review_summary", "arguments": {}}, self.TEXT, "t1"
            )
        self.assertIn("主诉通路尚未探明", str(raised.exception))
        self.assertIn("resp_breathing", str(raised.exception))

    def test_abstaining_lets_the_summary_through(self):
        from medagent.agent_tools import ProposalError

        engine, state = self.open_case()
        try:
            engine._execute(
                state,
                {
                    "tool": "review_summary",
                    "arguments": {
                        "abstain_reason": "该通路与主诉无关，按已记录事实生成结论"
                    },
                },
                self.TEXT,
                "t1",
            )
        except ProposalError as error:  # pragma: no cover - failure path
            self.fail(f"弃权后仍被拒：{error}")

    def test_an_empty_abstain_reason_is_not_an_escape(self):
        from medagent.agent_tools import ProposalError

        engine, state = self.open_case()
        for reason in ("", "   ", "x" * 201):
            with self.subTest(reason=reason):
                with self.assertRaises(ProposalError):
                    engine._execute(
                        state,
                        {
                            "tool": "review_summary",
                            "arguments": {"abstain_reason": reason},
                        },
                        self.TEXT,
                        "t1",
                    )


class AssertionEvidenceTests(unittest.TestCase):
    """Asserting absence from an uncertain answer must not be swallowed.

    `assertion-flow` drives exactly this case in the browser: the patient
    says 「过敏不知道」 and the scripted model answers `absent`. The engine
    is supposed to refuse it and tell the model to use `unknown/null`.

    Today it does neither. The batch is accepted, nine facts land, and the
    three uncertain ones vanish — no error, no event, and the next context
    simply lists them as missing again. The model is never told that what
    it said was wrong, so it can keep asserting 「无过敏史」 in prose while
    the record says nothing.

    Kept as an expected failure rather than deleted: the moment the engine
    starts refusing (the right behaviour), this test turns red and says so.
    """

    TEXT = (
        "我自己，32岁，手臂不舒服，从昨天开始，变化不大，影响程度3分，"
        "没有其他不适，上述危险信号都没有。过敏不知道；是否用过药不确定；"
        "病史记不清。这次想整理就医记录。"
    )

    @unittest.expectedFailure
    def test_uncertain_answers_cannot_be_recorded_as_absent(self):
        from medagent.agent_tools import ProposalError

        engine = build_engine([])
        state, _ = engine.create_session()
        facts = [
            {
                "key": "chief_complaint",
                "value": "手臂不舒服",
                "evidence": "手臂不舒服",
                "assertion": "present",
            },
            {
                "key": "allergies",
                "value": None,
                "evidence": "过敏不知道",
                "assertion": "absent",
            },
        ]
        with self.assertRaises(ProposalError):
            engine._execute(
                state, {"tool": "record_facts", "arguments": {"facts": facts}}, self.TEXT, "t1"
            )

    def test_the_same_answers_are_accepted_as_unknown(self):
        engine = build_engine([])
        state, _ = engine.create_session()
        facts = [
            {
                "key": "chief_complaint",
                "value": "手臂不舒服",
                "evidence": "手臂不舒服",
                "assertion": "present",
            },
            {
                "key": "allergies",
                "value": None,
                "evidence": "过敏不知道",
                "assertion": "unknown",
            },
        ]
        engine._execute(
            state, {"tool": "record_facts", "arguments": {"facts": facts}}, self.TEXT, "t1"
        )
        self.assertIn("allergies", state.facts)
