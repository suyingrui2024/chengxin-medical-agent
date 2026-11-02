"""Named-formula engine: contract validation, arithmetic, fail-closed rules."""

from copy import deepcopy
import json
import unittest
from importlib import resources

from medagent.extensions.formula_engine import (
    FormulaInputError,
    FormulaSpecError,
    run_formula,
    validate_formula,
)
from medagent.extensions.formula_forms import OUTPUT_SCHEMA, formula_form_spec
from medagent.extensions.formulas import FORMULAS


def load_pack(name):
    raw = resources.files("medagent.extensions").joinpath(f"packs/{name}.json").read_text(
        encoding="utf-8"
    )
    return json.loads(raw)


def manifest_for(key):
    return load_pack(key)


class ArithmeticTests(unittest.TestCase):
    def test_every_formula_matches_its_audit_cases(self):
        for key, spec in FORMULAS.items():
            validated = validate_formula(manifest_for(key))
            for values, expected in spec.audit_cases:
                result = run_formula(validated, values)
                self.assertEqual(result["value"], expected, f"{key} {values}")

    def test_result_is_deterministic(self):
        validated = validate_formula(manifest_for("bmi"))
        first = run_formula(validated, {"height_cm": "170", "weight_kg": "65"})
        for _ in range(100):
            again = run_formula(validated, {"height_cm": "170", "weight_kg": "65"})
            self.assertEqual(again["value"], first["value"])

    def test_bmi_band_boundaries_are_half_open(self):
        validated = validate_formula(manifest_for("bmi"))
        # 18.5 belongs to "正常范围" (>=18.5), not to "偏低" (<18.5).
        low = run_formula(validated, {"height_cm": "170", "weight_kg": "53.465"})
        self.assertEqual(low["band"]["key"], "normal")

    def test_missing_input_fails_closed(self):
        validated = validate_formula(manifest_for("bmi"))
        with self.assertRaises(FormulaInputError):
            run_formula(validated, {"height_cm": "170"})

    def test_unknown_input_rejected(self):
        validated = validate_formula(manifest_for("bmi"))
        with self.assertRaises(FormulaInputError):
            run_formula(
                validated,
                {"height_cm": "170", "weight_kg": "65", "age_years": "30"},
            )

    def test_out_of_range_input_rejected(self):
        validated = validate_formula(manifest_for("bmi"))
        with self.assertRaises(FormulaInputError):
            run_formula(validated, {"height_cm": "10", "weight_kg": "65"})

    def test_non_decimal_input_rejected(self):
        validated = validate_formula(manifest_for("bmi"))
        for bad in ("１７０", "1e3", "170cm", " 170", "170.12345"):
            with self.assertRaises(FormulaInputError):
                run_formula(validated, {"height_cm": bad, "weight_kg": "65"})

    def test_result_outside_output_range_rejected(self):
        validated = validate_formula(manifest_for("bmi"))
        # 250 cm / 2 kg → 0.32, below the declared output minimum of 5.
        with self.assertRaises(FormulaInputError):
            run_formula(validated, {"height_cm": "250", "weight_kg": "2"})

    def test_formula_result_carries_disclaimer_and_renderer(self):
        validated = validate_formula(manifest_for("bmi"))
        result = run_formula(validated, {"height_cm": "170", "weight_kg": "65"})
        self.assertTrue(result["disclaimer"])
        self.assertEqual(result["result_renderer"], "formula/v1")
        self.assertIsInstance(result["value"], str)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = deepcopy(manifest_for("bmi"))

    def test_unknown_function_rejected(self):
        self.manifest["formula"]["function"] = "nope"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_citation_must_match_registered_spec(self):
        self.manifest["formula"]["citation"] = "SOMETHING-ELSE"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_citation_must_appear_in_references(self):
        self.manifest["references"] = [
            {"title": "t", "url": "https://example.org", "section": "别的来源", "accessed": "2026-10-27"}
        ]
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_inputs_must_match_signature(self):
        self.manifest["formula"]["inputs"][0]["max"] = "999"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_input_count_must_match(self):
        self.manifest["formula"]["inputs"] = self.manifest["formula"]["inputs"][:1]
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_float_bound_rejected(self):
        self.manifest["formula"]["bands"][0]["lt"] = 18.5
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_band_gap_rejected(self):
        self.manifest["formula"]["bands"][1]["gte"] = "19"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_band_first_must_be_open_below(self):
        self.manifest["formula"]["bands"][0]["gte"] = "0"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_band_last_must_be_open_above(self):
        self.manifest["formula"]["bands"][-1]["lt"] = "60"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)

    def test_unknown_top_level_key_rejected(self):
        self.manifest["formula"]["expression"] = "w/(h*h)"
        with self.assertRaises(FormulaSpecError):
            validate_formula(self.manifest)


class FormTests(unittest.TestCase):
    def setUp(self):
        self.manifest = deepcopy(manifest_for("bmi"))

    def test_valid_pack_passes(self):
        spec = formula_form_spec(self.manifest)
        self.assertEqual(spec["form"], "formula_form/v1")
        self.assertEqual(spec["renderer"], "formula/v1")
        self.assertEqual(len(spec["inputs"]), 2)

    def test_input_schema_must_match(self):
        self.manifest["input_schema"]["properties"]["extra"] = {
            "type": "string",
            "title": "多出来的",
        }
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_input_schema_must_not_require_fields(self):
        self.manifest["input_schema"]["required"] = ["height_cm"]
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_output_schema_must_match_engine(self):
        self.manifest["output_schema"]["properties"]["extra"] = {"type": "string"}
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_patient_formula_needs_patient_form(self):
        self.manifest["patient_form"] = None
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_clinician_formula_must_not_have_patient_form(self):
        self.manifest["audience"] = "clinician"
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_missing_references_rejected(self):
        self.manifest["references"] = []
        with self.assertRaises(FormulaSpecError):
            formula_form_spec(self.manifest)

    def test_output_schema_constant_is_the_published_contract(self):
        # Packs restate this exact object; a change here is a breaking
        # change for every formula pack, so it is pinned deliberately.
        self.assertEqual(OUTPUT_SCHEMA["properties"]["result_renderer"]["enum"], ["formula/v1"])


if __name__ == "__main__":
    unittest.main()
