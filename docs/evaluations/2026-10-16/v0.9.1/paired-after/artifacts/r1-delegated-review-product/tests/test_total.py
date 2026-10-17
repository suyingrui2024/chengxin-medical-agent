import unittest
from decimal import Decimal, getcontext

from invoice import total


class TotalTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), "0.00")

    def test_zero_price(self):
        self.assertEqual(total([{"price": "0", "quantity": 1000}]), "0.00")

    def test_exact_multiplication_no_float_error(self):
        # float would give 0.30000000000000004
        self.assertEqual(total([{"price": "0.10", "quantity": 3}]), "0.30")
        self.assertEqual(total([{"price": "8.20", "quantity": 7}]), "57.40")

    def test_only_total_is_rounded(self):
        # per-line rounding would give 0.02
        lines = [{"price": "0.005", "quantity": 1}, {"price": "0.005", "quantity": 1}]
        self.assertEqual(total(lines), "0.01")
        # per-line rounding would give 1.01 + 1.01; exact rounding gives 2.01
        self.assertEqual(
            total([{"price": "1.005", "quantity": 1}, {"price": "1.005", "quantity": 1}]),
            "2.01",
        )

    def test_half_up(self):
        self.assertEqual(total([{"price": "1.005", "quantity": 1}]), "1.01")
        self.assertEqual(total([{"price": "1.004", "quantity": 1}]), "1.00")
        self.assertEqual(total([{"price": "0.015", "quantity": 1}]), "0.02")

    def test_large_values_exceed_float(self):
        # float arithmetic yields 1234567890123457024.00 here
        self.assertEqual(
            total([{"price": "1234567890123456.999999", "quantity": 1000}]),
            "1234567890123457000.00",
        )
        # exact product 2469135780246913.999998, then one final half-up round
        self.assertEqual(
            total([{"price": "1234567890123456.999999", "quantity": 2}]),
            "2469135780246914.00",
        )
        # exact sum stays below the cent boundary: no premature rounding
        self.assertEqual(
            total([{"price": "1234567890123456.999994", "quantity": 1}]),
            "1234567890123457.00",
        )
        self.assertEqual(
            total([{"price": "9999999999999999.999999", "quantity": 1000}]),
            "10000000000000000000.00",
        )
        self.assertEqual(total([{"price": "0.000001", "quantity": 1}]), "0.00")
        # rounds up only at the cent boundary (third decimal is 5)
        self.assertEqual(total([{"price": "0.005000", "quantity": 1}]), "0.01")
        self.assertEqual(total([{"price": "0.005000", "quantity": 0}]), "0.00")

    def test_accumulation_rounds_once(self):
        lines = [{"price": "0.000001", "quantity": 1}] * 999
        self.assertEqual(total(lines), "0.00")
        self.assertEqual(total(lines + [{"price": "0.000001", "quantity": 1}]), "0.00")
        self.assertEqual(total([{"price": "0.000001", "quantity": 1000}]), "0.00")

    def test_quantity_bounds(self):
        self.assertEqual(total([{"price": "1.00", "quantity": 0}]), "0.00")
        self.assertEqual(total([{"price": "1.00", "quantity": 1000}]), "1000.00")
        for bad in (1001, -1):
            with self.assertRaises(ValueError):
                total([{"price": "1.00", "quantity": bad}])

    def test_bool_quantity_rejected(self):
        for bad in (True, False):
            with self.assertRaises(ValueError):
                total([{"price": "1.00", "quantity": bad}])

    def test_invalid_quantity_rejected(self):
        for bad in ("2", 2.0, Decimal(2), None, [], {}):
            with self.assertRaises(ValueError):
                total([{"price": "1.00", "quantity": bad}])

    def test_invalid_price_rejected(self):
        for bad in (
            "-1.00", "+1.00", "1e3", "nan", "inf", "-inf", "NaN", "", " ", "1.2345678",
            "1.", ".5", "1,5", "0x10", "１２", 1.0, 1, None, True, Decimal("1.5"),
            "12345678901234567.0",  # 17 integer digits
        ):
            with self.assertRaises(ValueError):
                total([{"price": bad, "quantity": 1}])

    def test_non_finite_and_negative_via_strings(self):
        for bad in ("-0.01", "-0", "infinity", "sNaN"):
            with self.assertRaises(ValueError):
                total([{"price": bad, "quantity": 1}])

    def test_malformed_lines_rejected(self):
        with self.assertRaises(ValueError):
            total([{"price": "1.00"}])
        with self.assertRaises(ValueError):
            total([{"quantity": 1}])
        with self.assertRaises(ValueError):
            total(["nope"])
        with self.assertRaises(ValueError):
            total([None])

    def test_caller_precision_is_irrelevant_and_unchanged(self):
        lines = [{"price": "1234567890123456.999999", "quantity": 1000}]
        expected = "1234567890123457000.00"
        for prec in (8, 28, 1):
            getcontext().prec = prec
            self.assertEqual(total(lines), expected)
            self.assertEqual(getcontext().prec, prec)
        getcontext().prec = 28

    def test_string_type_and_two_decimals(self):
        result = total([{"price": "2", "quantity": 3}])
        self.assertIsInstance(result, str)
        self.assertEqual(result, "6.00")
        self.assertRegex(result, r"\A\d+\.\d{2}\Z")


if __name__ == "__main__":
    unittest.main()
