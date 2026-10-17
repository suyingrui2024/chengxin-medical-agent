"""Regression tests for the exact mass-unit converter and its CLI."""

import decimal
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from units import convert  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(REPO_ROOT, "cli.py")


class ExactConversionTests(unittest.TestCase):
    def test_basic_conversions(self):
        self.assertEqual(convert("1.25", "kg", "g"), "1250")
        self.assertEqual(convert("1234", "mg", "g"), "1.234")
        self.assertEqual(convert("1000", "g", "kg"), "1")
        self.assertEqual(convert("0.5", "kg", "mg"), "500000")
        self.assertEqual(convert("0", "kg", "mg"), "0")

    def test_trailing_fractional_zeros_are_stripped(self):
        self.assertEqual(convert("1.2500", "kg", "g"), "1250")
        self.assertEqual(convert("1.000", "g", "g"), "1")
        self.assertEqual(convert("0.500", "g", "g"), "0.5")
        self.assertEqual(convert("1.2500", "g", "kg"), "0.00125")

    def test_integer_magnitude_zeros_are_preserved(self):
        # Trailing zeros before the decimal point are significant.
        self.assertEqual(convert("1000", "g", "mg"), "1000000")
        self.assertEqual(convert("1000", "kg", "g"), "1000000")

    def test_no_scientific_notation_for_tiny_and_huge(self):
        tiny = convert("0.000000000000000000000000000001", "g", "kg")
        self.assertEqual(tiny, "0.000000000000000000000000000000001")
        self.assertNotIn("e", tiny.lower())

        huge = convert("9" * 24, "kg", "mg")
        self.assertEqual(huge, "9" * 24 + "0" * 6)
        self.assertNotIn("e", huge.lower())

    def test_24_integer_digits_exact(self):
        value = "123456789012345678901234"
        self.assertEqual(convert(value, "g", "g"), value)
        self.assertEqual(convert(value, "kg", "g"), value + "000")

    def test_36_fractional_digits_exact(self):
        value = "0." + "123456789012345678901234567890123456"
        self.assertEqual(convert(value, "g", "g"), value)
        # kg -> g shifts the point three places right.
        self.assertEqual(convert(value, "kg", "g"), "123.456789012345678901234567890123456")
        # g -> kg shifts the point three places left.
        self.assertEqual(
            convert(value, "g", "kg"),
            "0.000123456789012345678901234567890123456",
        )

    def test_max_precision_round_trip(self):
        value = "123456789012345678901234." + "987654321098765432109876543210987654"
        # kg -> mg -> kg must return the original exact value.
        as_mg = convert(value, "kg", "mg")
        self.assertEqual(convert(as_mg, "mg", "kg"), value)

    def test_accepts_decimal_and_int(self):
        self.assertEqual(convert(decimal.Decimal("1.2500"), "kg", "g"), "1250")
        self.assertEqual(convert(1234, "mg", "g"), "1.234")

    def test_plain_forms(self):
        self.assertEqual(convert("1234.", "g", "kg"), "1.234")
        self.assertEqual(convert(".5", "kg", "g"), "500")


class DecimalContextTests(unittest.TestCase):
    def test_exact_with_tiny_caller_precision(self):
        # The caller's context must not silently round the conversion.
        with decimal.localcontext() as ctx:
            ctx.prec = 8
            self.assertEqual(convert("1.25", "kg", "g"), "1250")
            self.assertEqual(
                convert("123456789012345678901234", "g", "g"),
                "123456789012345678901234",
            )
            self.assertEqual(
                convert("0.123456789012345678901234567890123456", "kg", "g"),
                "123.456789012345678901234567890123456",
            )

    def test_caller_context_is_not_modified(self):
        context = decimal.getcontext()
        original_prec = context.prec
        original_rounding = context.rounding
        original_traps = dict(context.traps)
        original_flags = dict(context.flags)
        try:
            context.prec = 8
            convert("1.25", "kg", "g")
            convert("0.123456789012345678901234567890123456", "kg", "g")
            self.assertEqual(decimal.getcontext().prec, 8)
            self.assertEqual(decimal.getcontext().rounding, original_rounding)
            self.assertEqual(dict(decimal.getcontext().traps), original_traps)
            self.assertEqual(dict(decimal.getcontext().flags), original_flags)
        finally:
            context.prec = original_prec


class ValidationTests(unittest.TestCase):
    def assert_value_error(self, *args):
        with self.assertRaises(ValueError):
            convert(*args)

    def test_invalid_units(self):
        self.assert_value_error("1", "lb", "g")
        self.assert_value_error("1", "g", "lb")
        self.assert_value_error("1", "KG", "g")
        self.assert_value_error("1", "", "g")

    def test_negative_values(self):
        self.assert_value_error("-1", "g", "kg")
        self.assert_value_error("-0.0001", "kg", "mg")

    def test_non_finite_values(self):
        for bad in ("NaN", "nan", "Infinity", "-Infinity", "inf", "sNaN"):
            self.assert_value_error(bad, "g", "kg")

    def test_scientific_notation_is_rejected(self):
        for bad in ("1e3", "1E3", "1.5e-3", "1_000"):
            self.assert_value_error(bad, "g", "kg")

    def test_malformed_values(self):
        for bad in ("", "abc", "1.2.3", "+1", "--1", " "):
            self.assert_value_error(bad, "g", "kg")

    def test_non_finite_decimal_objects(self):
        self.assert_value_error(decimal.Decimal("NaN"), "g", "kg")
        self.assert_value_error(decimal.Decimal("Infinity"), "g", "kg")
        self.assert_value_error(decimal.Decimal("-1"), "g", "kg")


class CliTests(unittest.TestCase):
    def run_cli(self, payload):
        completed = subprocess.run(
            [sys.executable, CLI_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout

    def test_cli_exact_no_float_no_fixed_decimals(self):
        out = self.run_cli({"value": "1234", "from": "mg", "to": "g"})
        self.assertEqual(out.strip(), '{"result":"1.234"}')

    def test_cli_keeps_high_precision(self):
        payload = {
            "value": "0.123456789012345678901234567890123456",
            "from": "kg",
            "to": "g",
        }
        out = self.run_cli(payload)
        self.assertEqual(
            out.strip(),
            '{"result":"123.456789012345678901234567890123456"}',
        )

    def test_cli_does_not_round_to_two_places(self):
        out = self.run_cli({"value": "1234567", "from": "mg", "to": "g"})
        self.assertEqual(out.strip(), '{"result":"1234.567"}')


if __name__ == "__main__":
    unittest.main()
