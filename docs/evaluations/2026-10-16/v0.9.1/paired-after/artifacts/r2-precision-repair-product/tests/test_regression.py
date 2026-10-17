"""Regression tests for exact unit conversion and the CLI output contract."""

import decimal
import json
import subprocess
import sys
import unittest

from units import convert

ROOT = __file__.rsplit("/tests/", 1)[0] if "/tests/" in __file__ else "."


class ExactnessTests(unittest.TestCase):
    def test_identity_and_simple_pairs(self):
        self.assertEqual(convert("1.25", "kg", "g"), "1250")
        self.assertEqual(convert("1234", "mg", "g"), "1.234")
        self.assertEqual(convert("1", "g", "mg"), "1000")
        self.assertEqual(convert("2", "kg", "mg"), "2000000")
        self.assertEqual(convert("500", "mg", "kg"), "0.0005")
        self.assertEqual(convert("0", "kg", "mg"), "0")
        self.assertEqual(convert("0.000", "kg", "mg"), "0")

    def test_no_scientific_notation_and_stripped_zeros(self):
        out = convert("1", "kg", "mg")
        self.assertNotIn("e", out.lower())
        self.assertEqual(convert("1.500", "g", "mg"), "1500")
        self.assertEqual(convert("0.10", "g", "mg"), "100")
        self.assertEqual(convert("1.000000000000000000001", "kg", "g"),
                         "1000.000000000000000001")

    def test_max_length_inputs(self):
        value = "9" * 24 + "." + "9" * 36
        self.assertEqual(convert(value, "kg", "g"), "9" * 27 + "." + "9" * 33)
        self.assertEqual(convert("0." + "0" * 35 + "1", "kg", "mg"),
                         "0." + "0" * 29 + "1")

    def test_prec8_context_still_exact_and_unmodified(self):
        with decimal.localcontext() as ctx:
            ctx.prec = 8
            before = (ctx.prec, ctx.rounding, ctx.Emin, ctx.Emax, ctx.capitals,
                      ctx.clamp, frozenset(ctx.traps), frozenset(ctx.flags))
            self.assertEqual(convert("1", "kg", "mg"), "1000000")
            self.assertEqual(convert("0.0000001", "kg", "mg"), "0.1")
            self.assertEqual(convert("1.23456789012345", "g", "kg"),
                             "0.00123456789012345")
            after = (ctx.prec, ctx.rounding, ctx.Emin, ctx.Emax, ctx.capitals,
                     ctx.clamp, frozenset(ctx.traps), frozenset(ctx.flags))
            self.assertEqual(after, before)
        self.assertEqual(decimal.getcontext().prec, decimal.DefaultContext.prec)

    def test_caller_precision_untouched_with_decimal_work(self):
        decimal.getcontext().prec = 8
        try:
            with decimal.localcontext() as ctx:
                ctx.prec = 8
                convert("123456789012345678901234", "mg", "kg")
                self.assertEqual(ctx.prec, 8)
        finally:
            decimal.getcontext().prec = decimal.DefaultContext.prec

    def test_invalid_inputs_raise_value_error(self):
        bad_values = ["-1", "-0.5", "NaN", "nan", "Infinity", "-Infinity",
                      "inf", "1e3", "abc", "", "1.2.3", " 1", "1 ", "+1",
                      "1" * 25, "0." + "1" * 37]
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert(value, "g", "mg")
        for unit in ["t", "", "KG", "lb", None]:
            with self.subTest(unit=unit):
                with self.assertRaises(ValueError):
                    convert("1", unit, "g")
                with self.assertRaises(ValueError):
                    convert("1", "g", unit)

    def test_roundtrip_all_unit_pairs(self):
        for src in ("kg", "g", "mg"):
            for dst in ("kg", "g", "mg"):
                for value in ("1", "0.001", "1234.5", "0"):
                    with self.subTest(src=src, dst=dst, value=value):
                        forward = convert(value, src, dst)
                        self.assertEqual(convert(forward, dst, src),
                                         convert(value, src, src))
                        self.assertNotIn("e", forward.lower())


class CliTests(unittest.TestCase):
    def run_cli(self, payload):
        proc = subprocess.run(
            [sys.executable, "cli.py"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        return proc

    def test_cli_exact_output(self):
        proc = self.run_cli({"value": "1234", "from": "mg", "to": "g"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), '{"result": "1.234"}')
        self.assertNotIn('"1.23"', proc.stdout)

    def test_cli_no_float_or_fixed_two_decimals(self):
        for payload, expected in [
            ({"value": "1", "from": "kg", "to": "mg"}, "1000000"),
            ({"value": "0.0000001", "from": "kg", "to": "g"}, "0.0001"),
            ({"value": "1.005", "from": "g", "to": "g"}, "1.005"),
        ]:
            proc = self.run_cli(payload)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout),
                             {"result": expected})

    def test_cli_rejects_invalid_input(self):
        proc = self.run_cli({"value": "-5", "from": "g", "to": "kg"})
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
