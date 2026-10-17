import io, json, subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aggregate import aggregate
from reader import read_rows

ROOT = Path(__file__).resolve().parent.parent


class AggregateTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(aggregate([]), [])

    def test_grouping_sorted_and_stripped(self):
        rows = [
            {"group": " b ", "value": "2"},
            {"group": "a", "value": "1"},
            {"group": "a", "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [
                {"group": "a", "count": 2, "min": "1", "max": "2", "mean": "1.50"},
                {"group": "b", "count": 1, "min": "2", "max": "2", "mean": "2.00"},
            ],
        )

    def test_exact_keys_order(self):
        out = aggregate([{"group": "g", "value": "1"}])
        self.assertEqual(list(out[0]), ["group", "count", "min", "max", "mean"])

    def test_blank_group_ignored(self):
        rows = [
            {"group": "", "value": "1"},
            {"group": "   ", "value": "2"},
            {"group": "ok", "value": "3"},
        ]
        self.assertEqual([r["group"] for r in aggregate(rows)], ["ok"])

    def test_missing_fields_ignored(self):
        rows = [
            {"group": "a"},
            {"value": "5"},
            {"group": "a", "value": "1"},
            {},
            {"group": None, "value": "2"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "1", "max": "1", "mean": "1.00"}],
        )

    def test_non_decimal_and_special_values_ignored(self):
        rows = [
            {"group": "a", "value": "abc"},
            {"group": "a", "value": "NaN"},
            {"group": "a", "value": "Infinity"},
            {"group": "a", "value": "-Infinity"},
            {"group": "a", "value": "0x10"},
            {"group": "a", "value": "1e3"},
            {"group": "a", "value": ""},
            {"group": "a", "value": "  "},
            {"group": "a", "value": None},
            {"group": "a", "value": "1"},
        ]
        self.assertEqual(
            aggregate(rows),
            [{"group": "a", "count": 1, "min": "1", "max": "1", "mean": "1.00"}],
        )

    def test_min_max_trim_trailing_zeros(self):
        rows = [
            {"group": "g", "value": "1.5000"},
            {"group": "g", "value": "-2.250"},
            {"group": "g", "value": "0.0"},
        ]
        out = aggregate(rows)[0]
        self.assertEqual(out["min"], "-2.25")
        self.assertEqual(out["max"], "1.5")

    def test_negative_zero_and_zero_mean(self):
        out = aggregate([{"group": "g", "value": "-0.000"}])[0]
        self.assertEqual(out["min"], "0")
        self.assertEqual(out["max"], "0")
        self.assertEqual(out["mean"], "0.00")

    def test_mean_round_half_up(self):
        out = aggregate(
            [{"group": "g", "value": "1"}, {"group": "g", "value": "2"}]
        )[0]
        self.assertEqual(out["mean"], "1.50")
        # 0.125 -> 0.13 under ROUND_HALF_UP (binary floats would give 0.12)
        out = aggregate(
            [{"group": "g", "value": "0.12"}, {"group": "g", "value": "0.13"}]
        )[0]
        self.assertEqual(out["mean"], "0.13")

    def test_mean_exact_decimal_precision(self):
        # mean is exactly 0.005 in Decimal (not a binary float), so
        # ROUND_HALF_UP gives 0.01; a float implementation would drift to 0.00.
        out = aggregate(
            [{"group": "g", "value": "0.01"}, {"group": "g", "value": "0"}]
        )[0]
        self.assertEqual(out["mean"], "0.01")
        # mean is exactly 0.0025 -> rounds down to 0.00
        out = aggregate(
            [{"group": "g", "value": "0.005"}, {"group": "g", "value": "0"}]
        )[0]
        self.assertEqual(out["mean"], "0.00")

    def test_large_and_signed_values(self):
        rows = [
            {"group": "g", "value": "9999999999999999.9999"},
            {"group": "g", "value": "-9999999999999999.9999"},
        ]
        out = aggregate(rows)[0]
        self.assertEqual(out["min"], "-9999999999999999.9999")
        self.assertEqual(out["max"], "9999999999999999.9999")
        self.assertEqual(out["mean"], "0.00")

    def test_input_list_not_mutated(self):
        rows = [{"group": " g ", "value": " 1.500 "}]
        aggregate(rows)
        self.assertEqual(rows, [{"group": " g ", "value": " 1.500 "}])

    def test_reader_integration(self):
        rows = read_rows(io.StringIO("group,value\n a ,1.500 \n,2\nb,oops\n"))
        self.assertEqual(
            aggregate(rows),
            [
                {"group": "a", "count": 1, "min": "1.5", "max": "1.5", "mean": "1.50"},
            ],
        )


class CliTests(unittest.TestCase):
    def run_cli(self, data):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "cli.py")],
            input=data,
            capture_output=True,
            text=True,
            cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_reads_csv_from_stdin(self):
        out = self.run_cli("group,value\nb,2\n a ,1.500\n,3\nc,x\n")
        self.assertEqual(
            out,
            {
                "groups": [
                    {"group": "a", "count": 1, "min": "1.5", "max": "1.5", "mean": "1.50"},
                    {"group": "b", "count": 1, "min": "2", "max": "2", "mean": "2.00"},
                ]
            },
        )

    def test_cli_empty_input(self):
        self.assertEqual(self.run_cli("group,value\n"), {"groups": []})


if __name__ == "__main__":
    unittest.main()
