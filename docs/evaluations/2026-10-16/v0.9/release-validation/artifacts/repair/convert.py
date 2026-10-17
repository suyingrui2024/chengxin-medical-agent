"""精确质量单位换算：kg / g / mg，全程不受 Decimal 上下文精度舍入影响。

设计要点（对应上一轮精度缺陷）：
  - kg/g/mg 之间的换算系数都是 10 的整数次幂，因此换算本质上是
    “十进制定点小数点移位”，不需要做任何乘法或除法。
  - 旧实现用 `amount * factor` 与 `grams / factor`，会受
    `decimal.getcontext().prec`（默认 28 位有效数字）影响而被静默舍入，
    例如 convert('12345678901234567890123456789.123','mg','g') 会得到
    '12345678901234567890123456.79' 而非精确值。
  - 新实现直接对 Decimal 的 (sign, digits, exponent) 元组做指数平移
    `Decimal((sign, digits, exp + shift))`，该构造是精确的，与调用方
    全局 context 精度完全无关，且不修改调用方 context。
  - 输出格式化不使用 format(...)/quantize()/normalize()（它们可能触发
    上下文舍入），而是直接用 digit 元组拼字符串。

输入范围与拒绝行为：
  - value 接受 str / int / Decimal（bool 被拒绝）；float 按其 repr 的最短
    十进制表示解释。
  - 允许量级：10**-1000 <= |value| < 10**1001（即 -1000 <= adjusted() <= 1000）；
    0 允许。
  - 超出量级（过大 "1e100000" / 过小 "1e-100000"）、负数、非有限数
    （inf/nan）、非法数值字符串、非法/非字符串单位、非法类型，一律抛 ValueError。
  - 单位 kg/g/mg，大小写与首尾空格不敏感。
"""

from decimal import Decimal
from typing import Union

__all__ = ["convert"]

Number = Union[str, int, Decimal]

# 数量级上限/下限（十进制指数），防止 1e100000 之类输入造成内存/日志放大
_MAX_EXPONENT = 1000
_MIN_EXPONENT = -1000

# 每个单位相对克(g)的十进制指数：kg = 10**3 g, g = 10**0 g, mg = 10**-3 g
_FACTOR_EXP = {
    "kg": 3,
    "g": 0,
    "mg": -3,
}

# 可接受的输入数值写法（先做字符白名单，避免 Decimal 接受 inf/nan/下划线）
_NUMERIC = frozenset("0123456789.+-eE")


def _normalize_unit(unit):
    if not isinstance(unit, str):
        raise ValueError("单位必须是字符串: %r" % (unit,))
    key = unit.strip().lower()
    if key not in _FACTOR_EXP:
        raise ValueError("不支持的单位: %r" % (unit,))
    return key


def _parse_amount(value):
    """把输入解析为精确的 Decimal，不受当前 context 精度影响。"""
    if isinstance(value, bool):
        raise ValueError("数值不能是布尔值: %r" % (value,))
    if isinstance(value, float):
        # 浮点输入本身可能已有误差，用 repr 保留用户书写的最短精度
        value = repr(value)
    if isinstance(value, str):
        text = value.strip()
        if text == "" or any(ch not in _NUMERIC for ch in text):
            raise ValueError("无法解析的数值: %r" % (value,))
        try:
            # Decimal(str) 构造本身精确（不按 context 舍入），这里再做一次
            # 显式高精度 context，防御未来/其他实现的差异。
            amount = _exact_decimal(text)
        except Exception:
            raise ValueError("无法解析的数值: %r" % (value,))
    elif isinstance(value, (int, Decimal)):
        # Decimal(Decimal) / Decimal(int) 构造是精确的
        amount = Decimal(value)
    else:
        raise ValueError("不支持的数值类型: %r" % (type(value).__name__,))

    if not amount.is_finite():
        raise ValueError("数值必须是有限数: %r" % (value,))
    if amount < 0:
        raise ValueError("不支持负数: %r" % (value,))
    if not amount.is_zero():
        adj = amount.adjusted()
        if adj > _MAX_EXPONENT:
            raise ValueError("数值过大，超出支持范围: %r" % (value,))
        if adj < _MIN_EXPONENT:
            raise ValueError("数值过小，超出支持范围: %r" % (value,))
    return amount


def _exact_decimal(text):
    """在高精度 localcontext 下解析文本，绝不改变调用方全局 context。"""
    from decimal import localcontext

    digits = sum(1 for ch in text if ch.isdigit())
    with localcontext() as ctx:
        ctx.prec = max(28, digits + 10)
        ctx.Emax = 999999
        ctx.Emin = -999999
        return Decimal(text)


def _format(value):
    """从 Decimal 的 digit 元组直接拼十进制字符串，精确且不再舍入。"""
    sign, digits, exp = value.as_tuple()
    if sign:
        # 调用方已拒绝负数；此处仅做防御，正常不会走到
        raise ValueError("内部错误：结果为负数")
    if not digits or all(d == 0 for d in digits):
        return "0"
    body = "".join(str(d) for d in digits)

    if exp >= 0:
        s = body + "0" * exp
    else:
        point = len(body) + exp
        if point > 0:
            s = body[:point] + "." + body[point:]
        else:
            s = "0." + "0" * (-point) + body

    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


def convert(value: Number, from_unit: str, to_unit: str) -> str:
    """把 value 从 from_unit 换算为 to_unit，返回精确的十进制字符串。

    结果等价于 value * 10**(exp(src) - exp(dst))，通过指数平移精确完成，
    不使用 * 或 /，因此不受 decimal context（含调用方 prec=8 等设置）影响，
    也不会修改调用方的全局精度。返回字符串会去掉小数部分无意义的尾零。
    """
    src = _normalize_unit(from_unit)
    dst = _normalize_unit(to_unit)

    amount = _parse_amount(value)

    shift = _FACTOR_EXP[src] - _FACTOR_EXP[dst]
    sign, digits, exp = amount.as_tuple()
    # 精确构造：指数平移，无任何算术运算，因此不会被 context 舍入
    result = Decimal((sign, digits, exp + shift))
    return _format(result)


if __name__ == "__main__":  # pragma: no cover
    print(convert("1.25", "kg", "g"))
