"""Declarative scale engine: contract validation and deterministic scoring."""

import unittest

from medagent.extensions.scale_engine import (
    DISCLAIMER,
    ScaleInputError,
    ScaleSpecError,
    run_scale,
    validate_scale,
)


def manifest(scale, **overrides):
    base = {
        "id": "calculate.demo",
        "name": "演示量表",
        "version": "1.0.0",
        "api_version": "medical-capability/v1",
        "kind": "calculation",
        "description": "演示",
        "permissions": [],
        "review_status": "engineering_verified_not_clinical",
        "ui": "calculation",
        "result_renderer": "scale/v1",
        "audience": "patient",
        "patient_form": "scale_form/v1",
        "aliases": ["演示量表"],
        "validation": {"status": "engineering_prototype_requires_review", "scope": "演示"},
        "references": [
            {"title": "t", "url": "https://example.org/a", "section": "s", "accessed": "2026-10-27"}
        ],
        "scale": scale,
    }
    base.update(overrides)
    return base


def choice_item(identity, values, label="条目", **extra):
    return {
        "id": identity,
        "label": label,
        "kind": "choice",
        "weight": extra.pop("weight", 1),
        "options": [{"value": value, "label": f"选项{value}"} for value in values],
        **extra,
    }


def simple_scale(items, bands, mode="sum", low=0, high=10):
    return {
        "engine": "scale-engine/v1",
        "aggregate": {
            "mode": mode,
            "min": low,
            "max": high,
            "unit": "分",
            "direction": "higher_is_worse",
        },
        "items": items,
        "bands": bands,
    }


