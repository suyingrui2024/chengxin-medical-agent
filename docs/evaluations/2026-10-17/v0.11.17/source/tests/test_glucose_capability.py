"""A second sourced calculation must work through the same patient and harness paths."""
from copy import deepcopy
from decimal import Decimal
import unittest

from medagent.consultation import UserInput, StaleActionError
from medagent.execution_capabilities import dispatch, freeze_binding, validate_receipt
from medagent.extensions.builtin import load_registry
from medagent.extensions.glucose import convert_glucose, glucose_evidence
from medagent.extensions.input_evidence import CapabilityError
from medagent.extensions.registry import Capability, CapabilityRegistry
from medagent.presentation import calculation_presentation
from tests import test_patient_calculations as patient_fixture
from tests.test_execution_capabilities import payload


class GlucoseCalculationTests(unittest.TestCase):
    def test_source_qualifiers_cannot_be_trimmed_into_a_single_exact_reading(self):
        args = {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"}
        for text in [
            "不是血糖180mg/dL",
            "血糖不是180mg/dL",
            "血糖<180mg/dL",
            "血糖至少180mg/dL",
            "血糖180mg/dL以上",
        ]:
            with self.subTest(text=text), self.assertRaises(CapabilityError):
                glucose_evidence(
                    args,
                    "血糖180mg/dL" if text.startswith("不是") else text,
                    [{"id": "root", "text": text}],
                )

    def test_conventional_factor_direction_identity_and_rounding_are_explicit(self):
        for value, before, after, expected in [
            ("10", "mmol/L", "mg/dL", "180"),
            ("180", "mg/dL", "mmol/L", "10"),
            ("5.6", "mmol/L", "mg/dL", "100.8"),
            ("97", "mg/dL", "mmol/L", "5.3889"),
            ("0", "mg/dL", "mmol/L", "0"),
            ("1.234567", "mg/dL", "mg/dL", "1.234567"),
        ]:
            with self.subTest(value=value, before=before):
                data = convert_glucose(
                    {"value": value, "from_unit": before, "to_unit": after}
                )
                self.assertEqual(data["value"], expected)
                self.assertEqual(data["approximate"], before != after)
                self.assertIn("18", data["method"])
                self.assertIn("不判断高低", data["limitations"][1])
        tiny = convert_glucose(
            {"value": "0.000000000001", "from_unit": "mg/dL", "to_unit": "mmol/L"}
        )
        self.assertGreater(Decimal(tiny["value"]), 0)
        self.assertEqual(tiny["value"], "0.00000000000005556")

    def test_non_numeric_and_foreign_units_never_produce_a_registered_result(self):
        registry = load_registry()
        for changes in [
            {"value": "NaN"},
            {"value": "-1"},
            {"value": "1e2"},
            {"value": 180},
            {"from_unit": "kg"},
            {"to_unit": "mmol/mol"},
            {"shell": "id"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(
                (ValueError, TypeError)
            ):
                registry.execute(
                    "calculate.glucose",
                    {
                        "value": "180",
                        "from_unit": "mg/dL",
                        "to_unit": "mmol/L",
                        **changes,
                    },
                    "test",
                )

    def test_source_policy_requires_blood_glucose_and_matches_full_tokens(self):
        args = {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"}
        for text in [
            "请把血糖180mg/dL换成mmol/L",
            "血浆葡萄糖180毫克/分升换算成mmol/L",
            "blood glucose 180 mg/dl to mmol/L",
        ]:
            receipt = glucose_evidence(args, text, [{"id": "root", "text": text}])
            self.assertEqual(receipt["scope"], "analyte_quantity_and_source_unit_only")
        for text in [
            "胆固醇180mg/dL换成mmol/L",
            "肌酐180mg/dL换成mmol/L",
            "不是血糖，180mg/dL换算",
            "糖化血红蛋白180mg/dL换算",
            "血糖180mg/dL和200mg/dL换算",
            "血糖-180mg/dL换算",
            "血糖负180mg/dL换算",
            "血糖1e180mg/dL换算",
            "血糖1×10^180mg/dL换算",
            "血糖180mg/dL/h换算",
            "血糖160至180mg/dL换算",
        ]:
            with self.subTest(text=text), self.assertRaises(CapabilityError):
                glucose_evidence(args, text, [{"id": "root", "text": text}])
        with self.assertRaises(CapabilityError):
            glucose_evidence(args, "180mg/dL", [{"id": "root", "text": "血糖180mg/dL"}])

    def test_origin_cannot_be_fabricated_and_another_analyte_in_another_clause_is_not_borrowed(
        self,
    ):
        args = {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"}
        text = "血糖180mg/dL，肌酐1.2mg/dL"
        self.assertEqual(
            glucose_evidence(args, "血糖180mg/dL", [{"id": "human", "text": text}])[
                "source_turn_id"
            ],
            "human",
        )
        for source in [[], [{"id": "other", "text": "血糖100mg/dL"}]]:
            with self.assertRaises(CapabilityError):
                glucose_evidence(args, "血糖180mg/dL", source)

    def test_dispatch_keeps_source_formula_references_and_old_receipts_readable(self):
        request = payload(
            "请把血糖180mg/dL换成mmol/L", "血糖180mg/dL", "180", "mg/dL", "mmol/L"
        )
        request["request"]["capability_id"] = "calculate.glucose"
        result = dispatch(request)
        self.assertEqual(result["data"]["value"], "10")
        self.assertTrue(result["data"]["approximate"])
        self.assertEqual(result["input_status"], "unconfirmed")
        self.assertEqual(result["quality"], "not_assessed")
        self.assertIn("diabetes.org", result["references"][0]["url"])
        self.assertEqual(validate_receipt(result), result)
        legacy = dispatch(payload())
        legacy.pop("name")
        legacy["presentation"] = calculation_presentation(legacy)
        self.assertNotIn("subject_label", legacy["presentation"]["blocks"][0]["data"])
        self.assertEqual(validate_receipt(legacy)["data"]["value"], "1250")
        for field in ["references", "data"]:
            altered = deepcopy(result)
            if field == "references":
                altered["references"][0]["url"] = "javascript:alert(1)"
            else:
                altered["data"].pop("approximate")
            altered["presentation"] = calculation_presentation(altered)
            with self.assertRaises(ValueError):
                validate_receipt(altered)

    def test_disabled_and_frozen_versions_cannot_gain_a_new_calculation(self):
        registry = load_registry().with_enabled("calculate.glucose", False)
        catalog = dispatch(
            {"binding": freeze_binding(registry), "operation": "catalog"}
        )
        self.assertNotIn(
            "calculate.glucose", {item["id"] for item in catalog["capabilities"]}
        )
        request = payload("血糖180mg/dL换算", "血糖180mg/dL", "180", "mg/dL", "mmol/L")
        request["request"]["capability_id"] = "calculate.glucose"
        request["binding"] = freeze_binding(registry)
        with self.assertRaises(CapabilityError):
            dispatch(request)

    def test_missing_sources_or_unregistered_form_policy_are_rejected_at_registration(
        self,
    ):
        manifest = next(
            item
            for item in load_registry().descriptions()
            if item["id"] == "calculate.glucose"
        )
        for key, value in [
            ("references", []),
            ("patient_input_policy", "arbitrary_python"),
        ]:
            altered = {**manifest, key: value}
            with self.assertRaises(ValueError):
                CapabilityRegistry(
                    [Capability(altered, execute=convert_glucose)],
                    {"enabled": [altered["id"]]},
                )


class PatientGlucoseTests(unittest.TestCase):
    def setUp(self):
        # Reuse setup only; importing the test module must not duplicate its cases.
        fixture = patient_fixture.PatientCalculationTests()
        fixture.setUp()
        self.engine, self.backend, self.state = (
            fixture.engine,
            fixture.backend,
            fixture.state,
        )

    def request(self, text="请把血糖180mg/dL换成mmol/L", arguments=None):
        self.backend.commands = [
            {
                "tool": "run_capability",
                "arguments": {
                    "id": "calculate.glucose",
                    "input": {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"}
                    if arguments is None
                    else arguments,
                    "evidence": text,
                },
            }
        ]
        return self.engine.submit(self.state.session_id, UserInput(text))

    def test_patient_can_check_and_edit_inputs_without_a_new_model_request_or_fact_write(
        self,
    ):
        draft = self.request()
        self.assertEqual(draft.calculation_input["form_kind"], "unit_conversion/v2")
        self.assertIn("血糖", draft.calculation_input["subject_label"])
        self.assertEqual(len(draft.calculation_input["unit_options"]), 2)
        before = len(self.backend.calls)
        reply = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对血糖参数",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": draft.calculation_input["proposal_id"],
                    "input": {"value": "10", "from_unit": "mmol/L", "to_unit": "mg/dL"},
                },
            ),
        )
        self.assertEqual(reply.calculation_result["data"]["value"], "180")
        self.assertEqual(
            reply.calculation_result["confirmation"]["original_input"]["value"], "180"
        )
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(self.state.facts, {})
        result = reply.calculation_result
        continued = self.engine.submit(
            self.state.session_id,
            UserInput(
                "继续问诊",
                "resume_after_calculation",
                reply.action_id,
                {"execution_id": result["execution_id"]},
            ),
        )
        self.assertEqual(continued.question_id, "chief_complaint")
        self.assertEqual(len(self.backend.calls), before)

    def test_missing_inputs_stay_blank_and_foreign_lab_is_not_prefilled(self):
        reply = self.request("请帮我进行血糖单位换算，还没给数值和单位", {})
        self.assertEqual(reply.calculation_input["input"], {})
        for text in ["肌酐180mg/dL换算成mmol/L", "血糖与胆固醇180mg/dL换算"]:
            with self.assertRaises(ValueError):
                self.engine.request_calculation(
                    self.state,
                    "calculate.glucose",
                    {"value": "180", "from_unit": "mg/dL", "to_unit": "mmol/L"},
                    text,
                    text,
                    "source",
                )

    def test_invalid_input_preserves_legal_draft_values_and_cannot_compute_twice(self):
        draft = self.request()
        identity = draft.calculation_input["proposal_id"]
        failed = self.engine.submit(
            self.state.session_id,
            UserInput(
                "核对",
                "confirm_calculation",
                draft.action_id,
                {
                    "proposal_id": identity,
                    "input": {
                        "value": "NaN",
                        "from_unit": "mmol/L",
                        "to_unit": "mg/dL",
                    },
                },
            ),
        )
        self.assertEqual(
            failed.calculation_input["input"],
            {"value": "NaN", "from_unit": "mmol/L", "to_unit": "mg/dL"},
        )
        self.assertIn("血糖", failed.calculation_input["feedback"])
        self.assertEqual(self.state.calculations, [])
        with self.assertRaises(StaleActionError):
            self.engine.submit(
                self.state.session_id,
                UserInput(
                    "核对",
                    "confirm_calculation",
                    draft.action_id,
                    {
                        "proposal_id": identity,
                        "input": {
                            "value": "180",
                            "from_unit": "mg/dL",
                            "to_unit": "mmol/L",
                        },
                    },
                ),
            )


if __name__ == "__main__":
    unittest.main()
