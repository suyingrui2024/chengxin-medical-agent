"""P5 close-gaps (round-2 ④-3 / ④-4 / C-07 / C-08 / worry narrowing).

New tests only. No existing test file is modified here.
"""

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.agent_tools import ProposalError, validate_proposals
from medagent.consultation import UserInput
from medagent.domain import Assertion
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService
from medagent.worry import extract_worry_object, is_worry_sentence


def fact(key, value, evidence, assertion="present", correction=False):
    return dict(
        key=key,
        value=value,
        assertion=assertion,
        evidence=evidence,
        correction=correction,
    )


def cmd(tool, **args):
    return dict(tool=tool, arguments=args)


class ScriptBackend:
    name = "scripted-test-only"

    def __init__(self):
        self.commands = []
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages.copy())
        if not self.commands:
            raise RuntimeError("No scripted command")
        return self.commands.pop(0)


class CloseGapsTests(unittest.TestCase):
    def setUp(self):
        self.backend = ScriptBackend()
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=self.backend,
        )
        self.state, self.opening = self.engine.create_session()

    def submit(self, text, choice=None, expected=None):
        return self.engine.submit(
            self.state.session_id, UserInput(text, choice, expected)
        )

    # -- ④-3: worry sentence filed as patient_goal is diverted --------
    def test_worry_sentence_as_goal_answer_goes_to_worry_not_goal(self):
        # The user text itself carries no worry (so _capture_worry does
        # not pre-fill patient_worry); the worry arrives only as the
        # model's proposed patient_goal value.
        self.state.active_question = "patient_goal"
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("patient_goal", "我是不是得了肺炎？", "想咨询一下"),
                ],
            ),
            cmd("ask_question", question_id="patient_goal"),
        ]
        reply = self.submit("想咨询一下")
        self.assertEqual(reply.question_id, "patient_goal")
        self.assertNotIn("patient_goal", self.state.facts)
        worry = self.state.facts.get("patient_worry")
        self.assertIsNotNone(worry)
        self.assertEqual(worry.value, "肺炎")
        self.assertEqual(worry.assertion, Assertion.PRESENT)
        # The real goal was not marked answered: it stays active so it
        # is asked again instead of being closed over.
        self.assertEqual(self.state.active_question, "patient_goal")
        kinds = [
            e.event_type
            for e in self.events.for_session(self.state.session_id)
        ]
        self.assertIn("goal.worry_diverted", kinds)

    # -- ④-4: urgent close after this round's facts land ---------------
    def test_urgent_close_lands_closing_round_facts(self):
        self.backend.commands = [
            cmd("record_facts", facts=[fact("severity", 9, "9分")]),
        ]
        reply = self.submit("难受程度9分")
        self.assertNotEqual(reply.kind, "urgent")
        kinds = [
            e.event_type
            for e in self.events.for_session(self.state.session_id)
        ]
        self.assertIn("triage.urgent_deferred", kinds)
        # Closing round: the associated-symptoms + goal answers must be
        # in the final state, not lost to a pre-record urgent close.
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("associated_symptoms", "咳嗽", "咳嗽"),
                    fact("patient_goal", "想知道要不要紧", "想知道要不要紧"),
                ],
            ),
        ]
        reply = self.submit("还有点咳嗽，想知道要不要紧")
        self.assertEqual(reply.kind, "urgent")
        self.assertEqual(self.state.get("associated_symptoms"), "咳嗽")
        self.assertEqual(self.state.get("patient_goal"), "想知道要不要紧")
        self.assertIn("咳嗽", reply.message)

    # -- C-07/C-08: denial-round chief facts can land ------------------
    def test_denial_round_records_chief_facts_instead_of_deadlocking(self):
        chief_text = (
            "我自己咨询。我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心。"
            "大概3天前开始的。"
        )
        reply = self.submit(chief_text)
        self.assertEqual(reply.kind, "clarification")
        self.assertEqual(self.state.facts, {})
        self.backend.commands = [
            cmd(
                "record_facts",
                facts=[
                    fact("consult_for", "self", "我自己"),
                    fact("chief_complaint", "胸口一阵阵刺痛", "胸口一阵阵刺痛"),
                    fact("onset", "3天前", "3天前"),
                    fact("associated_symptoms", "反酸烧心", "反酸烧心"),
                ],
            ),
            cmd("ask_question", question_id="sex"),
            cmd("ask_question", question_id="age"),
            cmd("ask_question", question_id="emergency_screen"),
        ]
        reply = self.submit("没有，我没有这种情况。", "deny_emergency_clarification")
        self.assertEqual(reply.kind, "question")
        self.assertEqual(self.state.get("consult_for"), "self")
        self.assertEqual(self.state.get("chief_complaint"), "胸口一阵阵刺痛")
        self.assertEqual(self.state.get("onset"), "3天前")
        self.assertEqual(self.state.get("associated_symptoms"), "反酸烧心")
        self.assertNotIn(
            "emergency_clarification", self.state.runtime_snapshot
        )

    def test_stale_first_turn_evidence_still_rejected_without_denial(self):
        # No denial marker: quoting another turn's text stays invalid.
        with self.assertRaises(ProposalError):
            validate_proposals(
                self.engine.content,
                self.state,
                "没有，我没有这种情况。",
                [
                    fact(
                        "chief_complaint",
                        "胸口一阵阵刺痛",
                        "胸口一阵阵刺痛",
                    ),
                ],
            )

    def test_denial_marker_accepts_chief_quote_only_on_denial_turn(self):
        chief_text = "我胸口一阵阵刺痛，按着疼，吃完饭还反酸烧心。"
        denial_text = "没有，我没有这种情况。"
        self.state.runtime_snapshot["emergency_clarification_denied"] = {
            "text": chief_text,
            "for_input": denial_text,
        }
        accepted = validate_proposals(
            self.engine.content,
            self.state,
            denial_text,
            [fact("chief_complaint", "胸口一阵阵刺痛", "胸口一阵阵刺痛")],
        )
        self.assertEqual([item["key"] for item in accepted], ["chief_complaint"])
        # Same marker, a later turn's input: the exception is inert.
        with self.assertRaises(ProposalError):
            validate_proposals(
                self.engine.content,
                self.state,
                "我头有点晕",
                [fact("chief_complaint", "胸口一阵阵刺痛", "胸口一阵阵刺痛")],
            )

    # -- worry.py: treatment questions are not disease worries ---------
    def test_treatment_questions_are_not_disease_worries(self):
        for text in (
            "是不是需要做复位治疗？",
            "是不是要做手术？",
            "是不是要用药？",
            "是不是可以滴眼药水？",
            "是不是要吃止痛药？",
            "是不是要吃退烧药？",
        ):
            self.assertFalse(is_worry_sentence(text), text)
            self.assertIsNone(extract_worry_object(text))

    def test_disease_worries_still_recognised(self):
        for text, obj in (
            ("是不是心脏病？", "心脏病"),
            ("是不是心绞痛？", "心绞痛"),
            ("是不是肺炎？", "肺炎"),
        ):
            self.assertTrue(is_worry_sentence(text), text)
            self.assertEqual(extract_worry_object(text), obj)


if __name__ == "__main__":
    unittest.main()
