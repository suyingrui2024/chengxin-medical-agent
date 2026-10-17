import decimal
import unittest

from invoice import total


def line(price, quantity=1):
    return {"price": price, "quantity": quantity}


class TotalFormattingTests(unittest.TestCase):
    def test_empty_list_returns_zero(self):
        self.assertEqual(total([]), "0.00")

    def test_single_line(self):
        self.assertEqual(total([line("1.25", 2)]), "2.50")

    def test_result_always_has_two_decimals(self):
        self.assertEqual(total([line("10", 1)]), "10.00")
        self.assertEqual(total([line("0", 1000)]), "0.00")
        self.assertEqual(total([line("0.5", 1)]), "0.50")

    def test_quantity_zero(self):
        self.assertEqual(total([line("9999999999999999.999999", 0)]), "0.00")


class ExactArithmeticTests(unittest.TestCase):
    def test_rounds_only_the_total_not_each_line(self):
        # 0.004 + 0.004 = 0.008 -> 0.01 with HALF_UP.  Per-line rounding would
        # have produced 0.00 + 0.00 = 0.00.
        self.assertEqual(total([line("0.004"), line("0.004")]), "0.01")

    def test_half_up_on_total(self):
        self.assertEqual(total([line("2.675")]), "2.68")
        self.assertEqual(total([line("1.005")]), "1.01")
        self.assertEqual(total([line("0.005")]), "0.01")

    def test_large_precision_is_exact(self):
        self.assertEqual(
            total([line("1234567890123456.125")]), "1234567890123456.13"
        )

    def test_six_decimal_places_multiplied_exactly(self):
        # 0.000001 * 1000 = 0.001 -> 0.00, while per-line rounding is irrelevant
        # because the sum is exact before the single final rounding.
        self.assertEqual(total([line("0.000001", 1000)]), "0.00")
        self.assertEqual(total([line("0.000005", 1)]), "0.00")
        self.assertEqual(total([line("0.000015", 1)]), "0.00")
        self.assertEqual(total([line("0.000205", 1)]), "0.00")

    def test_sum_accumulates_exactly(self):
        # 1000 lines of 0.004 -> 4.000 exactly.
        self.assertEqual(total([line("0.004")] * 1000), "4.00")

    def test_max_quantity_and_max_price(self):
        self.assertEqual(
            total([line("9999999999999999.999999", 1000)]),
            "10000000000000000000.00",
        )


class CallerContextTests(unittest.TestCase):
    def setUp(self):
        self.ctx = decimal.getcontext()
        self.original_prec = self.ctx.prec
        self.original_rounding = self.ctx.rounding
        self.addCleanup(self.ctx.__setattr__, "prec", self.original_prec)
        self.addCleanup(self.ctx.__setattr__, "rounding", self.original_rounding)

    def test_accurate_with_caller_precision_eight(self):
        self.ctx.prec = 8
        self.assertEqual(
            total([line("1234567890123456.125")]), "1234567890123456.13"
        )
        self.assertEqual(total([line("99999999.999999")]), "100000000.00")

    def test_does_not_modify_global_precision(self):
        self.ctx.prec = 8
        total([line("1234567890123456.125"), line("0.005")])
        self.assertEqual(decimal.getcontext().prec, 8)

    def test_does_not_modify_global_rounding(self):
        self.ctx.rounding = decimal.ROUND_DOWN
        total([line("0.005")])
        self.assertEqual(decimal.getcontext().rounding, decimal.ROUND_DOWN)


class InvalidInputTests(unittest.TestCase):
    def assert_raises(self, price, quantity=1):
        with self.assertRaises(ValueError):
            total([line(price, quantity)])

    def test_negative_prices_rejected(self):
        for bad in ("-1", "-0.01", "-9999999999999999.999999"):
            self.assert_raises(bad)

    def test_non_finite_prices_rejected(self):
        for bad in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
            self.assert_raises(bad)

    def test_malformed_prices_rejected(self):
        for bad in (
            "",
            "abc",
            "1.2.3",
            "1e3",
            "1E3",
            "0x10",
            "1,000",
            " 1.25",
            "1.25 ",
            "1.25\n",
            "1.25\r\n",
            "\n1.25",
            "1.2345678",  # too many fractional digits
            "12345678901234567",  # too many integer digits
        ):
            self.assert_raises(bad)

    def test_non_string_price_rejected(self):
        for bad in (1.25, 1, decimal.Decimal("1.25"), None, True):
            self.assert_raises(bad)

    def test_negative_and_too_large_quantity_rejected(self):
        for bad in (-1, 1001, -1000, 10**9):
            self.assert_raises("1", bad)

    def test_non_integer_quantity_rejected(self):
        for bad in (1.0, decimal.Decimal("2"), "2", None):
            self.assert_raises("1", bad)

    def test_bool_quantity_is_not_an_integer(self):
        for bad in (True, False):
            self.assert_raises("1", bad)

    def test_malformed_line_rejected(self):
        with self.assertRaises(ValueError):
            total([{"price": "1.00"}])
        with self.assertRaises(ValueError):
            total([{"quantity": 1}])
        with self.assertRaises(ValueError):
            total(["not-a-mapping"])

    def test_non_iterable_rejected(self):
        with self.assertRaises(ValueError):
            total(None)


if __name__ == "__main__":
    unittest.main()
