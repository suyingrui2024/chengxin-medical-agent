import json
import subprocess
import sys
import unittest
from pathlib import Path

from aggregate import aggregate

ROOT = Path(__file__).resolve().parent.parent


class AggregateTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(aggregate([]), [])

    def test_basic_grouping_and_stats(self):
        rows = [
            {"group": "b", "value": "3"},
            {"group": "a", "value": "1"},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [
                {"group": "a", "count": 2, "min": "1", "max": "2", "mean": "1.50"},
                {"group": "b", "count": 1, "min": "3", "max": "3", "mean": "3.00"},
            ],
        )

    def test_group_is_stripped(self):
        rows = [{"group": "  a  ", "value": "1"}, {"group": "a", "value": "3"}]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 2, "min": "1", "max": "3", "mean": "2.00"}],
        )

    def test_empty_group_ignored(self):
        rows = [
            {"group": "", "value": "1"},
            {"group": "   ", "value": "2"},
            {"group": "a", "value": "3"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "3", "max": "3", "mean": "3.00"}],
        )

    def test_missing_fields_ignored(self):
        rows = [
            {"value": "1"},
            {"group": "a"},
            {"group": "a", "value": "2"},
            {"group": "b"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "2", "max": "2", "mean": "2.00"}],
        )

    def test_invalid_and_non_finite_ignored(self):
        rows = [
            {"group": "a", "value": "abc"},
            {"group": "a", "value": ""},
            {"group": "a", "value": "1.2.3"},
            {"group": "a", "value": "NaN"},
            {"group": "a", "value": "Infinity"},
            {"group": "a", "value": "-Infinity"},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "2", "max": "2", "mean": "2.00"}],
        )

    def test_negative_values(self):
        rows = [{"group": "a", "value": "-2"}, {"group": "a", "value": "-4"}]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 2, "min": "-4", "max": "-2", "mean": "-3.00"}],
        )

    def test_min_max_strip_trailing_zeros(self):
        rows = [
            {"group": "a", "value": "1.5000"},
            {"group": "a", "value": "2.0000"},
            {"group": "a", "value": "-0.0000"},
        ]
        stats = aggregate(rows)[0]
        self.assertEqual(stats["min"], "0")
        self.assertEqual(stats["max"], "2")
        self.assertEqual(stats["mean"], "1.17")

    def test_round_half_up(self):
        cases = [
            (["0", "0.01"], "0.01"),
            (["0.01", "0.02"], "0.02"),
            (["-0.01", "0"], "-0.01"),
        ]
        for values, expected in cases:
            rows = [{"group": "g", "value": v} for v in values]
            self.assertEqual(aggregate(rows)[0]["mean"], expected, values)

    def test_zero_mean_prints_as_0_00(self):
        rows = [{"group": "a", "value": "1"}, {"group": "a", "value": "-1"}]
        self.assertEqual(aggregate(rows)[0]["mean"], "0.00")
        rows = [{"group": "a", "value": "-0.001"}]
        self.assertEqual(aggregate(rows)[0]["mean"], "0.00")

    def test_high_precision_is_exact(self):
        rows = [
            {"group": "a", "value": "1234567890123456.7891"},
            {"group": "a", "value": "0.0001"},
        ]
        stats = aggregate(rows)[0]
        self.assertEqual(stats["min"], "0.0001")
        self.assertEqual(stats["max"], "1234567890123456.7891")
        self.assertEqual(stats["mean"], "617283945061728.39")

    def test_sorted_lexicographically(self):
        rows = [
            {"group": "b", "value": "1"},
            {"group": "A", "value": "1"},
            {"group": "a", "value": "1"},
        ]
        self.assertEqual([r["group"] for r in aggregate(rows)], ["A", "a", "b"])

    def test_item_has_exact_keys(self):
        stats = aggregate([{"group": "a", "value": "1"}])[0]
        self.assertEqual(list(stats.keys()), ["group", "count", "min", "max", "mean"])
        self.assertIsInstance(stats["count"], int)

    def test_cli_reads_stdin_csv(self):
        csv_text = "group,value\nc,3\nc,5\n"
        proc = subprocess.run(
            [sys.executable, "cli.py"],
            input=csv_text,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            {
                "groups": [
                    {"group": "c", "count": 2, "min": "3", "max": "5", "mean": "4.00"}
                ]
            },
        )


if __name__ == "__main__":
    unittest.main()
