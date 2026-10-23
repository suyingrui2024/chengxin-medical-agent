"""Confirmed locations can be revised without losing their history or host question."""
from copy import deepcopy
import unittest

from medagent.consultation import StaleActionError, UserInput
from medagent.domain import Phase
from tests import test_location_answers as fixture
from tests.test_agent import cmd


class LocationRevisionTests(unittest.TestCase):
    def setUp(self):
        self.flow = fixture.LocationAnswerTests()
        self.flow.setUp()
        self.engine, self.state, self.backend = (
            self.flow.engine,
            self.flow.state,
            self.flow.backend,
        )
        offered = self.flow.offer()
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.original_reply = self.flow.confirm(offered, self.flow.payload(offered))
        self.original = deepcopy(self.state.facts["symptom_location"].to_dict())

    def start(self, reply=None, fact_id=None):
        reply = reply or self.original_reply
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "修改定位",
                f"revise_location:{fact_id or self.original['fact_id']}",
                reply.action_id,
            ),
        )

    def cancel(self, reply):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "取消修改，保留原记录",
                reply.task["location_revision"]["cancel_choice"],
                reply.action_id,
            ),
        )

    def test_revise_and_confirm_keeps_history_and_resumes_without_an_extra_model_call(
        self,
    ):
        calls = len(self.backend.calls)
        self.assertEqual(
            self.original_reply.location_revision_action["fact_id"],
            self.original["fact_id"],
        )
        reply = self.start()
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)
        self.assertEqual(reply.task["answer_spec"]["prefill"]["origin"], "record")
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        resumed = self.flow.confirm(reply, payload)
        updated = self.state.facts["symptom_location"]
        self.assertEqual(updated.supersedes, self.original["fact_id"])
        self.assertEqual(updated.structured_input["answers"]["side"]["value"], "left")
        self.assertEqual(
            updated.structured_input["revision_of"], self.original["fact_id"]
        )
        self.assertIn("左右：左侧", updated.evidence)
        self.assertNotEqual(updated.turn_id, self.original["turn_id"])
        self.assertEqual(updated.quality.value, "not_assessed")
        self.assertEqual(resumed.question_id, self.original_reply.question_id)
        self.assertEqual(resumed.message, self.original_reply.message)
        self.assertNotEqual(resumed.action_id, self.original_reply.action_id)
        self.assertEqual(len(self.backend.calls), calls)

    def test_cancel_keeps_the_original_fact_and_invalidates_the_form(self):
        reply = self.start()
        calls = len(self.backend.calls)
        resumed = self.cancel(reply)
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)
        self.assertEqual(resumed.question_id, "onset")
        self.assertEqual(len(self.backend.calls), calls)
        before = deepcopy(self.state.to_dict())
        events = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.flow.confirm(reply, self.flow.payload(reply))
        self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )

    def test_wrong_fact_version_is_rejected_before_journaling(self):
        events = len(self.engine.events.for_session(self.state.session_id))
        before = deepcopy(self.state.to_dict())
        with self.assertRaises(StaleActionError):
            self.start(fact_id="old-fact-id")
        self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )

    def test_skip_cancels_the_revision_instead_of_overwriting_a_known_location(self):
        reply = self.start()
        self.engine.submit(
            self.state.session_id, UserInput("先跳过", "skip", reply.action_id)
        )
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)

    def test_urgent_note_precedes_a_stale_revision_and_preserves_the_old_location(self):
        reply = self.start()
        payload = self.flow.payload(reply)
        payload["note"] = "现在叫不醒了"
        payload["form_id"] = "old-form"
        result = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对定位答案", "confirm_location_answer", "old-action", observation=payload
            ),
        )
        self.assertEqual(result.kind, "emergency")
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)
        self.assertIsNone(self.state.self_check_conversation)

    def test_summary_revision_rebuilds_current_values_and_requires_confirmation(self):
        summary = self.engine._finish(self.state)
        self.engine._record_reply(self.state, summary, "fixture", "Summary setup")
        reply = self.start(summary)
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        calls = len(self.backend.calls)
        updated = self.flow.confirm(reply, payload)
        self.assertEqual(updated.kind, "summary")
        item = next(
            x for x in updated.summary["known"] if x["key"] == "symptom_location"
        )
        self.assertEqual(item["structured_input"]["answers"]["side"]["value"], "left")
        self.assertEqual(self.state.phase, Phase.SUMMARY)
        self.assertIsNone(self.state.closed_reason)
        self.assertEqual(len(self.backend.calls), calls)

    def test_closed_confirmed_record_can_be_cancelled_or_reopened_for_reconfirmation(
        self,
    ):
        summary = self.engine._finish(self.state)
        self.engine._record_reply(self.state, summary, "fixture", "Summary setup")
        closed = self.engine.submit(
            self.state.session_id,
            UserInput("摘要准确", "confirm_summary", summary.action_id),
        )
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")
        reply = self.start(closed)
        cancelled = self.cancel(reply)
        self.assertEqual(self.state.closed_reason, "confirmed_by_user")
        self.assertEqual(cancelled.kind, "closed")
        reply = self.start(cancelled)
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        updated = self.flow.confirm(reply, payload)
        self.assertEqual(updated.kind, "summary")
        self.assertIsNone(self.state.closed_reason)

    def test_other_field_correction_invalidates_the_old_revision_form(self):
        reply = self.start()
        self.engine.correct_fact(
            self.state.session_id,
            "age_years",
            "40岁",
            self.state.facts["age_years"].fact_id,
        )
        self.assertIsNone(self.state.self_check_conversation)
        with self.assertRaises(StaleActionError):
            self.flow.confirm(reply, self.flow.payload(reply))
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)

    def test_new_note_service_retry_does_not_duplicate_the_revised_fact(self):
        reply = self.start()
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        payload["note"] = "昨晚用过药物甲"
        self.backend.commands = [RuntimeError("Synthetic unavailable model")]
        failed = self.flow.confirm(reply, payload)
        self.assertEqual(failed.kind, "service_error")
        saved = deepcopy(self.state.facts["symptom_location"].to_dict())
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.engine.submit(
            self.state.session_id, UserInput("重试这一轮", "retry_turn", failed.action_id)
        )
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), saved)
        self.assertEqual(
            len([f for f in self.state.history if f.key == "symptom_location"]), 2
        )

    def test_model_opens_a_revision_then_only_proposes_the_explicit_changes(self):
        text = "刚才位置选错了，改为左侧太阳穴附近"
        self.backend.commands = [
            cmd("revise_self_check", task_id="body_location", evidence=text),
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence=text,
                input={"side": "left", "area": {"kind": "preset", "value": "temple"}},
            ),
        ]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(text, expected_action_id=self.original_reply.action_id),
        )
        prefill = reply.task["answer_spec"]["prefill"]
        self.assertEqual(prefill["origin"], "revision_proposal")
        self.assertEqual(prefill["answers"]["side"], "left")
        self.assertEqual(prefill["answers"]["extent"], "area")
        self.assertEqual(prefill["baseline"]["answers"]["side"], "right")
        self.assertEqual(set(prefill["changed_fields"]), {"side", "area"})
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), self.original)
        calls = len(self.backend.calls)
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        self.flow.confirm(reply, payload)
        self.assertEqual(len(self.backend.calls), calls)

    def test_model_cannot_open_revision_for_a_hypothetical_or_declined_request(self):
        for text in ["如果位置选错了怎么办", "不用修改定位", "刚才药名说错了"]:
            with self.subTest(text=text):
                self.backend.commands = [
                    cmd("revise_self_check", task_id="body_location", evidence=text),
                    cmd("ask_question", question_id="onset"),
                ]
                self.engine.submit(self.state.session_id, UserInput(text))
                self.assertIsNone(self.state.self_check_conversation)
                self.assertEqual(
                    self.state.facts["symptom_location"].to_dict(), self.original
                )

    def test_pending_calculation_keeps_its_card_and_rejects_a_second_interactive_form(
        self,
    ):
        self.backend.commands = [
            cmd(
                "run_capability",
                id="calculate.units",
                input={"value": "1.25", "from_unit": "kg", "to_unit": "g"},
                evidence="请把1.25kg换算成g",
            )
        ]
        reply = self.engine.submit(
            self.state.session_id,
            UserInput("请把1.25kg换算成g", expected_action_id=self.original_reply.action_id),
        )
        self.assertEqual(reply.kind, "calculation_input")
        self.assertIsNone(reply.location_revision_action)
        before = deepcopy(self.state.to_dict())
        events = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.start(reply)
        self.assertEqual(self.state.to_dict(), before)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )

    def test_unchanged_note_is_preserved_without_reprocessing_it(self):
        reply = self.start()
        payload = self.flow.payload(reply)
        payload["note"] = "这是先前的备注"
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        reply = self.flow.confirm(reply, payload)
        self.original = deepcopy(self.state.facts["symptom_location"].to_dict())
        reply = self.start(reply)
        self.assertEqual(reply.task["answer_spec"]["prefill"]["note"], payload["note"])
        changed = self.flow.payload(reply)
        changed["note"] = payload["note"]
        changed["answers"]["side"] = "left"
        calls = len(self.backend.calls)
        self.flow.confirm(reply, changed)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(
            self.state.facts["symptom_location"].structured_input["note"],
            payload["note"],
        )

    def test_answering_history_during_revision_resumes_the_newer_question(self):
        from tests.test_agent import fact

        reply = self.start()
        self.backend.commands = [
            cmd("record_facts", facts=[fact("onset", "昨天开始", "昨天开始")]),
            cmd("ask_question", question_id="trajectory"),
        ]
        reply = self.engine.submit(
            self.state.session_id, UserInput("昨天开始", expected_action_id=reply.action_id)
        )
        self.assertEqual(reply.question_id, "trajectory")
        resume = next(
            q.value
            for q in reply.quick_replies
            if q.value.startswith("resume_self_check:")
        )
        reply = self.engine.submit(
            self.state.session_id, UserInput("继续修改", resume, reply.action_id)
        )
        payload = self.flow.payload(reply)
        payload["answers"]["side"] = "left"
        calls = len(self.backend.calls)
        completed = self.flow.confirm(reply, payload)
        self.assertEqual(completed.question_id, "trajectory")
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(self.state.get("onset"), "昨天开始")

    def test_offline_caregiver_can_revise_a_legacy_coarse_location(self):
        from tests import test_consultation as offline

        base = offline.ConsultationTestCase()
        base.setUp()
        try:
            # Answer order follows the differential planner's history
            # phase for a headache chief (associated + targeted detail
            # questions precede onset/trajectory/severity).
            for text, choice in [
                ("孩子", "child"),
                ("8岁", None),
                ("头疼", None),
                ("都没有", "no"),
                ("没有其他不适", None),
                ("后脑勺胀痛", None),
                ("昨天开始", None),
                ("变化不大", "stable"),
                ("3分", None),
            ]:
                base.answer(text, choice)
            self.assertEqual(base.reply.task["id"], "body_location")
            old_reply = base.answer("头/面部", "head")
            old = base.state.facts["symptom_location"]
            self.assertEqual(old.source.value, "caregiver")
            revised = base.engine.submit(
                base.state.session_id,
                UserInput(
                    "修改定位", f"revise_location:{old.fact_id}", old_reply.action_id
                ),
            )
            self.assertEqual(
                revised.task["answer_spec"]["prefill"]["answers"], {"region": "head"}
            )
            payload = self.flow.payload(revised)
            payload["answers"]["side"] = "left"
            resumed = base.engine.submit(
                base.state.session_id,
                UserInput(
                    "核对定位答案",
                    "confirm_location_answer",
                    revised.action_id,
                    observation=payload,
                ),
            )
            latest = base.state.facts["symptom_location"]
            self.assertEqual(latest.source.value, "caregiver")
            self.assertEqual(latest.supersedes, old.fact_id)
            self.assertEqual(resumed.question_id, old_reply.question_id)
        finally:
            base.tearDown()


if __name__ == "__main__":
    unittest.main()
