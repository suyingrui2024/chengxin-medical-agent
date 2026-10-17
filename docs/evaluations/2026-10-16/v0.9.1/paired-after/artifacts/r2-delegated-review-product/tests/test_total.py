import unittest
from decimal import Decimal, getcontext

from invoice import total


class ExactnessTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), "0.00")

    def test_zero_quantity(self):
        self.assertEqual(total([{"price": "9.99", "quantity": 0}]), "0.00")

    def test_single_line_two_decimals(self):
        self.assertEqual(total([{"price": "1.25", "quantity": 2}]), "2.50")

    def test_full_precision_large_price(self):
        # float math would give 9007199254740994.00 here
        self.assertEqual(total([{"price": "9007199254740993.99", "quantity": 1}]),
                         "9007199254740993.99")

    def test_16_digit_integer_6_decimals(self):
        # 9999999999999999.999999 * 1000 = 9999999999999999999.999 -> half-up
        self.assertEqual(
            total([{"price": "9999999999999999.999999", "quantity": 1000}]),
            "10000000000000000000.00",
        )

    def test_fractional_accumulation_is_exact(self):
        self.assertEqual(total([{"price": "0.1", "quantity": 3}]), "0.30")
        self.assertEqual(
            total([{"price": "0.001", "quantity": 1000}]), "1.00"
        )

    def test_no_per_line_rounding(self):
        # 0.005 + 0.005 = 0.010 -> "0.01"; per-line rounding would give "0.02"
        lines = [{"price": "0.005", "quantity": 1}] * 2
        self.assertEqual(total(lines), "0.01")

    def test_no_per_line_rounding_three_halves(self):
        # 3 * 0.005 = 0.015 -> half-up on the total -> "0.02"
        lines = [{"price": "0.005", "quantity": 1}] * 3
        self.assertEqual(total(lines), "0.02")

    def test_half_up_on_total(self):
        self.assertEqual(total([{"price": "0.015", "quantity": 1}]), "0.02")
        self.assertEqual(total([{"price": "0.005", "quantity": 1}]), "0.01")
        self.assertEqual(total([{"price": "0.014999", "quantity": 1}]), "0.01")
        self.assertEqual(total([{"price": "0.025", "quantity": 1}]), "0.03")

    def test_multi_line_sum(self):
        lines = [
            {"price": "3.33", "quantity": 3},
            {"price": "0.005", "quantity": 2},
            {"price": "0.001", "quantity": 1000},
        ]
        # 9.99 + 0.010 + 1.000 = 11.000
        self.assertEqual(total(lines), "11.00")

    def test_low_caller_precision_does_not_leak_or_break(self):
        saved = getcontext().prec
        getcontext().prec = 8
        try:
            self.assertEqual(total([{"price": "0.1", "quantity": 3}]), "0.30")
            self.assertEqual(
                total([{"price": "12345678901234.56", "quantity": 7}]),
                "86419752308641.92",
            )
            self.assertEqual(total([{"price": "0.005", "quantity": 3}]), "0.02")
            self.assertEqual(getcontext().prec, 8)
        finally:
            getcontext().prec = saved
        self.assertEqual(getcontext().prec, saved)

    def test_accepts_decimal_quantity_like_int_boundaries(self):
        self.assertEqual(total([{"price": "1", "quantity": 1000}]), "1000.00")
        self.assertEqual(total([{"price": "0", "quantity": 1000}]), "0.00")
        # 1.999999 * 1000 = 1999.999 -> half-up on the total -> "2000.00"
        self.assertEqual(total([{"price": "1.999999", "quantity": 1000}]), "2000.00")

    def test_zero_padded_and_plain_forms(self):
        self.assertEqual(total([{"price": "0.5", "quantity": 1}]), "0.50")
        self.assertEqual(total([{"price": "00.50", "quantity": 1}]), "0.50")


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, lines):
        with self.assertRaises(ValueError):
            total(lines)

    def test_negative_price(self):
        self.assert_invalid([{"price": "-1.00", "quantity": 1}])

    def test_non_finite_price(self):
        for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "sNaN"):
            self.assert_invalid([{"price": bad, "quantity": 1}])

    def test_malformed_price(self):
        for bad in ("", "abc", "1e3", "1.2.3", ".5", "5.", "+1.00", "1,000.00",
                    " 1.00", "1.00 ", "1.0000000", "1" * 17, "0x10"):
            self.assert_invalid([{"price": bad, "quantity": 1}])

    def test_non_string_price(self):
        for bad in (1.25, Decimal("1.25"), None, True):
            self.assert_invalid([{"price": bad, "quantity": 1}])

    def test_invalid_quantity(self):
        for bad in (True, False, 1.0, Decimal("2"), "2", None, -1, 1001):
            self.assert_invalid([{"price": "1.00", "quantity": bad}])

    def test_invalid_row(self):
        self.assert_invalid([{"price": "1.00"}])
        self.assert_invalid([{"quantity": 1}])

    def test_valid_boundaries_accepted(self):
        self.assertEqual(total([{"price": "0", "quantity": 0}]), "0.00")
        self.assertEqual(total([{"price": "0.000001", "quantity": 1000}]), "0.00")
        self.assertEqual(total([{"price": "9" * 16, "quantity": 1000}]),
                         "9999999999999999000.00")


if __name__ == "__main__":
    unittest.main()
