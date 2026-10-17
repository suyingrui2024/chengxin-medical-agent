import decimal
import unittest

from invoice import total


class TotalTests(unittest.TestCase):
    def test_existing_style(self):
        self.assertEqual(total([{"price": "1.25", "quantity": 2}]), "2.50")

    def test_empty_list(self):
        self.assertEqual(total([]), "0.00")

    def test_zero_quantity(self):
        self.assertEqual(total([{"price": "123.45", "quantity": 0}]), "0.00")

    def test_zero_price(self):
        self.assertEqual(total([{"price": "0.00", "quantity": 1000}]), "0.00")

    def test_only_total_is_rounded(self):
        # 0.004 + 0.004 = 0.008 -> 0.01; per-line rounding would give 0.00
        self.assertEqual(
            total(
                [
                    {"price": "0.004", "quantity": 1},
                    {"price": "0.004", "quantity": 1},
                ]
            ),
            "0.01",
        )
        # 0.015 + 0.015 = 0.03; per-line rounding would give 0.02
        self.assertEqual(
            total(
                [
                    {"price": "0.015", "quantity": 1},
                    {"price": "0.015", "quantity": 1},
                ]
            ),
            "0.03",
        )

    def test_round_half_up_on_total(self):
        self.assertEqual(total([{"price": "0.005", "quantity": 1}]), "0.01")
        self.assertEqual(total([{"price": "0.015", "quantity": 1}]), "0.02")
        self.assertEqual(total([{"price": "0.025", "quantity": 1}]), "0.03")
        self.assertEqual(total([{"price": "0.125", "quantity": 1}]), "0.13")

    def test_large_precision_is_exact(self):
        self.assertEqual(
            total([{"price": "1234567890123456.12", "quantity": 1}]),
            "1234567890123456.12",
        )
        self.assertEqual(
            total([{"price": "1234567890123456.123456", "quantity": 1000}]),
            "1234567890123456123.46",
        )
        # 16 integer digits + 6 decimals multiplied by 1000: no float loss.
        # ...999.999 correctly rounds up and carries to the next integer.
        self.assertEqual(
            total([{"price": "9999999999999999.999999", "quantity": 1000}]),
            "10000000000000000000.00",
        )

    def test_quantity_bounds(self):
        self.assertEqual(total([{"price": "1.00", "quantity": 1000}]), "1000.00")
        self.assertEqual(total([{"price": "1.00", "quantity": 0}]), "0.00")

    def test_small_caller_precision_is_ignored_and_global_untouched(self):
        ctx = decimal.getcontext()
        old_prec, old_rounding = ctx.prec, ctx.rounding
        try:
            ctx.prec = 8
            result = total(
                [{"price": "1234567890123456.123456", "quantity": 1000}]
            )
            prec_after = ctx.prec
        finally:
            ctx.prec, ctx.rounding = old_prec, old_rounding
        self.assertEqual(result, "1234567890123456123.46")
        self.assertEqual(prec_after, 8)  # total() must not change global prec

    def test_global_rounding_mode_is_not_used(self):
        ctx = decimal.getcontext()
        old_rounding = ctx.rounding
        try:
            ctx.rounding = decimal.ROUND_DOWN
            self.assertEqual(total([{"price": "0.015", "quantity": 1}]), "0.02")
        finally:
            ctx.rounding = old_rounding

    def test_invalid_prices(self):
        bad_prices = [
            "nan",
            "NaN",
            "inf",
            "-inf",
            "Infinity",
            "-1",
            "-0.01",
            "abc",
            "1e3",
            "",
            ".",
            "1.2.3",
            "1,000",
            " 1",
            "1 ",
            None,
            5,
            1.5,
            True,
            "1.1234567",  # 7 fractional digits
            "12345678901234567",  # 17 integer digits
        ]
        for bad in bad_prices:
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    total([{"price": bad, "quantity": 1}])

    def test_invalid_quantities(self):
        bad_quantities = [True, False, 1.5, "2", -1, 1001, None, decimal.Decimal(2)]
        for bad in bad_quantities:
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    total([{"price": "1.00", "quantity": bad}])

    def test_missing_keys_raise_value_error(self):
        with self.assertRaises(ValueError):
            total([{"price": "1.00"}])
        with self.assertRaises(ValueError):
            total([{"quantity": 1}])


if __name__ == "__main__":
    unittest.main()
