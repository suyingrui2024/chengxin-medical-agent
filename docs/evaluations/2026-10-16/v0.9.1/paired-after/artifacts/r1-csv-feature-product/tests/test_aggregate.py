import io, json, subprocess, sys, unittest
from decimal import Decimal
from aggregate import aggregate
from reader import read_rows


class AggregateTests(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(aggregate([]), [])
        self.assertEqual(aggregate([{"group": "  ", "value": "1"}]), [])

    def test_groups_sorted_lexicographically(self):
        rows = [
            {"group": "b", "value": "2"},
            {"group": "a", "value": "1"},
            {"group": "A", "value": "3"},
        ]
        self.assertEqual([g["group"] for g in aggregate(rows)], ["A", "a", "b"])

    def test_group_whitespace_stripped(self):
        rows = [{"group": " a ", "value": "1"}, {"group": "a", "value": "3"}]
        self.assertEqual(aggregate(rows), [
            {"group": "a", "count": 2, "min": "1", "max": "3", "mean": "2.00"}])

    def test_keys_are_exact(self):
        out = aggregate([{"group": "g", "value": "1"}])[0]
        self.assertEqual(set(out), {"group", "count", "min", "max", "mean"})

    def test_missing_fields_and_bad_values_ignored(self):
        rows = [
            {"group": "g", "value": "1"},
            {"group": "g"},                      # missing value
            {"value": "5"},                      # missing group
            {"group": None, "value": "5"},
            {"group": "g", "value": None},
            {"group": "g", "value": ""},
            {"group": "g", "value": "  "},
            {"group": "g", "value": "abc"},
            {"group": "g", "value": "1e3"},      # exponent notation
            {"group": "g", "value": "NaN"},
            {"group": "g", "value": "Infinity"},
            {"group": "g", "value": "-Infinity"},
            {"group": "g", "value": "0x10"},
            {"group": "g", "value": "1,5"},
            {"group": "g", "value": "1.2.3"},
            "not-a-dict",
        ]
        self.assertEqual(aggregate(rows), [
            {"group": "g", "count": 1, "min": "1", "max": "1", "mean": "1.00"}])

    def test_extreme_valid_range(self):
        rows = [
            {"group": "g", "value": "1234567890123456.1234"},
            {"group": "g", "value": "-1234567890123456.4321"},
        ]
        self.assertEqual(aggregate(rows), [{
            "group": "g",
            "count": 2,
            "min": "-1234567890123456.4321",
            "max": "1234567890123456.1234",
            "mean": "-0.15",
        }])

    def test_trailing_zeros_removed_for_min_max(self):
        rows = [
            {"group": "g", "value": "-2.5000"},
            {"group": "g", "value": "+3.0000"},
            {"group": "g", "value": "1.0"},
        ]
        out = aggregate(rows)[0]
        self.assertEqual(out["min"], "-2.5")
        self.assertEqual(out["max"], "3")

    def test_mean_half_up_rounding(self):
        rows = [{"group": "g", "value": "1"}, {"group": "g", "value": "1"},
                {"group": "g", "value": "1"}, {"group": "g", "value": "1"},
                {"group": "g", "value": "1"}, {"group": "g", "value": "1"},
                {"group": "g", "value": "1"}, {"group": "g", "value": "2"}]
        self.assertEqual(aggregate(rows)[0]["mean"], "1.13")  # 1.125 -> half up
        neg = [{"group": "g", "value": "-1"}, {"group": "g", "value": "-1"},
               {"group": "g", "value": "-1"}, {"group": "g", "value": "-1"},
               {"group": "g", "value": "-1"}, {"group": "g", "value": "-1"},
               {"group": "g", "value": "-1"}, {"group": "g", "value": "-2"}]
        self.assertEqual(aggregate(neg)[0]["mean"], "-1.13")

    def test_zero_mean_is_0_00(self):
        rows = [{"group": "g", "value": "-1"}, {"group": "g", "value": "1"}]
        out = aggregate(rows)[0]
        self.assertEqual(out["mean"], "0.00")
        self.assertEqual(out["min"], "-1")
        self.assertEqual(out["max"], "1")

    def test_negative_zero_normalised(self):
        out = aggregate([{"group": "g", "value": "-0.0"},
                         {"group": "g", "value": "0"}])[0]
        self.assertEqual((out["min"], out["max"], out["mean"]), ("0", "0", "0.00"))

    def test_non_string_values_ignored(self):
        rows = [{"group": "g", "value": 1}, {"group": "g", "value": Decimal("1")}]
        self.assertEqual(aggregate(rows), [])


class CliTests(unittest.TestCase):
    def test_cli_reads_stdin_csv(self):
        csv_text = "group,value\n b ,2\n a ,1\n b ,4\n a ,bad\n ,7\n"
        proc = subprocess.run([sys.executable, "cli.py"], input=csv_text,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"groups": [
            {"group": "a", "count": 1, "min": "1", "max": "1", "mean": "1.00"},
            {"group": "b", "count": 2, "min": "2", "max": "4", "mean": "3.00"},
        ]})

    def test_cli_empty_input(self):
        proc = subprocess.run([sys.executable, "cli.py"], input="",
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"groups": []})

    def test_reader_api_unchanged(self):
        self.assertEqual(read_rows(io.StringIO("group,value\na,1\n")),
                         [{"group": "a", "value": "1"}])


if __name__ == "__main__":
    unittest.main()
