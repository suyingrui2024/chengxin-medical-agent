"""Conclusion sheet (docs/问诊结论单设计文档.md): composer unit behavior,
serialization through both engines, emergency exclusion, abstention, and
the return_now_if drift guard against the summary payload."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from medagent import conclusion as conclusion_module
from medagent.agent import AdaptiveConsultationEngine
from medagent.conclusion import compose_conclusion, spoken_close_line
from medagent.consultation import UserInput
from medagent.domain import (
    Assertion,
    CaseState,
    Fact,
    Phase,
    RiskAssessment,
    RiskLevel,
    Source,
)
from medagent.events import EventStore
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.runtime import build_runtime
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


def closed_state(chief="发烧两天", risk=RiskLevel.ROUTINE, reason="confirmed_by_user"):
    state = CaseState(session_id="unit-test")
    state.phase = Phase.CLOSED
    state.closed_reason = reason
    state.risk = RiskAssessment(risk)
    state.record(
        Fact(
            key="chief_complaint",
            value=chief,
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=chief,
        )
    )
    state.transcript.append({"role": "user", "content": chief})
    return state


class ComposerUnitTests(unittest.TestCase):
    def test_open_session_gets_no_sheet(self):
        state = closed_state()
        state.phase = Phase.SUMMARY
        self.assertIsNone(compose_conclusion(state))

    def test_emergency_route_gets_no_sheet(self):
        state = closed_state(risk=RiskLevel.EMERGENCY, reason="emergency_route")
        self.assertIsNone(compose_conclusion(state))

    def test_uncovered_complaint_abstains_but_still_dispositions(self):
        state = closed_state(chief="膝盖疼一周")
        sheet = compose_conclusion(state)
        self.assertIsNotNone(sheet)
        self.assertTrue(sheet["directions_abstained"])
        self.assertEqual(sheet["directions"], [])
        self.assertIn("不足以给出可能的方向", sheet["abstain_text"])
        self.assertIn(sheet["disposition"]["level"], ("self_care", "routine_visit"))
        self.assertTrue(sheet["self_care"]["do"])

    def test_fever_case_yields_flu_direction_with_verbatim_quote(self):
        sheet = compose_conclusion(closed_state())
        self.assertFalse(sheet["directions_abstained"])
        ids = [d["hypothesis_id"] for d in sheet["directions"]]
        self.assertIn("influenza_like", ids)
        flu = sheet["directions"][ids.index("influenza_like")]
        self.assertEqual(flu["patient_label"], "流感样疾病")
        self.assertEqual(flu["certainty"], "possible")
        self.assertIn("发烧", flu["support_quotes"])

    def test_missing_patient_label_never_reaches_patient_text(self):
        original = conclusion_module._patient_labels
        conclusion_module._patient_labels = lambda: {}
        try:
            sheet = compose_conclusion(closed_state())
        finally:
            conclusion_module._patient_labels = original
        for direction in sheet["directions"]:
            self.assertIsNone(direction["patient_label"])

    def test_revision_increments_on_recompose(self):
        state = closed_state()
        first = compose_conclusion(state)
        state.conclusion = first
        second = compose_conclusion(state)
        self.assertEqual(second["revision"], first["revision"] + 1)

    def test_templates_and_constants_pass_banned_lint(self):
        for template in conclusion_module.CERTAINTY_TEMPLATES.values():
            rendered = template.format(label="某种情况")
            for phrase in conclusion_module.BANNED_PHRASES:
                self.assertNotIn(phrase, rendered)
        for phrase in conclusion_module.BANNED_PHRASES:
            self.assertNotIn(phrase, conclusion_module.ABSTAIN_TEXT)

    def test_return_now_if_constant_matches_summary_payload(self):
        # Drift guard: the composer's fallback list must equal the list the
        # summary payload ships (the payload stays the preferred source).
        host = build_runtime()
        context = host.start()
        engine = context.services.require("consultation")
        try:
            state, reply = engine.create_session()
            answers = {
                "consult_for": ("我自己", "self"),
                "age": ("32岁", None),
                "chief_complaint": ("腹痛两小时", None),
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
            for _ in range(25):
                if reply.kind == "summary":
                    break
                if reply.kind == "self_check":
                    reply = engine.submit(
                        state.session_id, UserInput("暂时跳过", "skip")
                    )
                    continue
                text, choice = answers.get(reply.question_id, ("没有", None))
                reply = engine.submit(state.session_id, UserInput(text, choice))
            self.assertEqual(reply.kind, "summary")
            self.assertEqual(
                reply.summary["return_now_if"],
                conclusion_module.RETURN_NOW_IF,
            )
        finally:
            host.stop()


ANSWERS = {
    "consult_for": ("我自己", "self"),
    "age": ("32岁", None),
    "chief_complaint": ("咳嗽、发烧两天，嗓子疼", None),
    "emergency_screen": ("都没有", "no"),
    "onset": ("两天前开始的", None),
    "trajectory": ("变化不大", "stable"),
    "severity": ("4分", None),
    "associated_symptoms": ("没有", None),
    "medications_taken": ("没有", None),
    "allergies": ("不知道", None),
    "relevant_history": ("没有", None),
    "patient_goal": ("想整理信息去就医", None),
}


class DeterministicConclusionTests(unittest.TestCase):
    def setUp(self):
        self.host = build_runtime()
        self.context = self.host.start()
        self.engine = self.context.services.require("consultation")
        self.events = self.context.services.require("events")
        self.state, self.reply = self.engine.create_session()

    def tearDown(self):
        self.host.stop()

    def _answer(self, text, choice=None):
        return self.engine.submit(self.state.session_id, UserInput(text, choice))

    def _to_summary(self, overrides=None):
        answers = {**ANSWERS, **(overrides or {})}
        reply = self.reply
        for _ in range(30):
            if reply.kind == "summary":
                return reply
            if reply.kind == "self_check":
                reply = self._answer("暂时跳过", "skip")
                continue
            text, choice = answers.get(reply.question_id, ("没有", None))
            reply = self._answer(text, choice)
        raise AssertionError(f"no summary; last={reply.kind}")

    def test_confirmed_close_carries_sheet_everywhere(self):
        self._to_summary()
        final = self._answer("确认", "confirm_summary")
        self.assertEqual(final.kind, "closed")
        sheet = final.conclusion
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet["tier"], "routine")
        self.assertEqual(sheet["follow_up"]["kind"], "routine_checkin")
        self.assertEqual(
            sheet["disposition"]["window_hours"],
            sheet["follow_up"]["due_in_hours"],
        )
        payload = final.to_dict()
        self.assertEqual(payload["conclusion"]["tier"], "routine")
        types = [b["type"] for b in payload["presentation"]["blocks"]]
        self.assertLess(types.index("conclusion"), types.index("summary"))
        self.assertLess(types.index("summary"), types.index("follow_up"))
        self.assertEqual(self.state.to_dict()["conclusion"]["tier"], "routine")
        kinds = [e.event_type for e in self.events.for_session(self.state.session_id)]
        self.assertIn("conclusion.generated", kinds)

    def test_urgent_close_sheet_is_urgent_tier(self):
        self._answer("我自己", "self")
        self._answer("男", "male")
        self._answer("32岁")
        self._answer("咳嗽发烧两天")
        self._answer("都没有", "no")
        self._answer("两天前开始")
        reply = self._answer("在加重", "worsening")
        # Batch A: single-clue trajectory no longer closes urgently at
        # once; the controller defers and asks the remaining questions.
        self.assertEqual(reply.kind, "question")
        self.assertIsNone(reply.conclusion)
        kinds = [e.event_type for e in self.events.for_session(self.state.session_id)]
        self.assertIn("triage.urgent_deferred", kinds)
        # Drive the remaining minimum-fact questions; the deferred trigger
        # closes urgently once severity, associated symptoms and the
        # patient goal are on file, with the same final sheet values.
        self._answer("4分")
        self._answer("没有")
        self._answer("两天前开始")
        self._answer("没有")
        self._answer("没有")
        self._answer("没有")
        self._answer("暂时跳过", "skip")
        self._answer("没有")
        self._answer("不知道")
        self._answer("没有")
        reply = self._answer("想整理信息去就医")
        self.assertEqual(reply.kind, "urgent")
        sheet = reply.conclusion
        self.assertIsNotNone(sheet)
        self.assertEqual(sheet["tier"], "urgent")
        self.assertEqual(sheet["disposition"]["level"], "urgent_today")
        self.assertEqual(sheet["disposition"]["window_hours"], 24)

    def test_emergency_close_has_no_sheet(self):
        self._answer("我自己", "self")
        reply = self._answer("他现在叫不醒")
        self.assertEqual(reply.kind, "emergency")
        self.assertIsNone(reply.conclusion)
        self.assertFalse(self.state.conclusion)

    def test_confirmed_close_speaks_disposition_before_boilerplate(self):
        self._to_summary()
        final = self._answer("确认", "confirm_summary")
        self.assertEqual(final.kind, "closed")
        line = spoken_close_line(final.conclusion)
        self.assertTrue(line)
        # The spoken disposition (with its first safety-net alert) leads;
        # the administrative boilerplate is preserved verbatim after it.
        self.assertEqual(
            final.message,
            line + "你已确认本次摘要，会谈现已结束。可以回看记录；情况变化时请重新评估。",
        )
        sheet = final.conclusion
        self.assertIn(f"{sheet['disposition']['window_hours']} 小时内", final.message)
        self.assertIn(sheet["return_now_if"][0], final.message)
        # The recorded transcript carries the spoken line too, so a replay
        # of the chat shows the same closing the patient saw.
        self.assertEqual(self.state.transcript[-1]["content"], final.message)

    def test_confirmed_close_without_sheet_keeps_plain_boilerplate(self):
        with mock.patch.dict(os.environ, {"MEDAGENT_CONCLUSION_SHEET": "off"}):
            self._to_summary()
            final = self._answer("确认", "confirm_summary")
        self.assertEqual(final.kind, "closed")
        self.assertIsNone(final.conclusion)
        self.assertEqual(
            final.message,
            "你已确认本次摘要，会谈现已结束。可以回看记录；情况变化时请重新评估。",
        )

    def test_post_close_reply_does_not_repeat_spoken_line(self):
        self._to_summary()
        self._answer("确认", "confirm_summary")
        guard = self._answer("还在吗")
        self.assertEqual(guard.kind, "closed")
        self.assertEqual(
            guard.message,
            "这次问诊已经结束。如情况有变化，请开始一次新的问诊；出现紧急情况请立即拨打 120。",
        )

    def test_emergency_close_names_signal_and_gives_waiting_guidance(self):
        self._answer("我自己", "self")
        reply = self._answer("他现在叫不醒")
        self.assertEqual(reply.kind, "emergency")
        self.assertTrue(self.state.risk.triggers)
        for trigger in self.state.risk.triggers:
            self.assertIn(trigger, reply.message)
        self.assertIn("请现在拨打 120", reply.message)
        self.assertEqual(reply.message.count("拨打 120"), 1)
        self.assertIn("保持安静休息", reply.message)
        self.assertIn("不要自行开车", reply.message)
        self.assertIn("陪同", reply.message)
        self.assertTrue(reply.message.endswith("不要继续等待本次对话。"))
        self.assertEqual(
            reply.summary["triggers"], sorted(set(self.state.risk.triggers))
        )


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


class AdaptiveConclusionTests(unittest.TestCase):
    def test_confirm_close_schedules_sheet_at_parity(self):
        backend = _ScriptBackend()
        events = EventStore()
        engine = AdaptiveConsultationEngine(
            content=ContentRepository(),
            triage=TriageService(),
            self_checks=SelfCheckService(),
            events=events,
            ranker=OrderedQuestionRanker(),
            backend=backend,
        )
        state, _opening = engine.create_session()
        facts = [
            _fact("consult_for", "self", "我自己"),
            _fact("age_years", 32, "32岁"),
            _fact("chief_complaint", "发烧两天", "发烧两天"),
            _fact("emergency_screen", None, "没有危险信号", "absent"),
        ]
        known = {f["key"] for f in facts}
        for q in engine.content.questions:
            if q.fact_key not in known and engine.content.question_applies(
                q, "发烧两天"
            ):
                facts.append(_fact(q.fact_key, None, "不知道", "unknown"))
        backend.commands = [
            _cmd("record_facts", facts=facts),
            _cmd("review_summary"),
        ]
        reply = engine.submit(
            state.session_id, UserInput("我自己32岁，发烧两天，没有危险信号，其它不知道")
        )
        self.assertEqual(reply.kind, "summary")
        final = engine.submit(
            state.session_id, UserInput("摘要准确", "confirm_summary", reply.action_id)
        )
        self.assertEqual(final.kind, "closed")
        self.assertIsNotNone(final.conclusion)
        self.assertEqual(final.conclusion["tier"], "routine")
        self.assertEqual(state.conclusion["tier"], "routine")
        self.assertEqual(
            final.to_dict()["conclusion"]["disposition"]["level"],
            final.conclusion["disposition"]["level"],
        )
        kinds = [e.event_type for e in events.for_session(state.session_id)]
        self.assertIn("conclusion.generated", kinds)


if __name__ == "__main__":
    unittest.main()
