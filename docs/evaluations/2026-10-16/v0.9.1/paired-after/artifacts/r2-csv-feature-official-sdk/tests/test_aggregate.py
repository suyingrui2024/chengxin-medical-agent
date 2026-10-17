import unittest

from aggregate import aggregate


class AggregateTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(aggregate([]), [])

    def test_basic_grouping_and_sorting(self):
        rows = [
            {"group": "b", "value": "2"},
            {"group": "a", "value": "1"},
            {"group": "b", "value": "4"},
            {"group": "a", "value": "3"},
        ]
        self.assertEqual(
            aggregate(rows),
            [
                {"group": "a", "count": 2, "min": "1", "max": "3", "mean": "2.00"},
                {"group": "b", "count": 2, "min": "2", "max": "4", "mean": "3.00"},
            ],
        )

    def test_lexicographic_group_order(self):
        rows = [
            {"group": "b", "value": "1"},
            {"group": "a", "value": "1"},
            {"group": "A", "value": "1"},
            {"group": "B", "value": "1"},
        ]
        self.assertEqual([item["group"] for item in aggregate(rows)], ["A", "B", "a", "b"])

    def test_group_stripped_and_empty_groups_ignored(self):
        rows = [
            {"group": " a ", "value": "1"},
            {"group": "a", "value": "3"},
            {"group": "   ", "value": "5"},
            {"group": "", "value": "7"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 2, "min": "1", "max": "3", "mean": "2.00"}],
        )

    def test_missing_fields_ignored(self):
        rows = [
            {"group": "a"},
            {"value": "1"},
            {"group": "a", "value": None},
            {"group": None, "value": "9"},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "2", "max": "2", "mean": "2.00"}],
        )

    def test_invalid_values_ignored(self):
        rows = [
            {"group": "a", "value": "NaN"},
            {"group": "a", "value": "Infinity"},
            {"group": "a", "value": "-inf"},
            {"group": "a", "value": "abc"},
            {"group": "a", "value": ""},
            {"group": "a", "value": "1e3"},
            {"group": "a", "value": "0x10"},
            {"group": "a", "value": "1.2.3"},
            {"group": "a", "value": " 1.5 "},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "2", "max": "2", "mean": "2.00"}],
        )

    def test_group_with_only_invalid_values_excluded(self):
        rows = [{"group": "a", "value": "nope"}, {"group": "b", "value": "Infinity"}]
        self.assertEqual(aggregate(rows), [])

    def test_trailing_zeros_removed_min_max(self):
        rows = [
            {"group": "a", "value": "1.5000"},
            {"group": "a", "value": "1.2500"},
            {"group": "a", "value": "2.0000"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 3, "min": "1.25", "max": "2", "mean": "1.58"}],
        )

    def test_half_up_rounding(self):
        rows = [{"group": "a", "value": "1.005"}]
        self.assertEqual(aggregate(rows)[0]["mean"], "1.01")
        rows = [{"group": "a", "value": "2.675"}]
        self.assertEqual(aggregate(rows)[0]["mean"], "2.68")

    def test_zero_mean_and_zero_min_max(self):
        rows = [
            {"group": "a", "value": "-1"},
            {"group": "a", "value": "1"},
            {"group": "a", "value": "-0.0"},
            {"group": "a", "value": "0.00"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 4, "min": "-1", "max": "1", "mean": "0.00"}],
        )

    def test_negative_values(self):
        rows = [
            {"group": "a", "value": "-5.5"},
            {"group": "a", "value": "-2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 2, "min": "-5.5", "max": "-2", "mean": "-3.75"}],
        )

    def test_large_16_digit_values(self):
        rows = [
            {"group": "a", "value": "1234567890123456"},
            {"group": "a", "value": "1234567890123458"},
        ]
        self.assertEqual(
            aggregate(rows),
            [
                {
                    "group": "a",
                    "count": 2,
                    "min": "1234567890123456",
                    "max": "1234567890123458",
                    "mean": "1234567890123457.00",
                }
            ],
        )

    def test_exact_item_keys(self):
        rows = [{"group": "a", "value": "1"}]
        item = aggregate(rows)[0]
        self.assertEqual(
            list(item.keys()), ["group", "count", "min", "max", "mean"]
        )
        self.assertIsInstance(item["count"], int)


if __name__ == "__main__":
    unittest.main()
