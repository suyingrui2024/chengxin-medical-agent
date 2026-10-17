"""Frozen synthetic projects and independent checks; never mounted in agent runs."""

from textwrap import dedent


def code(text):
    return dedent(text).lstrip()


UNIT_FILES = {
    "units.py": code(
        """
        def convert(value, source, target):
            factors = {"kg": 1000, "g": 1, "mg": 0.001}
            return str(float(value) * factors[source] / factors[target])
    """
    ),
    "cli.py": code(
        """
        import json, sys
        from units import convert
        def main():
            data = json.load(sys.stdin)
            result = convert(data["value"], data["from"], data["to"])
            print(json.dumps({"result": format(float(result), ".2f")}))
        if __name__ == "__main__": main()
    """
    ),
    "tests/test_existing.py": code(
        """
        import unittest
        from units import convert
        class ExistingTests(unittest.TestCase):
            def test_existing(self):
                self.assertEqual(float(convert("1.25", "kg", "g")), 1250)
        if __name__ == "__main__": unittest.main()
    """
    ),
}
CSV_FILES = {
    "reader.py": code(
        """
        import csv
        def read_rows(stream):
            return list(csv.DictReader(stream))
    """
    ),
    "cli.py": code(
        """
        import json, sys
        from reader import read_rows
        if __name__ == "__main__":
            print(json.dumps({"rows": read_rows(sys.stdin)}))
    """
    ),
    "tests/test_existing.py": code(
        """
        import io, unittest
        from reader import read_rows
        class ExistingTests(unittest.TestCase):
            def test_existing(self):
                self.assertEqual(read_rows(io.StringIO("group,value\\na,1\\n")), [{"group":"a","value":"1"}])
        if __name__ == "__main__": unittest.main()
    """
    ),
}
INVOICE_FILES = {
    "invoice.py": code(
        """
        def total(lines):
            return format(sum(round(float(row["price"]) * int(row["quantity"]), 2) for row in lines), ".2f")
    """
    ),
    "tests/test_existing.py": code(
        """
        import unittest
        from invoice import total
        class ExistingTests(unittest.TestCase):
            def test_existing(self):
                self.assertEqual(total([{"price":"1.25","quantity":2}]), "2.50")
        if __name__ == "__main__": unittest.main()
    """
    ),
}

BASE_PROBE = code(
    """
    import json, subprocess, sys
    from pathlib import Path
    sys.path.insert(0, '/workspace')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd='/workspace', capture_output=True, text=True, timeout=6)
    assert result.returncode == 0, result.stdout + result.stderr
"""
)
UNIT_PROBE = BASE_PROBE + code(
    """
    from units import convert
    from decimal import getcontext
    saved = getcontext().prec
    getcontext().prec = 8
    try:
        cases = [
            ('1.25','kg','g','1250'), ('0.1','g','mg','100'),
            ('1234','mg','g','1.234'), ('0','kg','mg','0'),
            ('999999999999999999999999.123456','mg','g','999999999999999999999.999123456'),
            ('0.000000000000000000000000000007','kg','mg','0.000000000000000000000007'),
            ('120.0000','g','g','120'),
        ]
        for value, source, target, expected in cases:
            actual = convert(value, source, target)
            assert actual == expected, (value, source, target, actual, expected)
            output = subprocess.check_output([sys.executable, 'cli.py'], input=json.dumps({'value':value,'from':source,'to':target}).encode(), cwd='/workspace', timeout=3)
            assert json.loads(output) == {'result':expected}, output
        assert getcontext().prec == 8, 'changed caller precision'
    finally: getcontext().prec = saved
    for args in [('-1','g','mg'),('1','pound','g'),('NaN','g','mg'),('Infinity','g','mg')]:
        try: convert(*args)
        except ValueError: pass
        else: raise AssertionError(('invalid input accepted', args))
    assert json.load(open('/workspace/followup.json')) == {'result':'0.00789'}
    print('unit API, precision, input rejection, CLI, and continuation passed')
"""
)
CSV_PROBE = BASE_PROBE + code(
    """
    from aggregate import aggregate
    rows = [
        {'group':' b ','value':'1.00'}, {'group':'a','value':'0.1'},
        {'group':'b','value':'2.01'}, {'group':'a','value':'0.2'},
        {'group':'b','value':'NaN'}, {'group':'b','value':'Infinity'},
        {'group':' ','value':'3'}, {'group':'a','value':'oops'},
        {'group':'z','value':'-0.005'}, {'group':'z','value':'0'},
    ]
    expected = [
        {'group':'a','count':2,'min':'0.1','max':'0.2','mean':'0.15'},
        {'group':'b','count':2,'min':'1','max':'2.01','mean':'1.51'},
        {'group':'z','count':2,'min':'-0.005','max':'0','mean':'0.00'},
    ]
    assert aggregate(rows) == expected, aggregate(rows)
    assert aggregate([]) == []
    assert aggregate([{'group':'a','value':'1.005'}])[0]['mean'] == '1.01'
    output = subprocess.check_output([sys.executable,'cli.py'], input=b'group,value\\nb,2.01\\na,0.1\\nb,1\\na,0.2\\n', cwd='/workspace', timeout=3)
    assert json.loads(output) == {'groups':expected[:2]}, output
    assert json.load(open('/workspace/followup.json')) == {'groups':[{'group':'c','count':2,'min':'3','max':'5','mean':'4.00'}]}
    print('grouping, invalid rows, rounding, CLI, and continuation passed')
"""
)
INVOICE_PROBE = BASE_PROBE + code(
    """
    from invoice import total
    from decimal import getcontext
    saved = getcontext().prec
    getcontext().prec = 8
    try:
        for lines, expected in [
            ([], '0.00'),
            ([{'price':'0.005','quantity':1}], '0.01'),
            ([{'price':'0.005','quantity':1}]*2, '0.01'),
            ([{'price':'0.1','quantity':3},{'price':'0.2','quantity':1}], '0.50'),
            ([{'price':'1234567890123456.789','quantity':3}], '3703703670370370.37'),
        ]:
            assert total(lines) == expected, (lines, total(lines), expected)
        assert getcontext().prec == 8
    finally: getcontext().prec = saved
    for row in [{'price':'-1','quantity':1},{'price':'1','quantity':-1},{'price':'NaN','quantity':1},{'price':'1','quantity':1.5},{'price':'Infinity','quantity':1}]:
        try: total([row])
        except ValueError: pass
        else: raise AssertionError(('invalid invoice row accepted', row))
    review = json.load(open('/workspace/review.json'))
    assert isinstance(review.get('checks'),list) and len(review['checks']) >= 2
    assert json.load(open('/workspace/followup.json')) == {'result':'0.03'}
    print('invoice arithmetic, validation, review artifact, and continuation passed')
"""
)

