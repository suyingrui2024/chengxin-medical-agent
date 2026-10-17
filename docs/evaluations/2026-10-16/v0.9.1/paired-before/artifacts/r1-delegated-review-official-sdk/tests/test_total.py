import decimal
import unittest
from decimal import Decimal, getcontext

from invoice import total


class TotalContractTests(unittest.TestCase):
    # --- happy path / formatting -------------------------------------------
    def test_empty_list_is_fixed_zero(self):
        self.assertEqual(total([]), "0.00")

    def test_single_line(self):
        self.assertEqual(total([{"price": "1.25", "quantity": 2}]), "2.50")

    def test_zero_quantity_is_allowed(self):
        self.assertEqual(total([{"price": "9.99", "quantity": 0}]), "0.00")

    def test_max_quantity(self):
        self.assertEqual(total([{"price": "1.00", "quantity": 1000}]), "1000.00")

    def test_result_always_has_two_decimals(self):
        self.assertEqual(total([{"price": "2", "quantity": 1}]), "2.00")
        self.assertEqual(total([{"price": "2.5", "quantity": 1}]), "2.50")

    # --- exact arithmetic, no per-line rounding ----------------------------
    def test_no_per_line_rounding(self):
        # Per-line rounding would give 1.01 + 1.01 = 2.02; exact total is 2.01.
        lines = [
            {"price": "1.005", "quantity": 1},
            {"price": "1.005", "quantity": 1},
        ]
        self.assertEqual(total(lines), "2.01")

    def test_per_line_rounding_would_change_result(self):
        # Exact total 0.015 -> half-up 0.02. Per-line half-up would be 0.03.
        lines = [{"price": "0.005", "quantity": 1} for _ in range(3)]
        self.assertEqual(total(lines), "0.02")

    def test_binary_float_cannot_represent_exactly(self):
        # 0.1 + 0.2 in binary float drifts; exact decimal total is 0.30.
        lines = [
            {"price": "0.1", "quantity": 1},
            {"price": "0.2", "quantity": 1},
        ]
        self.assertEqual(total(lines), "0.30")

    def test_large_amounts_stay_exact(self):
        # 16 integer digits and 6 decimals; float would lose the low digits.
        lines = [
            {"price": "9999999999999999.999999", "quantity": 1},
            {"price": "0.000001", "quantity": 1},
        ]
        self.assertEqual(total(lines), "10000000000000000.00")

    def test_multiplication_before_sum(self):
        lines = [
            {"price": "0.07", "quantity": 3},
            {"price": "0.01", "quantity": 1},
        ]
        self.assertEqual(total(lines), "0.22")

    # --- rounding: ROUND_HALF_UP on the total only -------------------------
    def test_round_half_up(self):
        self.assertEqual(total([{"price": "0.005", "quantity": 1}]), "0.01")
        self.assertEqual(total([{"price": "0.015", "quantity": 1}]), "0.02")
        self.assertEqual(total([{"price": "0.025", "quantity": 1}]), "0.03")
        self.assertEqual(total([{"price": "2.675", "quantity": 1}]), "2.68")

    def test_round_half_up_does_not_round_down_half(self):
        self.assertEqual(total([{"price": "0.004", "quantity": 1}]), "0.00")
        self.assertEqual(total([{"price": "1.004", "quantity": 1}]), "1.00")

    # --- price validation ---------------------------------------------------
    def test_non_finite_and_malformed_prices_raise(self):
        bad_prices = [
            "abc",
            "",
            "NaN",
            "nan",
            "Infinity",
            "inf",
            "-Infinity",
            "-1.00",
            "1e3",
            "1E3",
            " 1.00",
            "1.00 ",
            "1.",
            ".5",
            "1,000.00",
            "0x10",
        ]
        for price in bad_prices:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    total([{"price": price, "quantity": 1}])

    def test_non_string_prices_raise(self):
        for price in [None, 1, 1.0, Decimal("1.00"), True, ["1.00"]]:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    total([{"price": price, "quantity": 1}])

    def test_bool_is_not_an_integer_quantity(self):
        for quantity in [True, False]:
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    total([{"price": "1.00", "quantity": quantity}])

    def test_invalid_quantities_raise(self):
        for quantity in [2.0, "2", -1, 1001, None, Decimal(2), -0.0]:
            with self.subTest(quantity=quantity):
                with self.assertRaises(ValueError):
                    total([{"price": "1.00", "quantity": quantity}])

    # --- caller's decimal context must not matter or be changed ------------
    def test_exact_under_low_caller_precision_and_context_unchanged(self):
        ctx = getcontext()
        old_prec = ctx.prec
        try:
            ctx.prec = 8
            lines = [
                {"price": "1234567890123456.123456", "quantity": 1},
                {"price": "0.000001", "quantity": 1},
            ]
            self.assertEqual(total(lines), "1234567890123456.12")
            self.assertEqual(getcontext().prec, 8)
        finally:
            ctx.prec = old_prec
        self.assertEqual(getcontext().prec, old_prec)

    def test_global_context_object_identity_untouched(self):
        before = getcontext()
        total([{"price": "0.005", "quantity": 1}])
        self.assertIs(getcontext(), before)
        self.assertEqual(getcontext().prec, before.prec)
        self.assertEqual(getcontext().rounding, before.rounding)


if __name__ == "__main__":
    unittest.main()