class ContractTests(unittest.TestCase):
    def test_minimal_scale_validates(self):
        spec = validate_scale(
            manifest(
                simple_scale(
                    [choice_item("q1", [0, 1, 2])],
                    [{"min": 0, "max": 2, "key": "low", "label": "低"}],
                    high=2,
                )
            )
        )
        self.assertEqual(spec["items"][0]["id"], "q1")
        self.assertEqual(spec["mode"], "sum")

    def test_unknown_scale_key_rejected(self):
        scale = simple_scale([choice_item("q1", [0, 1])], [])
        scale["formula"] = "sum(items)"
        with self.assertRaises(ScaleSpecError):
            validate_scale(manifest(scale))

    def test_float_weight_rejected(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(simple_scale([choice_item("q1", [0, 1], weight=1.5)], []))
            )

    def test_boolean_weight_rejected(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(simple_scale([choice_item("q1", [0, 1], weight=True)], []))
            )

    def test_duplicate_item_id_rejected(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(
                    simple_scale(
                        [choice_item("q1", [0, 1]), choice_item("q1", [0, 1])], []
                    )
                )
            )

    def test_band_gap_rejected(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(
                    simple_scale(
                        [choice_item("q1", [0, 1, 2])],
                        [
                            {"min": 0, "max": 1, "key": "a", "label": "甲"},
                            {"min": 3, "max": 2, "key": "b", "label": "乙"},
                        ],
                        high=2,
                    )
                )
            )

    def test_band_overlap_rejected(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(
                    simple_scale(
                        [choice_item("q1", [0, 1, 2])],
                        [
                            {"min": 0, "max": 1, "key": "a", "label": "甲"},
                            {"min": 1, "max": 2, "key": "b", "label": "乙"},
                        ],
                        high=2,
                    )
                )
            )

    def test_band_must_reach_the_max(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(
                    simple_scale(
                        [choice_item("q1", [0, 1, 2])],
                        [{"min": 0, "max": 1, "key": "a", "label": "甲"}],
                        high=2,
                    )
                )
            )

    def test_expression_symbols_rejected_in_label(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(simple_scale([choice_item("q1", [0, 1], label="a;b")], []))
            )

    def test_code_words_rejected_in_label(self):
        with self.assertRaises(ScaleSpecError):
            validate_scale(
                manifest(simple_scale([choice_item("q1", [0, 1], label="eval(x)")], []))
            )

    def test_hyphen_and_slash_allowed_in_label(self):
        # Medical text legitimately contains these; nothing is evaluated.
        spec = validate_scale(
            manifest(simple_scale([choice_item("q1", [0, 1], label="PHQ-8 / mmol/L")], []))
        )
        self.assertEqual(spec["items"][0]["label"], "PHQ-8 / mmol/L")

    def test_threshold_table_requires_default(self):
        item = {
            "id": "rr",
            "label": "呼吸频率",
            "kind": "threshold_table",
            "thresholds": [{"lt": 12, "score": 1}],
        }
        with self.assertRaises(ScaleSpecError):
            validate_scale(manifest(simple_scale([item], [])))

    def test_threshold_default_must_be_last(self):
        item = {
            "id": "rr",
            "label": "呼吸频率",
            "kind": "threshold_table",
            "thresholds": [{"score": 0}, {"lt": 12, "score": 1}],
        }
        with self.assertRaises(ScaleSpecError):
            validate_scale(manifest(simple_scale([item], [])))

    def test_threshold_inverted_range_rejected(self):
        item = {
            "id": "rr",
            "label": "呼吸频率",
            "kind": "threshold_table",
            "thresholds": [{"gte": 20, "lt": 12, "score": 1}, {"score": 0}],
        }
        with self.assertRaises(ScaleSpecError):
            validate_scale(manifest(simple_scale([item], [])))

    def test_sum_percent_bands_must_cover_zero_to_hundred(self):
        scale = simple_scale(
            [choice_item("q1", [0, 1])],
            [{"min": 0, "max": 50, "key": "a", "label": "甲"}],
            mode="sum_percent",
            low=0,
            high=1,
        )
        with self.assertRaises(ScaleSpecError):
            validate_scale(manifest(scale))


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.spec = validate_scale(
            manifest(
                simple_scale(
                    [
                        choice_item("q1", [0, 1, 2, 3]),
                        choice_item("q2", [0, 1, 2, 3], weight=2),
                        choice_item("q3", [0, 1], reverse_of=1),
                    ],
                    [
                        {"min": 0, "max": 3, "key": "low", "label": "低"},
                        {"min": 4, "max": 11, "key": "high", "label": "高"},
                    ],
                    high=11,
                )
            )
        )

    def test_weighted_sum_and_reverse_scoring(self):
        result = run_scale(self.spec, {"q1": 3, "q2": 2, "q3": 0})
        # 3 + (2*2) + (1-0) = 8
        self.assertEqual(result["value"], "8")
        self.assertEqual(result["band"]["key"], "high")
        self.assertEqual(len(result["items"]), 3)
        self.assertEqual(result["items"][2]["score"], 1)
        self.assertEqual(result["disclaimer"], DISCLAIMER)

    def test_missing_item_fails_closed_without_partial_score(self):
        with self.assertRaises(ScaleInputError) as caught:
            run_scale(self.spec, {"q1": 1, "q2": 1})
        self.assertIn("未完成", str(caught.exception))

    def test_unknown_answer_key_rejected(self):
        with self.assertRaises(ScaleInputError):
            run_scale(self.spec, {"q1": 1, "q2": 1, "q3": 1, "q9": 0})

    def test_answer_outside_options_rejected(self):
        with self.assertRaises(ScaleInputError):
            run_scale(self.spec, {"q1": 9, "q2": 1, "q3": 1})

    def test_float_answer_to_choice_rejected(self):
        with self.assertRaises(ScaleInputError):
            run_scale(self.spec, {"q1": 1.5, "q2": 1, "q3": 1})

    def test_boolean_answer_rejected(self):
        with self.assertRaises(ScaleInputError):
            run_scale(self.spec, {"q1": True, "q2": 1, "q3": 1})

    def test_threshold_table_first_match_wins(self):
        spec = validate_scale(
            manifest(
                simple_scale(
                    [
                        {
                            "id": "temp",
                            "label": "体温",
                            "kind": "threshold_table",
                            "thresholds": [
                                {"lt": 35.0, "score": 3},
                                {"gte": 35.0, "lt": 36.1, "score": 1},
                                {"gte": 36.1, "lt": 38.1, "score": 0},
                                {"gte": 38.1, "score": 2},
                            ],
                        }
                    ],
                    [{"min": 0, "max": 3, "key": "a", "label": "甲"}],
                    high=3,
                )
            )
        )
        self.assertEqual(run_scale(spec, {"temp": 34.9})["value"], "3")
        self.assertEqual(run_scale(spec, {"temp": 36.5})["value"], "0")
        self.assertEqual(run_scale(spec, {"temp": 39.2})["value"], "2")

    def test_sum_percent_rounds_half_up(self):
        spec = validate_scale(
            manifest(
                simple_scale(
                    [choice_item("q1", [0, 1, 2, 3, 4, 5])],
                    [
                        {"min": 0, "max": 49, "key": "a", "label": "甲"},
                        {"min": 50, "max": 100, "key": "b", "label": "乙"},
                    ],
                    mode="sum_percent",
                    high=5,
                )
            )
        )
        result = run_scale(spec, {"q1": 1})
        self.assertEqual(result["value"], "20")
        self.assertEqual(result["unit"], "%")
        self.assertEqual(result["max_score"], 100)

    def test_band_absent_yields_empty_object(self):
        spec = validate_scale(
            manifest(simple_scale([choice_item("q1", [0, 1])], [], high=1))
        )
        result = run_scale(spec, {"q1": 1})
        self.assertEqual(result["band"], {})


if __name__ == "__main__":
    unittest.main()
