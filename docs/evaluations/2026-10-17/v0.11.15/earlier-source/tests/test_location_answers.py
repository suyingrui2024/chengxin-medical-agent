"""Location components collect declared dimensions, with no facts before confirmation."""
from copy import deepcopy
import unittest

from medagent.consultation import StaleActionError, UserInput
from tests import test_self_check_conversation as fixture
from tests.test_agent import cmd, fact


class LocationAnswerTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture.SelfCheckConversationTests()
        self.helper.setUp()
        self.engine, self.state, self.backend = (
            self.helper.engine,
            self.helper.state,
            self.helper.backend,
        )

    def offer(self, fields=None):
        original = self.helper.offer()
        self.backend.commands = [
            cmd(
                "clarify",
                topic="symptom_location",
                message="头部哪里、哪一侧？不适是一点还是一片？",
                answer_fields=fields or ["region", "area", "side", "extent"],
            )
        ]
        reply = self.helper.submit("位置还不太确定")
        self.assertEqual(reply.task["interaction_id"], original.task["interaction_id"])
        return reply

    def payload(self, reply):
        return {
            "schema": "location-answer/v1",
            "form_id": reply.task["answer_spec"]["id"],
            "answers": {
                "region": "head",
                "area": {"kind": "preset", "value": "temple"},
                "side": "right",
                "extent": "area",
            },
            "note": "",
        }

    def confirm(self, reply, payload):
        return self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对定位答案",
                "confirm_location_answer",
                reply.action_id,
                observation=payload,
            ),
        )

    def test_declared_dimensions_have_registered_inputs_and_no_default_facts(self):
        reply = self.offer()
        spec = reply.task["answer_spec"]
        self.assertEqual(spec["fields"], ["region", "area", "side", "extent"])
        self.assertNotIn("prefill", spec)
        self.assertEqual([x.value for x in reply.quick_replies], ["skip"])
        self.assertNotIn("symptom_location", self.state.facts)

    def test_natural_description_prefills_only_reported_dimensions(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="右太阳穴附近疼",
                input={"side": "right", "area": {"kind": "preset", "value": "temple"}},
            )
        ]
        reply = self.helper.submit("右太阳穴附近疼")
        draft = reply.task["answer_spec"]["prefill"]
        self.assertNotIn("extent", draft["answers"])
        self.assertEqual(draft["answers"]["side"], "right")
        self.assertNotIn("symptom_location", self.state.facts)
        self.assertNotIn("result_proposal", reply.task)

    def test_confirmation_records_typed_choices_without_quality_upgrade(self):
        reply = self.offer()
        payload = self.payload(reply)
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        result = self.state.facts["symptom_location"]
        self.assertEqual(result.value, "部位：头/面部；具体位置：太阳穴附近；左右：右侧；范围：一片范围")
        self.assertEqual(result.structured_input["answers"]["side"]["value"], "right")
        self.assertEqual(result.source.value, "patient")
        self.assertEqual(result.quality.value, "not_assessed")
        payload["answers"]["side"] = "left"
        self.assertEqual(result.structured_input["answers"]["side"]["value"], "right")

    def test_unknown_subfields_stay_unknown_under_known_broad_region(self):
        reply = self.offer()
        payload = self.payload(reply)
        payload["answers"].update(
            area={"kind": "unknown", "value": None}, side="unknown", extent="unknown"
        )
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        result = self.state.facts["symptom_location"]
        self.assertEqual(result.assertion.value, "present")
        for field in ["area", "side", "extent"]:
            self.assertEqual(
                result.structured_input["answers"][field]["assertion"], "unknown"
            )
            self.assertIsNone(result.structured_input["answers"][field]["value"])

    def test_missing_or_cross_region_answers_are_rejected_before_journaling(self):
        reply = self.offer()
        for mutate in [
            lambda x: x["answers"].pop("extent"),
            lambda x: x["answers"].update(region="chest"),
            lambda x: x["answers"].update(unasked="value"),
        ]:
            payload = self.payload(reply)
            mutate(payload)
            before = deepcopy(self.state.to_dict())
            events = len(self.engine.events.for_session(self.state.session_id))
            with self.assertRaises(ValueError):
                self.confirm(reply, payload)
            self.assertEqual(self.state.to_dict(), before)
            self.assertEqual(
                len(self.engine.events.for_session(self.state.session_id)), events
            )

    def test_old_coarse_button_cannot_bypass_requested_side_and_area(self):
        reply = self.offer()
        events = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.helper.submit("头/面部", "head", reply.action_id)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )
        self.assertNotIn("symptom_location", self.state.facts)

    def test_patient_can_correct_a_model_draft_without_borrowing_the_old_quote(self):
        self.offer()
        text = "右太阳穴附近，一片范围"
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence=text,
                input={
                    "side": "right",
                    "area": {"kind": "preset", "value": "temple"},
                    "extent": "area",
                },
            )
        ]
        reply = self.helper.submit(text)
        payload = self.payload(reply)
        payload["answers"]["side"] = "left"
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        result = self.state.facts["symptom_location"]
        self.assertIn("左右：左侧", result.evidence)
        self.assertEqual(
            result.structured_input["proposal_source"]["answers"]["side"], "right"
        )
        self.assertEqual(result.structured_input["answers"]["side"]["value"], "left")

    def test_repeated_confirmation_does_not_duplicate_a_fact_or_user_turn(self):
        reply = self.offer()
        payload = self.payload(reply)
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        events = len(self.engine.events.for_session(self.state.session_id))
        with self.assertRaises(StaleActionError):
            self.confirm(reply, payload)
        self.assertEqual(
            len(self.engine.events.for_session(self.state.session_id)), events
        )
        self.assertEqual(
            len([f for f in self.state.history if f.key == "symptom_location"]), 1
        )

    def test_urgent_note_precedes_old_form_validation_and_does_not_record_location(
        self,
    ):
        reply = self.offer()
        payload = self.payload(reply)
        payload["form_id"] = "obsolete"
        payload["note"] = "现在叫不醒了"
        urgent = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对定位答案",
                "confirm_location_answer",
                "obsolete-action",
                observation=payload,
            ),
        )
        self.assertEqual(urgent.kind, "emergency")
        self.assertNotIn("symptom_location", self.state.facts)

    def test_invalid_implicit_draft_cannot_leave_a_new_form_behind(self):
        self.helper.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="右太阳穴附近",
                input={"side": "invented"},
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.helper.submit("右太阳穴附近")
        self.assertNotIn("answer_spec", reply.task)
        self.assertNotIn("symptom_location", self.state.facts)

    def test_spontaneous_details_can_create_a_reviewable_form_without_an_extra_question(
        self,
    ):
        self.helper.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="右太阳穴附近",
                input={"side": "right", "area": {"kind": "preset", "value": "temple"}},
            )
        ]
        reply = self.helper.submit("右太阳穴附近")
        self.assertEqual(
            reply.task["answer_spec"]["fields"], ["region", "area", "side"]
        )
        self.assertNotIn("extent", reply.task["answer_spec"]["prefill"]["answers"])
        self.assertNotIn("symptom_location", self.state.facts)

    def test_unsupported_registered_contract_fails_before_a_patient_can_use_it(self):
        from medagent.location_answers import validate_answer_contract

        task = deepcopy(self.engine.content.tasks["body_location"])
        task["answer_contract"]["area_presets"]["head"].append(
            {"value": "unknown", "label": "伪造未知预设"}
        )
        with self.assertRaises(ValueError):
            validate_answer_contract(task)

    def test_uncertain_or_unsupported_words_cannot_become_positive_drafts(self):
        self.offer()
        for text, proposed in [
            ("可能右太阳穴附近", {"side": "right"}),
            ("可能右太阳穴附近", {"area": {"kind": "text", "value": "右太阳穴"}}),
            ("右太阳穴附近", {"extent": "area"}),
            ("额头附近", {"area": {"kind": "preset", "value": "temple"}}),
            ("右太阳穴附近", {"extent": "unknown"}),
        ]:
            with self.subTest(text=text, proposed=proposed):
                self.backend.commands = [
                    cmd(
                        "propose_self_check_result",
                        task_id="body_location",
                        option="head",
                        evidence=text,
                        input=proposed,
                    ),
                    cmd("show_self_check", task_id="body_location"),
                ]
                reply = self.helper.submit(text)
                self.assertNotIn("prefill", reply.task["answer_spec"])
                self.assertNotIn("symptom_location", self.state.facts)

    def test_field_quotes_preserve_clear_location_and_uncertain_extent(self):
        self.offer()
        text = "右太阳穴附近疼；范围说不清"
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence=text,
                input={
                    "side": "right",
                    "area": {"kind": "preset", "value": "temple"},
                    "extent": "unknown",
                },
                input_evidence={
                    "side": "右太阳穴附近疼",
                    "area": "右太阳穴附近疼",
                    "extent": "范围说不清",
                },
            ),
        ]
        reply = self.helper.submit(text)
        draft = reply.task["answer_spec"]["prefill"]
        self.assertEqual(draft["answers"]["side"], "right")
        self.assertEqual(draft["answers"]["extent"], "unknown")
        self.assertEqual(draft["input_evidence"]["extent"], "范围说不清")
        self.assertNotIn("symptom_location", self.state.facts)
        payload = self.payload(reply)
        payload["answers"]["extent"] = "unknown"
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        structured = self.state.facts["symptom_location"].structured_input
        self.assertIsNone(structured["answers"]["extent"]["value"])
        self.assertEqual(
            structured["proposal_source"]["input_evidence"], draft["input_evidence"]
        )

    def test_field_quote_cannot_cut_off_a_same_clause_qualifier(self):
        self.offer()
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence="可能右太阳穴附近",
                input={"side": "right"},
                input_evidence={"side": "右"},
            ),
            cmd("show_self_check", task_id="body_location"),
        ]
        reply = self.helper.submit("可能右太阳穴附近")
        self.assertNotIn("prefill", reply.task["answer_spec"])

    def test_uncertain_verbatim_text_remains_uncertain_in_the_confirmed_answer(self):
        self.offer()
        text = "可能右太阳穴附近"
        self.backend.commands = [
            cmd(
                "propose_self_check_result",
                task_id="body_location",
                option="head",
                evidence=text,
                input={"area": {"kind": "text", "value": text}},
            ),
        ]
        reply = self.helper.submit(text)
        payload = self.payload(reply)
        payload["answers"]["area"] = {"kind": "text", "value": text}
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.confirm(reply, payload)
        area = self.state.facts["symptom_location"].structured_input["answers"]["area"]
        self.assertEqual(area["assertion"], "unknown")
        self.assertEqual(area["reported_text"], text)
        self.assertIsNone(area["value"])

    def test_service_retry_keeps_the_same_confirmed_structured_fact(self):
        import json

        reply = self.offer()
        payload = self.payload(reply)
        self.backend.commands = [RuntimeError("Synthetic unavailable backend")]
        failed = self.confirm(reply, payload)
        self.assertEqual(failed.kind, "service_error")
        original = deepcopy(self.state.facts["symptom_location"].to_dict())
        self.backend.commands = [cmd("ask_question", question_id="onset")]
        self.helper.submit("重试这一轮", "retry_turn", failed.action_id)
        self.assertEqual(self.state.facts["symptom_location"].to_dict(), original)
        context = json.loads(self.backend.calls[-1][1]["content"])
        self.assertEqual(
            context["observation_completion"]["fact_id"], original["fact_id"]
        )
        self.assertEqual(
            context["facts"]["symptom_location"]["structured_input"],
            original["structured_input"],
        )
        self.assertEqual(
            len([f for f in self.state.history if f.key == "symptom_location"]), 1
        )


if __name__ == "__main__":
    unittest.main()
