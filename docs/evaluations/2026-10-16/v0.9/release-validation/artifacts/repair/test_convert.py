import decimal
import unittest
from decimal import Decimal

from convert import convert


class TestConvert(unittest.TestCase):
    # ---- 原有正常用例，期望值保持不变 ----
    def test_required_cases(self):
        self.assertEqual(convert("1.25", "kg", "g"), "1250")
        self.assertEqual(convert("0.1", "g", "mg"), "100")
        self.assertEqual(convert("1234", "mg", "g"), "1.234")
        self.assertEqual(convert("0", "g", "mg"), "0")

    def test_same_unit_and_identity(self):
        self.assertEqual(convert("2.500", "kg", "kg"), "2.5")
        self.assertEqual(convert(3, "g", "g"), "3")

    def test_round_trip(self):
        self.assertEqual(convert(convert("1.25", "kg", "mg"), "mg", "kg"), "1.25")

    def test_zero(self):
        self.assertEqual(convert("0.000", "kg", "mg"), "0")

    def test_invalid_units(self):
        with self.assertRaises(ValueError):
            convert("1", "lb", "g")
        with self.assertRaises(ValueError):
            convert("1", "kg", "ton")
        with self.assertRaises(ValueError):
            convert("1", "KGG", "g")
        with self.assertRaises(ValueError):
            convert("1", 5, "g")
        with self.assertRaises(ValueError):
            convert("1", "kg", None)

    def test_negative(self):
        with self.assertRaises(ValueError):
            convert("-1", "kg", "g")
        with self.assertRaises(ValueError):
            convert(-0.5, "g", "mg")
        with self.assertRaises(ValueError):
            convert("-0.0001", "mg", "g")

    def test_bad_value(self):
        with self.assertRaises(ValueError):
            convert("abc", "kg", "g")
        with self.assertRaises(ValueError):
            convert("", "kg", "g")
        with self.assertRaises(ValueError):
            convert(None, "kg", "g")

    def test_unit_normalization(self):
        self.assertEqual(convert("1", "KG ", "g"), "1000")
        self.assertEqual(convert("1", " Mg", "g"), "0.001")

    def test_scientific_and_large(self):
        self.assertEqual(convert("1e-3", "g", "mg"), "1")
        self.assertEqual(convert("1E+3", "g", "kg"), "1")
        self.assertEqual(convert(10 ** 30, "mg", "kg"), "1" + "0" * 24)

    def test_exponent_limit(self):
        with self.assertRaises(ValueError):
            convert("1e100000", "kg", "g")

    def test_precision(self):
        # 0.1 + 0.2 型误差不应出现
        self.assertEqual(convert("0.3", "kg", "g"), "300")
        self.assertEqual(convert("1", "mg", "kg"), "0.000001")

    # ---- 本轮新增：超过 28 位有效数字（默认 context prec=28）----
    def test_beyond_default_context_precision(self):
        # 回归用例：旧实现返回 '12345678901234567890123456.79'
        self.assertEqual(
            convert("12345678901234567890123456789.123", "mg", "g"),
            "12345678901234567890123456.789123",
        )
        # 32 位有效数字，单位不变（幂等）
        self.assertEqual(
            convert("12345678901234567890123456789.123", "g", "g"),
            "12345678901234567890123456789.123",
        )
        # kg -> g 放大 1000 倍，仍应保留全部 34 位有效数字
        self.assertEqual(
            convert("1.2345678901234567890123456789", "kg", "g"),
            "1234.5678901234567890123456789",
        )
        # 40 位整数
        big = "1234567890123456789012345678901234567890"
        # 除以 10**6 == 小数点左移 6 位，并去掉小数尾零
        self.assertEqual(convert(big, "mg", "kg"), "1234567890123456789012345678901234.56789")
        # 超长小数（40 位小数）精确保留有效数字
        self.assertEqual(
            convert("0.1234567890123456789012345678901234567890", "g", "mg"),
            "123.456789012345678901234567890123456789",
        )

    # ---- 本轮新增：极小小数 ----
    def test_tiny_decimals(self):
        # 用户指定必验用例
        self.assertEqual(
            convert("0.000000000000000000000000000001", "kg", "mg"),
            "0.000000000000000000000001",
        )
        self.assertEqual(
            convert("0.000000000000000000000000000001", "g", "mg"),
            "0.000000000000000000000000001",
        )
        self.assertEqual(
            convert("1e-30", "mg", "kg"),
            "0.000000000000000000000000000000000001",
        )
        # 极小值放大 10**6 倍（kg -> mg）后不丢精度
        self.assertEqual(
            convert("0.000000000000000000000000000001234", "kg", "mg"),
            "0.000000000000000000000001234",
        )
        self.assertEqual(convert("1e-40", "g", "mg"), "0.0000000000000000000000000000000000001")

    # ---- 本轮新增：调用方全局精度为 8 时仍精确，且不被修改 ----
    def test_caller_context_precision_8_is_respected_but_not_harmed(self):
        saved = decimal.getcontext().copy()
        try:
            decimal.getcontext().prec = 8
            before = decimal.getcontext().prec
            self.assertEqual(
                convert("12345678901234567890123456789.123", "mg", "g"),
                "12345678901234567890123456.789123",
            )
            self.assertEqual(
                convert("0.000000000000000000000000000001", "kg", "mg"),
                "0.000000000000000000000001",
            )
            # 传入 prec=8 的 Decimal 对象：其自身系数是什么就精确换算什么
            d = Decimal("1.234567890123456789")
            self.assertEqual(convert(d, "kg", "g"), "1234.567890123456789")
            # 调用方 context 精度未被 convert 改动
            self.assertEqual(decimal.getcontext().prec, before)
            self.assertEqual(decimal.getcontext().prec, 8)
        finally:
            decimal.getcontext().prec = saved.prec
        self.assertEqual(decimal.getcontext().prec, saved.prec)

    # ---- 本轮新增：范围与拒绝行为 ----
    def test_range_and_rejection(self):
        # 边界内可接受：|value| < 10**1001
        self.assertTrue(convert("1e1000", "kg", "g").startswith("1"))
        self.assertTrue(convert("1e-1000", "g", "mg").startswith("0."))
        # 超范围拒绝
        for bad in ("1e1001", "1e100000", "1e-1001", "1e-100000", "-1"):
            with self.assertRaises(ValueError):
                convert(bad, "kg", "g")
        # 非有限数 / 非法字符串 / 非法类型
        for bad in ("inf", "-inf", "nan", "Infinity", "1_0", "abc", "", "  "):
            with self.assertRaises(ValueError):
                convert(bad, "kg", "g")
        with self.assertRaises(ValueError):
            convert(None, "kg", "g")
        with self.assertRaises(ValueError):
            convert(True, "kg", "g")
        with self.assertRaises(ValueError):
            convert(["1"], "kg", "g")
        # float 非有限数
        with self.assertRaises(ValueError):
            convert(float("inf"), "kg", "g")
        with self.assertRaises(ValueError):
            convert(float("nan"), "kg", "g")


if __name__ == "__main__":
    unittest.main(verbosity=2)
