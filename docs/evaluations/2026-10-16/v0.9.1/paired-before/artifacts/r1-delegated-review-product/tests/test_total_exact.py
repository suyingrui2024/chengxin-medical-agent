"""Regression tests for the exact-decimal contract of invoice.total."""

import unittest
from decimal import Decimal, localcontext, getcontext

from invoice import total


class ExactTotalTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(total([]), "0.00")

    def test_no_per_line_rounding(self):
        # 0.125 + 0.125: per-line rounding would give 0.12 + 0.12 = 0.24
        self.assertEqual(total([{"price": "0.125", "quantity": 1},
                                {"price": "0.125", "quantity": 1}]), "0.25")

    def test_half_up_on_total(self):
        self.assertEqual(total([{"price": "1.005", "quantity": 1}]), "1.01")
        self.assertEqual(total([{"price": "2.675", "quantity": 1}]), "2.68")
        self.assertEqual(total([{"price": "2.674", "quantity": 1}]), "2.67")

    def test_binary_float_trap(self):
        self.assertEqual(total([{"price": "0.1", "quantity": 1},
                                {"price": "0.2", "quantity": 1}]), "0.30")

    def test_full_width_values(self):
        self.assertEqual(
            total([{"price": "1234567890123456.123456", "quantity": 1000}]),
            "1234567890123456123.46")

    def test_low_global_precision_does_not_change_result(self):
        original = getcontext().prec
        try:
            getcontext().prec = 8
            with localcontext() as ctx:
                ctx.prec = 8
                self.assertEqual(
                    total([{"price": "1234567890123456.123456", "quantity": 1000}]),
                    "1234567890123456123.46")
                self.assertEqual(
                    total([{"price": "0.1", "quantity": 1},
                           {"price": "0.2", "quantity": 1}]), "0.30")
            self.assertEqual(getcontext().prec, 8)
        finally:
            getcontext().prec = original
        self.assertEqual(getcontext().prec, original)

    def test_accepts_int_and_decimal_prices(self):
        self.assertEqual(total([{"price": Decimal("1.25"), "quantity": 2}]), "2.50")
        self.assertEqual(total([{"price": 3, "quantity": 3}]), "9.00")

    def test_quantity_bounds(self):
        self.assertEqual(total([{"price": "1.00", "quantity": 1000}]), "1000.00")
        self.assertEqual(total([{"price": "1.00", "quantity": 0}]), "0.00")

    def test_illegal_prices(self):
        for bad in ["-1", "-0.01", "abc", "", "1e5", "0x10", " 1.00",
                    "1.2345678", "12345678901234567", "NaN", "Infinity",
                    "\u0665", "\uff11\uff12", "123\u0664",
                    float("nan"), float("inf"), -float("inf"), "-1.00",
                    None, True]:
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    total([{"price": bad, "quantity": 1}])

    def test_illegal_quantities(self):
        for bad in [-1, 1001, 1.5, True, False, "2", None, Decimal("2")]:
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    total([{"price": "1.00", "quantity": bad}])


if __name__ == "__main__":
    unittest.main()
