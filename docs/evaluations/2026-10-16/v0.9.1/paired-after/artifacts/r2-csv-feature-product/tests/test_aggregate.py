import io, json, subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aggregate import aggregate
from reader import read_rows

ROOT = Path(__file__).resolve().parents[1]


class AggregateTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(aggregate([]), [])

    def test_missing_and_invalid_fields_ignored(self):
        rows = [
            {"group": "a", "value": "1"},
            {"value": "2"},                      # missing group
            {"group": "   ", "value": "2"},      # blank group
            {"group": "a"},                      # missing value
            {"group": "a", "value": "abc"},      # non-decimal
            {"group": "a", "value": "0x10"},     # non-decimal
            {"group": "a", "value": "NaN"},
            {"group": "a", "value": "Infinity"},
            {"group": "a", "value": "-inf"},
            {"group": "a", "value": ""},
            {"group": "a", "value": "1e3"},      # exponent notation
            None,
        ]
        self.assertEqual(aggregate(rows), [
            {"group": "a", "count": 1, "min": "1", "max": "1", "mean": "1.00"},
        ])

    def test_grouping_trim_sort_and_stats(self):
        rows = [
            {"group": " b ", "value": "2"},
            {"group": "a", "value": "-1.5000"},
            {"group": "b", "value": " 4 "},
            {"group": "a", "value": "3"},
            {"group": "B", "value": "1"},
        ]
        self.assertEqual(aggregate(rows), [
            {"group": "B", "count": 1, "min": "1", "max": "1", "mean": "1.00"},
            {"group": "a", "count": 2, "min": "-1.5", "max": "3", "mean": "0.75"},
            {"group": "b", "count": 2, "min": "2", "max": "4", "mean": "3.00"},
        ])

    def test_min_max_trim_trailing_zeros(self):
        rows = [
            {"group": "g", "value": "1.5000"},
            {"group": "g", "value": "2.2500"},
            {"group": "g", "value": "10.0000"},
        ]
        got = aggregate(rows)[0]
        self.assertEqual((got["min"], got["max"]), ("1.5", "10"))
        self.assertEqual(got["mean"], "4.58")

    def test_zero_outputs(self):
        rows = [
            {"group": "z", "value": "0.0000"},
            {"group": "z", "value": "-0"},
            {"group": "neg", "value": "-1"},
            {"group": "neg", "value": "1"},
        ]
        self.assertEqual(aggregate(rows), [
            {"group": "neg", "count": 2, "min": "-1", "max": "1", "mean": "0.00"},
            {"group": "z", "count": 2, "min": "0", "max": "0", "mean": "0.00"},
        ])

    def test_mean_round_half_up(self):
        # 0.125 -> 0.13 (half-up, not banker's rounding)
        self.assertEqual(aggregate([{"group": "g", "value": "0.125"}])[0]["mean"], "0.13")
        # 1/3 -> 1.67
        self.assertEqual(
            aggregate([{"group": "g", "value": v} for v in ("1", "0", "0")])[0]["mean"],
            "0.33",
        )
        # exactly representable third case with repeating expansion
        rows = [{"group": "g", "value": v} for v in ("1", "2", "2")]
        self.assertEqual(aggregate(rows)[0]["mean"], "1.67")
        # negative half-up rounds away from zero at the .xx5 boundary
        rows = [{"group": "g", "value": v} for v in ("-0.125", "0")]
        self.assertEqual(aggregate(rows)[0]["mean"], "-0.06")

    def test_large_16_digit_values(self):
        rows = [
            {"group": "g", "value": "9999999999999999.9999"},
            {"group": "g", "value": "-9999999999999999.9999"},
        ]
        got = aggregate(rows)[0]
        self.assertEqual(got["count"], 2)
        self.assertEqual(got["min"], "-9999999999999999.9999")
        self.assertEqual(got["max"], "9999999999999999.9999")
        self.assertEqual(got["mean"], "0.00")

    def test_key_order_of_items(self):
        item = aggregate([{"group": "g", "value": "1"}])[0]
        self.assertEqual(list(item), ["group", "count", "min", "max", "mean"])


class CliTests(unittest.TestCase):
    def run_cli(self, text):
        proc = subprocess.run(
            [sys.executable, "cli.py"],
            cwd=ROOT,
            input=text,
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_cli_end_to_end(self):
        csv_text = "group,value\n a ,1.5\nb,2\n a ,-0.5\nc,oops\n"
        self.assertEqual(self.run_cli(csv_text), {"groups": [
            {"group": "a", "count": 2, "min": "-0.5", "max": "1.5", "mean": "0.50"},
            {"group": "b", "count": 1, "min": "2", "max": "2", "mean": "2.00"},
        ]})

    def test_cli_empty_input(self):
        self.assertEqual(self.run_cli("group,value\n"), {"groups": []})

    def test_cli_uses_reader(self):
        rows = read_rows(io.StringIO("group,value\nx,3\n"))
        self.assertEqual(rows, [{"group": "x", "value": "3"}])


if __name__ == "__main__":
    unittest.main()
