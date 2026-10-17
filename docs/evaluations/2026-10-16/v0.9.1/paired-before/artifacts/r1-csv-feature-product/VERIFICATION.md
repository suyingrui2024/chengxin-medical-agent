# 验证报告 — aggregate.py / cli.py 扩展（合成工程对照评测）

## 交付物

| 文件 | 状态 |
|---|---|
| `/workspace/aggregate.py` | 新增，公开 API `aggregate(rows) -> list` |
| `/workspace/cli.py` | 修改：保留从 stdin 读 CSV，输出 `{"groups": aggregate(read_rows(sys.stdin))}` |
| `/workspace/tests/test_aggregate.py` | 新增 15 个测试（聚合语义 + CLI 端到端） |
| `/workspace/reader.py` | 未改动（API `read_rows(stream)` 保持不变） |
| `/workspace/tests/test_existing.py` | 未改动 |
| `/workspace/fixture_identity.json` | 未改动，md5 `f7e568f90f646ef9aebb8da25daf2d56` |

## 实现要点（与规范对应）

- 分组：`group` 必须为字符串，两侧 `strip()` 后为空则忽略。
- 取值：仅接受普通定点十进制（可选符号 + 数字，可带小数部分），
  正则 `^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$`；显式拒绝 `NaN` / `Infinity` /
  指数记法 / 十六进制 / 千分位。缺失字段、非字符串值、非 dict 行忽略。
- 输出：按组名 Python 码点序（字典序）排序，每项恰有
  `group,count,min,max,mean` 五个键（顺序即此）。
- `min`/`max`：`Decimal` 精确值，`format(v, "f")` 去尾零去尾点，
  `-0`/`-`/空统一为 `0`。
- `mean`：`localcontext()` 精度 60 下 `sum/count`，再
  `quantize(Decimal("0.01"), ROUND_HALF_UP)`；值为零（含负零）统一输出 `"0.00"`。
  全程 `Decimal`，无 `float()`/`round()`。
- 空输入（含 `None`、空列表）返回 `[]`；输入列表不被修改。

## 实测结果

`python3 -m unittest discover -s tests -v` → **Ran 16 tests, OK（exit 0）**
（既有 1 个 + 新增 15 个，全部通过。）

CLI 手工核对：

```
$ printf 'group,value\n b ,2.50\n a ,1\n a ,-0.5000\n,9\n c ,oops\n d ,Infinity\n' | python3 cli.py
{"groups": [{"group": "a", "count": 2, "min": "-0.5", "max": "1", "mean": "0.25"}, {"group": "b", "count": 1, "min": "2.5", "max": "2.5", "mean": "2.50"}]}
```

空组名 `,9` 与非法值 `oops`/`Infinity` 被忽略；`mean(a) = (1 + -0.5)/2 = 0.25`；退出码 0。

## 独立复核（子智能体，只读对抗性探测）

结论：**未发现真实规范违反**（8 个区域全 PASS）。

- 值词法 20 例判定与独立推导一致；`1e2/0x1f/nan/inf/1,5/1_0` 均按规范忽略。
- 舍入边界：`0.005→0.01`、`0.015→0.02`、`0.125→0.13`、`-0.005→-0.01`、
  `0/-0.000→0.00`、`3/8→0.38`，符合 `ROUND_HALF_UP`。
- 与独立 `Decimal` 参考实现对拍：3000 组随机值 mean 零不匹配，500 组 min/max 零不匹配。
- 大数（16 位整数 + 4 位小数）精确，无二进制浮点漂移；
  复核者自设的一次 FAIL 经确认为其期望值写错，实现正确
  （`9999999999999999.9999` 与 `…9998` 的均值 = `10000000000000000.00`）。
- 输出类型：`group/min/max/mean` 为 `str`，`count` 为 `int`，恰好 5 键；
  非 dict/None/tuple、数字 group、非字符串 value 忽略；输入未被修改。

## 规范歧义（实现选择，供决策）

1. 规范只说“有效值最多 16 位整数 4 位小数”，未要求拒绝越界值；
   实现对 `1.23456`、17 位整数等**宽容接受**。若上游需要硬校验，属未实现需求。
2. 均值整数位可超过 16 位（两个 16 位整数平均后为 17 位），规范仅约束输入，未约束输出。
3. 负数的 `ROUND_HALF_UP` 为远离零（`-0.005 → -0.01`），与 `decimal` 官方定义一致。
4. 接受 `1.`、`.5`、`+.5`、`007.10` 等普通定点写法；拒绝 `-.`、`.`。
5. 空白按 Python `str.strip()` 定义（含 `\t\n\u00a0`）。

## 局限

- 仅黑盒行为 + 参考实现对拍，非形式化证明；未覆盖无限生成器、并发等规范未要求场景。
- 本轮工具链未做外部（跨语言/跨实现）交叉验证；无真实临床或生产数据涉及。
- `aggregate.py` 的越界输入行为（歧义 1）未被测试固定，若上游明确要求严格校验需再补测试。
