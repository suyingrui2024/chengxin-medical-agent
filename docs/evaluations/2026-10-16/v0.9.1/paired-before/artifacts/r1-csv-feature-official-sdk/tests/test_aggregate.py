import unittest
from aggregate import aggregate


class AggregateTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(aggregate([]), [])

    def test_groups_sorted_and_shaped(self):
        rows = [
            {"group": "b", "value": "3"},
            {"group": "a", "value": "1"},
            {"group": "b", "value": "7"},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [
                {"group": "a", "count": 2, "min": "1", "max": "2", "mean": "1.50"},
                {"group": "b", "count": 2, "min": "3", "max": "7", "mean": "5.00"},
            ],
        )

    def test_group_is_stripped_and_merged(self):
        rows = [
            {"group": " a ", "value": "1"},
            {"group": "a", "value": "3"},
            {"group": "\ta\n", "value": "2"},
        ]
        result = aggregate(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["group"], "a")
        self.assertEqual(result[0]["count"], 3)
        self.assertEqual(result[0]["min"], "1")
        self.assertEqual(result[0]["max"], "3")
        self.assertEqual(result[0]["mean"], "2.00")

    def test_ignores_empty_groups_missing_fields_and_bad_values(self):
        rows = [
            {"group": "", "value": "1"},
            {"group": "   ", "value": "1"},
            {"group": "a", "value": ""},
            {"group": "a", "value": "   "},
            {"group": "a", "value": "abc"},
            {"group": "a", "value": "NaN"},
            {"group": "a", "value": "Infinity"},
            {"group": "a", "value": "-inf"},
            {"group": "a", "value": "1e3"},
            {"group": "a"},
            {"value": "5"},
            {"group": "a", "value": None},
            {"group": None, "value": "5"},
            {"group": "a", "value": "2.5"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "2.5", "max": "2.5", "mean": "2.50"}],
        )

    def test_min_max_strip_trailing_zeros(self):
        rows = [
            {"group": "g", "value": "1.500"},
            {"group": "g", "value": "-2.2500"},
            {"group": "g", "value": "010.00"},
        ]
        result = aggregate(rows)[0]
        self.assertEqual(result["min"], "-2.25")
        self.assertEqual(result["max"], "10")
        self.assertEqual(result["mean"], "3.08")

    def test_mean_round_half_up(self):
        rows = [
            {"group": "g", "value": "1.005"},
            {"group": "g", "value": "0"},
        ]
        # Exact mean is 0.5025 -> ROUND_HALF_UP to 2 places -> 0.50
        self.assertEqual(aggregate(rows)[0]["mean"], "0.50")

        rows = [{"group": "g", "value": "1.005"}]
        # Single value 1.005 -> 1.01 (half up)
        self.assertEqual(aggregate(rows)[0]["mean"], "1.01")

    def test_zero_mean_is_normalised(self):
        rows = [
            {"group": "g", "value": "-0.00"},
            {"group": "g", "value": "0"},
        ]
        result = aggregate(rows)[0]
        self.assertEqual(result["min"], "0")
        self.assertEqual(result["max"], "0")
        self.assertEqual(result["mean"], "0.00")

    def test_negative_values(self):
        rows = [
            {"group": "g", "value": "-1.5"},
            {"group": "g", "value": "-2.5"},
        ]
        result = aggregate(rows)[0]
        self.assertEqual(result["min"], "-2.5")
        self.assertEqual(result["max"], "-1.5")
        self.assertEqual(result["mean"], "-2.00")

    def test_entry_has_exactly_expected_keys(self):
        result = aggregate([{"group": "g", "value": "1"}])
        self.assertEqual(set(result[0]), {"group", "count", "min", "max", "mean"})

    def test_large_values_within_contract(self):
        rows = [
            {"group": "g", "value": "1234567890123456.1234"},
            {"group": "g", "value": "-9999999999999999.9999"},
        ]
        result = aggregate(rows)[0]
        self.assertEqual(result["min"], "-9999999999999999.9999")
        self.assertEqual(result["max"], "1234567890123456.1234")


if __name__ == "__main__":
    unittest.main()
