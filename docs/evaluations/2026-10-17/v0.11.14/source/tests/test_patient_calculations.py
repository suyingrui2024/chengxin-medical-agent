"""Explicit patient confirmation, zero-model arithmetic and continuation boundaries."""
from copy import deepcopy
import unittest

from medagent.consultation import StaleActionError, UserInput
from medagent.domain import Assertion, Fact, Phase, Source
from medagent.events import EventStore
from medagent.agent import AdaptiveConsultationEngine
from medagent.extensions.builtin import load_registry
from medagent.knowledge import ContentRepository
from medagent.reasoner import OrderedQuestionRanker
from medagent.self_checks import SelfCheckService
from medagent.triage import TriageService


class Backend:
    name = "fixture"

    def __init__(self):
        self.commands, self.calls = [], []

    def decide(self, identity, messages):
        self.calls.append(deepcopy(messages))
        return self.commands.pop(0)


class PatientCalculationTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        registry = load_registry()
        self.events = EventStore()
        self.engine = AdaptiveConsultationEngine(
            content=ContentRepository(registry),
            triage=TriageService(),
            self_checks=SelfCheckService(registry),
            events=self.events,
            ranker=OrderedQuestionRanker(),
            backend=self.backend,
        )
        self.state, self.opening = self.engine.create_session()

    def request(self, values=None, text="请把1.25kg换算成g", quote=None):
        self.backend.commands.append(
            {
                "tool": "run_capability",
                "arguments": {
                    "id": "calculate.units",
                    "input": {"value": "1.25", "from_unit": "kg", "to_unit": "g"}
                    if values is None
                    else values,
                    "evidence": text if quote is None else quote,
                },
            }
        )
        return self.engine.submit(self.state.session_id, UserInput(text))

    def confirm_input(self, reply, values=None):
        return UserInput(
            "核对换算参数",
            "confirm_calculation",
            reply.action_id,
            {
                "proposal_id": reply.calculation_input["proposal_id"],
                "input": values or {"value": "1.25", "from_unit": "kg", "to_unit": "g"},
            },
        )

    def test_confirmation_executes_once_without_model_or_clinical_fact_and_resumes(
        self,
    ):
        draft = self.request()
        self.assertEqual(draft.kind, "calculation_input")
        self.assertEqual(self.state.calculations, [])
        original = self.confirm_input(draft)
        result = self.engine.submit(self.state.session_id, original)
        self.assertEqual(result.kind, "calculation_result")
        self.assertEqual(result.calculation_result["data"]["value"], "1250")
        self.assertEqual(result.calculation_result["input_status"], "confirmed")
        self.assertEqual(result.calculation_result["quality"], "not_assessed")
        self.assertEqual(self.state.facts, {})
        self.assertEqual(len(self.backend.calls), 1)
        with self.assertRaises(StaleActionError):
            self.engine.submit(self.state.session_id, original)
        self.assertEqual(len(self.state.calculations), 1)
        resumed = self.engine.submit(
            self.state.session_id,
            UserInput(
                "继续问诊",
                "resume_after_calculation",
                result.action_id,
                {"execution_id": result.calculation_result["execution_id"]},
            ),
        )
        self.assertEqual(resumed.message, self.opening.message)
        self.assertEqual(len(self.backend.calls), 1)

    def test_partial_input_is_blank_and_patient_can_supply_new_values(self):
        draft = self.request({}, "请帮我换算单位，我还没有提供数字或单位")
        self.assertEqual(draft.calculation_input["input"], {})
        result = self.engine.submit(
            self.state.session_id,
            self.confirm_input(
                draft, {"value": "0.0005", "from_unit": "L", "to_unit": "mL"}
            ),
        )
        self.assertEqual(result.calculation_result["data"]["value"], "0.5")
        self.assertEqual(
            result.calculation_result["confirmation"]["original_input"], {}
        )
        self.assertEqual(len(self.backend.calls), 1)

    def test_unknown_parameters_are_missing_not_invalid_measurements(self):
        draft = self.request(
            {"value": None, "from_unit": None, "to_unit": None}, "请帮我换算，还不知道具体数值和单位"
        )
        self.assertEqual(draft.kind, "calculation_input")
        self.assertEqual(draft.calculation_input["input"], {})
        capability = next(
            item
            for item in self.engine.static_context["available_capabilities"]
            if item["id"] == "calculate.units"
        )
        self.assertEqual(capability["proposal_input_schema"]["required"], [])
        self.assertEqual(
            set(capability["execution_input_schema"]["required"]),
            {"value", "from_unit", "to_unit"},
        )
        self.assertEqual(self.state.calculations, [])

    def test_invalid_dimension_keeps_edited_inputs_and_original_proposal(self):
        draft = self.request()
        bad = {"value": "2", "from_unit": "kg", "to_unit": "mL"}
        retry = self.engine.submit(
            self.state.session_id, self.confirm_input(draft, bad)
        )
        self.assertEqual(retry.kind, "calculation_input")
        self.assertTrue(retry.calculation_input["feedback"])
        self.assertEqual(retry.calculation_input["input"], bad)
        self.assertEqual(self.state.calculations, [])
        final = self.engine.submit(
            self.state.session_id, self.confirm_input(retry, {**bad, "to_unit": "g"})
        )
        self.assertEqual(final.calculation_result["data"]["value"], "2000")
        self.assertEqual(
            final.calculation_result["confirmation"]["original_input"]["value"], "1.25"
        )
        self.assertEqual(len(self.backend.calls), 1)

    def test_missing_or_wrong_card_identifiers_are_refused_before_mutation(self):
        draft = self.request()
        for edit in ["action", "proposal", "missing-action", "unknown-field"]:
            user = self.confirm_input(draft)
            if edit == "action":
                user = UserInput(user.text, user.choice_value, "old", user.calculation)
            if edit == "missing-action":
                user = UserInput(user.text, user.choice_value, None, user.calculation)
            if edit == "proposal":
                user.calculation["proposal_id"] = "other"
            if edit == "unknown-field":
                user.calculation["confirmed"] = True
            revision, count = self.state.revision, len(
                self.events.for_session(self.state.session_id)
            )
            with self.subTest(edit=edit), self.assertRaises(StaleActionError):
                self.engine.submit(self.state.session_id, user)
            self.assertEqual(self.state.revision, revision)
            self.assertEqual(len(self.events.for_session(self.state.session_id)), count)
            self.assertEqual(self.state.calculations, [])

    def test_malformed_confirmed_parameters_cannot_execute_and_form_is_retained(self):
        draft = self.request()
        for values in [
            {},
            {"value": "NaN", "from_unit": "kg", "to_unit": "g"},
            {"value": "1", "from_unit": "kg", "to_unit": "g", "quality": "adequate"},
            {"value": "1", "from_unit": [], "to_unit": "g"},
        ]:
            user = UserInput(
                "核对参数",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": values,
                },
            )
            draft = self.engine.submit(self.state.session_id, user)
            self.assertEqual(draft.kind, "calculation_input")
            self.assertEqual(self.state.calculations, [])

    def test_skip_returns_to_original_question_without_model_call(self):
        draft = self.request()
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "先跳过换算",
                "skip_calculation",
                draft.action_id,
                {"proposal_id": draft.calculation_input["proposal_id"], "input": {}},
            ),
        )
        self.assertEqual(reply.message, self.opening.message)
        self.assertIsNone(self.state.pending_calculation)
        self.assertEqual(self.state.calculations, [])
        self.assertEqual(len(self.backend.calls), 1)

    def test_emergency_text_bypasses_even_stale_card_and_never_computes(self):
        self.request()
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "现在叫不醒",
                "confirm_calculation",
                "old",
                {"proposal_id": "wrong", "input": {}},
            ),
        )
        self.assertEqual(reply.kind, "emergency")
        self.assertIsNone(self.state.pending_calculation)
        self.assertIsNone(self.state.calculation_resume)
        self.assertEqual(self.state.calculations, [])
        self.assertEqual(len(self.backend.calls), 1)

    def test_new_patient_message_cancels_draft_and_reaches_the_planner(self):
        self.request()
        self.backend.commands = [
            {"tool": "ask_question", "arguments": {"question_id": "age"}}
        ]
        reply = self.engine.submit(self.state.session_id, UserInput("先不换算了，我想说说咳嗽"))
        self.assertEqual(reply.question_id, "age")
        self.assertIsNone(self.state.pending_calculation)
        self.assertEqual(self.state.calculations, [])
        self.assertIn("先不换算了", self.backend.calls[-1][1]["content"])

    def test_fabricated_or_unsolicited_inputs_do_not_create_a_form(self):
        for text, values in [
            ("我自己32岁", {"value": "1.25", "from_unit": "kg", "to_unit": "g"}),
            ("请把1.25kg换算成g", {"value": "9", "from_unit": "kg", "to_unit": "g"}),
            ("请把1 000kg换算成g", {"value": "000", "from_unit": "kg", "to_unit": "g"}),
        ]:
            self.backend.commands = [
                {
                    "tool": "run_capability",
                    "arguments": {
                        "id": "calculate.units",
                        "input": values,
                        "evidence": text,
                    },
                },
                {"tool": "ask_question", "arguments": {"question_id": "age"}},
            ]
            reply = self.engine.submit(self.state.session_id, UserInput(text))
            self.assertEqual(reply.question_id, "age")
            self.assertIsNone(self.state.pending_calculation)
            self.assertEqual(self.state.calculations, [])

    def test_fact_correction_invalidates_the_previous_confirmation(self):
        self.state.record(
            Fact("age_years", 32, Assertion.PRESENT, Source.PATIENT, "fixture")
        )
        draft = self.request()
        reply = self.engine.correct_fact(
            self.state.session_id,
            "age_years",
            "33岁",
            self.state.facts["age_years"].fact_id,
        )
        self.assertIsNone(self.state.pending_calculation)
        with self.assertRaises(StaleActionError):
            self.engine.submit(self.state.session_id, self.confirm_input(draft))
        self.assertEqual(self.state.get("age_years"), 33)

    def test_prior_clarification_returns_without_losing_its_wording(self):
        self.backend.commands = [
            {
                "tool": "clarify",
                "arguments": {"topic": "medications_taken", "message": "刚才提到的药，叫什么名字？"},
            }
        ]
        old = self.engine.submit(self.state.session_id, UserInput("用过一个药但我想补充"))
        draft = self.request()
        resumed = self.engine.submit(
            self.state.session_id,
            UserInput(
                "先跳过换算",
                "skip_calculation",
                draft.action_id,
                {"proposal_id": draft.calculation_input["proposal_id"], "input": {}},
            ),
        )
        self.assertEqual(resumed.message, old.message)
        self.assertTrue(self.state.active_question_is_clarification)
        self.assertEqual(resumed.phase, old.phase)
