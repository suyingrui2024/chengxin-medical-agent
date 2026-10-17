import json
import subprocess
import sys
import unittest
from decimal import Decimal, getcontext, localcontext

sys.path.insert(0, __file__.rsplit("/tests/", 1)[0])

from units import convert  # noqa: E402

CLI = __file__.rsplit("/tests/", 1)[0] + "/cli.py"


class ConvertExactnessTests(unittest.TestCase):
    def test_basic_conversion(self):
        self.assertEqual(convert("1234", "mg", "g"), "1.234")

    def test_existing_identity(self):
        self.assertEqual(float(convert("1.25", "kg", "g")), 1250)

    def test_int_units_same_unit(self):
        self.assertEqual(convert("42", "g", "g"), "42")

    def test_trailing_zeros_removed(self):
        self.assertEqual(convert("1.2500", "kg", "g"), "1250")
        self.assertEqual(convert("0.000", "g", "mg"), "0")
        self.assertEqual(convert("1000", "mg", "g"), "1")

    def test_wide_conversion_is_not_scientific(self):
        tiny = convert("1", "mg", "kg")
        self.assertEqual(tiny, "0.000001")
        self.assertNotIn("e", tiny.lower())
        big = convert("999999999999999999999999", "kg", "mg")
        self.assertEqual(big, "999999999999999999999999000000")
        self.assertNotIn("e", big.lower())

    def test_full_precision_value(self):
        value = "0." + "0" * 35 + "1"   # 1e-36 g
        self.assertEqual(convert(value, "g", "mg"), "0." + "0" * 32 + "1")

    def test_roundtrip(self):
        for value in ["0", "0.1", "1.25", "123456789012345678", "0.000000000000000000000001"]:
            with self.subTest(value=value):
                back = convert(convert(value, "kg", "mg"), "mg", "kg")
                self.assertEqual(Decimal(back), Decimal(value))


class CallerContextTests(unittest.TestCase):
    def test_caller_precision_8_is_respected_and_untouched(self):
        before = getcontext().copy()
        with localcontext() as ctx:
            ctx.prec = 8
            cases = [
                ("1" + "0" * 23, "kg", "mg"),
                ("0." + "1" * 36, "mg", "kg"),
                ("123456789012345678901234", "mg", "kg"),
                ("0.123456789012345678901234", "kg", "g"),
            ]
            results = [convert(v, s, t) for v, s, t in cases]
            # context must survive the call unchanged
            self.assertEqual(ctx.prec, 8)
            self.assertEqual(ctx.rounding, before.rounding)
            self.assertEqual(getcontext().prec, 8)
        expected = [
            "1" + "0" * 29,                          # 1e23 kg -> 1e29 mg
            "0." + "0" * 6 + "1" * 36,               # 0.111... mg -> kg (36 frac digits)
            "123456789012345678.901234",             # mg -> kg
            "123.456789012345678901234",             # kg -> g
        ]
        self.assertEqual(results, expected)
        self.assertEqual(getcontext().prec, before.prec)

    def test_no_float_rounding(self):
        with localcontext() as ctx:
            ctx.prec = 8
            self.assertEqual(convert("0.1", "kg", "g"), "100")
            self.assertEqual(convert("0.3", "g", "mg"), "300")


class InvalidInputTests(unittest.TestCase):
    def test_bad_units(self):
        for source, target in [("lb", "g"), ("g", "lb"), ("KG", "g"), ("", "g"), (None, "g")]:
            with self.subTest(source=source, target=target):
                with self.assertRaises(ValueError):
                    convert("1", source, target)

    def test_bad_values(self):
        bad = [
            "-1", "-0.5", "NaN", "nan", "Infinity", "-Infinity", "inf",
            "1e3", "1E-3", "1.2.3", "", " ", "1,5", "+1", "1.", ".5",
            "0x10", "1" * 25, "0." + "1" * 37, "1" * 24 + "." + "1" * 37,
            None, 1.5, Decimal("1"),
        ]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert(value, "g", "mg")

    def test_boundary_lengths_accepted(self):
        self.assertEqual(convert("1" * 24, "g", "g"), "1" * 24)
        self.assertEqual(convert("0." + "1" * 36, "g", "g"), "0." + "1" * 36)


class CliTests(unittest.TestCase):
    def _run(self, payload):
        proc = subprocess.run(
            [sys.executable, CLI],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
        )
        return proc

    def test_cli_exact_output(self):
        proc = self._run({"value": "1234", "from": "mg", "to": "g"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, '{"result":"1.234"}\n')

    def test_cli_no_two_decimal_rounding(self):
        proc = self._run({"value": "1", "from": "mg", "to": "kg"})
        self.assertEqual(proc.stdout, '{"result":"0.000001"}\n')

    def test_cli_rejects_invalid(self):
        proc = self._run({"value": "-5", "from": "g", "to": "mg"})
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
