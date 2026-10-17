"""Regression tests for the exact kg/g/mg conversion fixes.

Covers: exactness, trailing-zero stripping, absence of scientific notation,
24-integer/36-fraction digit limits, rejection of negative / NaN / Infinity /
scientific / malformed input and unknown units, immunity to a caller context
with ``prec = 8``, non-mutation of the caller's decimal context, and the CLI
contract (exact string, no float round-trip, no fixed 2 decimals).
"""

import decimal
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from units import convert  # noqa: E402

CLI = Path(__file__).resolve().parent.parent / "cli.py"


class ExactnessTests(unittest.TestCase):
    def test_basic_conversions(self):
        self.assertEqual(convert("1.25", "kg", "g"), "1250")
        self.assertEqual(convert("1234", "mg", "g"), "1.234")
        self.assertEqual(convert("1", "kg", "mg"), "1000000")
        self.assertEqual(convert("1", "mg", "kg"), "0.000001")
        self.assertEqual(convert("0", "kg", "mg"), "0")
        self.assertEqual(convert("0.000", "g", "mg"), "0")

    def test_trailing_zeros_stripped(self):
        self.assertEqual(convert("1.500", "g", "mg"), "1500")
        self.assertEqual(convert("2.000", "kg", "g"), "2000")
        self.assertEqual(convert("0.1000", "g", "mg"), "100")

    def test_no_scientific_notation(self):
        # 24 integer digits, converted kg -> mg => 30-digit integer result.
        big = "9" * 24
        out = convert(big, "kg", "mg")
        self.assertNotIn("E", out.upper())
        self.assertEqual(out, "9" * 24 + "000000")

        # 36 fractional digits, mg -> kg => 42 fractional digits.
        small = "0." + "1" * 36
        out = convert(small, "mg", "kg")
        self.assertNotIn("E", out.upper())
        self.assertEqual(out, "0." + "0" * 6 + "1" * 36)

    def test_digit_limits_accepted(self):
        self.assertEqual(convert("1" * 24, "g", "g"), "1" * 24)
        self.assertEqual(convert("0." + "1" * 36, "g", "g"), "0." + "1" * 36)

    def test_digit_limits_rejected(self):
        with self.assertRaises(ValueError):
            convert("1" * 25, "g", "g")
        with self.assertRaises(ValueError):
            convert("0." + "1" * 37, "g", "g")

    def test_roundtrip_is_exact(self):
        self.assertEqual(convert(convert("7.125", "kg", "mg"), "mg", "kg"), "7.125")

    def test_cross_check_against_fractions(self):
        """Seeded random inputs vs exact integer-arithmetic expectations."""
        import random

        exponents = {"kg": 6, "g": 3, "mg": 0}
        rng = random.Random(20240516)
        for _ in range(500):
            int_digits = rng.randint(1, 24)
            frac_digits = rng.randint(0, 36)
            integer = "".join(rng.choice("0123456789") for _ in range(int_digits))
            frac = "".join(rng.choice("0123456789") for _ in range(frac_digits))
            text = integer + ("." + frac if frac else "")
            source, target = rng.choice(list(exponents)), rng.choice(list(exponents))
            shift = exponents[source] - exponents[target]
            num = int(integer + frac)
            power = frac_digits - shift  # value == num / 10**power
            if power <= 0:
                expected = str(num * 10 ** (-power))
            else:
                s = str(num).rjust(power + 1, "0")
                expected = s[:-power] + "." + s[-power:]
                expected = expected.rstrip("0").rstrip(".")
            self.assertEqual(convert(text, source, target), expected,
                             msg="%s %s->%s" % (text, source, target))


class InvalidInputTests(unittest.TestCase):
    def test_invalid_units(self):
        for source, target in (("lb", "g"), ("g", "lb"), ("G", "g"), ("", "g"), ("g", None)):
            with self.assertRaises(ValueError):
                convert("1", source, target)

    def test_negative_and_special_values(self):
        bad = ["-1", "-0.5", "NaN", "nan", "Infinity", "-Infinity", "inf", "-inf",
               "1e3", "1E3", "+1", " 1", "1 ", "", ".", "1.2.3", "1,5", "0x10", None, 5, 1.5]
        for value in bad:
            with self.assertRaises(ValueError, msg="value=%r" % (value,)):
                convert(value, "g", "mg")


class DecimalContextTests(unittest.TestCase):
    def _snapshot(self):
        ctx = decimal.getcontext()
        return (ctx.prec, ctx.rounding, dict(ctx.traps), dict(ctx.flags))

    def test_accurate_under_caller_precision_8(self):
        with decimal.localcontext() as ctx:
            ctx.prec = 8
            self.assertEqual(convert("123456789012345678901234", "kg", "mg"),
                             "123456789012345678901234000000")
            # g -> mg multiplies by 10**3: the 36 fractional digits shift left,
            # leaving 33 nines after the decimal point.
            self.assertEqual(convert("0." + "9" * 36, "g", "mg"), "999." + "9" * 33)
            self.assertEqual(convert("1234", "mg", "g"), "1.234")

    def test_caller_context_unchanged(self):
        original = self._snapshot()
        with decimal.localcontext() as ctx:
            ctx.prec = 8
            before = self._snapshot()
            for _ in range(3):
                convert("123456789012345678901234", "kg", "mg")
                convert("0." + "9" * 36, "mg", "kg")
            self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._snapshot(), original)
        self.assertEqual(decimal.getcontext().prec, original[0])
        self.assertEqual(decimal.getcontext().rounding, original[1])

    def test_default_high_precision_caller_also_fine(self):
        with decimal.localcontext() as ctx:
            ctx.prec = 500
            # kg -> mg multiplies by 10**6: 30 sevens remain after the point.
            self.assertEqual(convert("0." + "7" * 36, "kg", "mg"),
                             "777777." + "7" * 30)


class CliTests(unittest.TestCase):
    def _run(self, payload):
        proc = subprocess.run(
            [sys.executable, str(CLI)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(CLI.parent),
        )
        return proc

    def test_required_example(self):
        proc = self._run({"value": "1234", "from": "mg", "to": "g"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), '{"result": "1.234"}')

    def test_no_float_roundtrip_or_two_decimals(self):
        proc = self._run({"value": "123456789012345678901234", "from": "kg", "to": "mg"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(),
                         '{"result": "123456789012345678901234000000"}')

        proc = self._run({"value": "1.005", "from": "g", "to": "g"})
        self.assertEqual(proc.stdout.strip(), '{"result": "1.005"}')

    def test_single_line_output_only(self):
        proc = self._run({"value": "1", "from": "kg", "to": "kg"})
        self.assertEqual(proc.stdout, '{"result": "1"}\n')


if __name__ == "__main__":
    unittest.main()
