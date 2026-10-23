"""Closed-consultation outcome surface: the follow-up plan derived at
close must survive AgentReply/CaseState serialization, appear in the
patient presentation blocks, and be scheduled identically by the
adaptive engine (whose confirm fast path used to bypass scheduling).
"""
from __future__ import annotations

import unittest

from medagent.agent import AdaptiveConsultationEngine
from medagent.consultation import UserInput
from medagent.domain import Phase
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService

ANSWERS = {
    "consult_for": ("我自己", "self"),
    "age": ("32岁", None),
    "chief_complaint": ("腹痛两小时", None),
    "gi_details": ("肚脐周围隐痛", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两小时前突然出现", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}


class DeterministicClosedOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def _answer(self, text, choice=None):
        return self.engine.submit(self.state.session_id, UserInput(text, choice))

    def _close_confirmed(self):
        reply = self.reply
        for _ in range(25):
            if reply.kind == "summary":
                break
            if reply.kind == "self_check":
                reply = self._answer("暂时跳过", "skip")
                continue
            text, choice = ANSWERS[reply.question_id]
            reply = self._answer(text, choice)
        self.assertEqual(reply.kind, "summary")
        return self._answer("确认", "confirm_summary")

    def test_follow_up_survives_reply_serialization(self):
        reply = self._close_confirmed()
        payload = reply.to_dict()
        self.assertEqual(payload["follow_up"]["kind"], "routine_checkin")
        self.assertEqual(payload["follow_up"]["due_in_hours"], 48)

    def test_follow_up_block_follows_summary_in_presentation(self):
        reply = self._close_confirmed()
        blocks = reply.to_dict()["presentation"]["blocks"]
        types = [block["type"] for block in blocks]
        self.assertIn("summary", types)
        self.assertIn("follow_up", types)
        self.assertLess(types.index("summary"), types.index("follow_up"))
        follow = blocks[types.index("follow_up")]
        self.assertEqual(follow["data"]["kind"], "routine_checkin")

    def test_follow_up_in_state_projection(self):
        self._close_confirmed()
        projected = self.state.to_dict()
        self.assertEqual(projected["follow_up"]["kind"], "routine_checkin")


def _fact(key, value, evidence, assertion="present"):
    return dict(
        key=key, value=value, assertion=assertion,
        evidence=evidence, correction=False,
    )


def _cmd(tool, **args):
    return dict(tool=tool, arguments=args)


class _ScriptBackend:
    name = "scripted-test-only"

    def __init__(self):
        self.commands = []
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages.copy())
        if not self.commands:
            raise RuntimeError("No scripted command")
        return self.commands.pop(0)


class AdaptiveClosedOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.backend = _ScriptBackend()
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

    def test_confirm_close_schedules_follow_up_like_deterministic(self):
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("age_years", 32, "32岁"),
            _fact("chief_complaint", "腹痛", "腹痛"),
            _fact("emergency_screen", None, "没有危险信号", "absent"),
        ]
        known = {f["key"] for f in facts}
        for q in self.engine.content.questions:
            if q.fact_key not in known and self.engine.content.question_applies(
                q, "腹痛"
            ):
                facts.append(_fact(q.fact_key, None, "不知道", "unknown"))
        self.backend.commands = [
            _cmd("record_facts", facts=facts),
            _cmd("review_summary"),
        ]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("我自己32岁腹痛，没有危险信号，其它不知道"),
        )
        self.assertEqual(reply.kind, "summary")
        final = self.engine.submit(
            self.state.session_id,
            UserInput("摘要准确", "confirm_summary", reply.action_id),
        )
        self.assertEqual(final.kind, "closed")
        self.assertEqual(self.state.phase, Phase.CLOSED)
        self.assertEqual(self.state.follow_up["kind"], "routine_checkin")
        self.assertEqual(self.state.follow_up["due_in_hours"], 48)
        self.assertEqual(final.follow_up["kind"], "routine_checkin")
        payload = final.to_dict()
        self.assertEqual(payload["follow_up"]["kind"], "routine_checkin")
        types = [b["type"] for b in payload["presentation"]["blocks"]]
        self.assertIn("follow_up", types)
        kinds = [e.event_type for e in self.events.for_session(self.state.session_id)]
        self.assertIn("followup.scheduled", kinds)


if __name__ == "__main__":
    unittest.main()