CASES = {
    "precision-repair": {
        "files": UNIT_FILES,
        "prompt": """这是合成工程对照评测。工作区已有 units.py、cli.py 和 tests/test_existing.py。修复已有项目，不改动已有测试和 fixture_identity.json。
convert(value, source, target) 接收非负、有限的普通十进制字符串（最多24位整数、36位小数），单位仅 kg/g/mg；返回精确、非科学计数法、去掉无意义小数零的字符串。非法单位、负数、NaN、Infinity 必须抛 ValueError。调用方将 Decimal 精度设成8也必须准确，并且不能改变调用方上下文。
cli.py 从标准输入读取 {"value":"1234","from":"mg","to":"g"}，只输出 {"result":"1.234"}；不得再转 float 或固定保留2位。修复两个模块，增加回归测试，实际运行现有和新增测试。不要只报告建议，保存实际代码和运行结果。""",
        "followup": "继续在同一会话中，实际运行现有 cli.py 将 7.89 mg 转为 g，把程序的 JSON 输出原样保存为 followup.json；保留已有文件。",
        "probe": UNIT_PROBE,
        "changed": ["units.py", "cli.py"],
        "child_required": False,
    },
    "csv-feature": {
        "files": CSV_FILES,
        "prompt": """这是合成工程对照评测。扩展已有 reader.py/cli.py 项目，不改动已有测试和 fixture_identity.json。
新增 aggregate.py，提供 aggregate(rows) -> list，rows 是含 group/value 字符串的字典列表。按去掉两侧空白的 group 分组，忽略空组名、缺失字段、非十进制或 NaN/Infinity 值。输入有效值最多16位整数4位小数，可以为负数。返回按组名字典序排序的列表，每项恰有 group、count、min、max、mean。min/max 为精确的去多余小数零普通十进制字符串；mean 是 Decimal 运算后 ROUND_HALF_UP 到两位小数的字符串，零统一输出 0.00。空输入返回 []。
修改 cli.py 保留从 stdin 读取 CSV 的用法，输出 {"groups": aggregate(read_rows(sys.stdin))}。不要改 reader.py 的现有 API。增加测试并实际运行现有和新增测试，核对 CLI。""",
        "followup": "继续同一会话，实际把 CSV 文本 group,value\\nc,3\\nc,5\\n（其中 \\n 表示换行）送入 cli.py，将 CLI 的 JSON 输出原样保存为 followup.json。",
        "probe": CSV_PROBE,
        "changed": ["cli.py"],
        "child_required": False,
    },
    "delegated-review": {
        "files": INVOICE_FILES,
        "prompt": """这是合成工程对照评测。修复 invoice.py 的 total(lines) -> str，不改已有测试和 fixture_identity.json。
每行 price 是有限非负普通十进制字符串（最多16位整数6位小数），quantity 是 0 至1000的整数，布尔值不算整数。金额必须用十进制精确相乘后求和，最后仅对总和按 ROUND_HALF_UP 舍入两位；禁止逐行舍入。返回固定两位小数字符串，空列表返回 0.00。非法/非有限/负数价格及非法数量抛 ValueError。即使调用方 Decimal 精度是8仍须准确，且不修改全局精度。
先重现问题并修改、增加测试，再使用官方 subagent 创建独立复核者，run_in_background=false，让它读取代码并实际运行针对性检查，保存 review.json，至少包含 checks 数组（记录两项以上具体检查及发现）。根据复核修复遗留问题，再实际运行测试后收尾。分工明确，避免父子反复重复审计。""",
        "followup": '继续同一会话，调用已修复 total([{ "price":"0.015", "quantity":2 }])，实际执行并把 {"result":函数返回值} 保存为 followup.json。',
        "probe": INVOICE_PROBE,
        "changed": ["invoice.py"],
        "child_required": True,
    },
}
