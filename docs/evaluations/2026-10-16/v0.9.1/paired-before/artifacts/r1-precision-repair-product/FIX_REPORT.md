# 修复报告：kg/g/mg 精确十进制换算（合成工程对照评测）

日期：2026-10-15（UTC） · 工作区：`/workspace` · 全部结果由实际执行产生，原始输出见 `RUN_RESULTS.txt`。

## 1. 修改的产物

| 文件 | 状态 | sha256 |
|---|---|---|
| `units.py` | 重写 | `c3dd138176a4401ce1a70d944507c5188e883c4ecb91f8c67209b7f8c799ba22` |
| `cli.py` | 重写（后加错误分支） | 见 `RUN_RESULTS.txt` 末尾说明，最终版见文件 |
| `tests/test_regression.py` | 新增（16 项含原有 1 项） | `914e76079138c2dc40e42714f544fc9afc247c0abd5cf499af16b70260af4999` |
| `tests/test_existing.py` | **未改动** | `654d14a1c2d7d37c495891c559fdf5a7699fd1a35bc72667f1eb458eefbc5238` |
| `fixture_identity.json` | **未改动**，内容 `{"fixture":"frozen-synthetic-v1"}` | `3be40c8ff3e48e3aa35c2584f764fa7fe238881347d8a40517a67fc5aa5e4bc6` |
| `RUN_RESULTS.txt` | 实际运行记录 | — |

## 2. 根因

- `units.py` 旧实现 `float(value) * factors[source] / factors[target]`：二进制浮点误差（如 1234 mg→g 得到 `1.2340000000000002` 之类），且 `mg` 用 `0.001` 放大误差；`str(float)` 还会对极大/极小值产出科学计数法。
- `cli.py` 旧实现 `format(float(result), ".2f")`：二次浮点往返 + 固定 2 位小数，`1.005` 会被改写、大数丢精度。

## 3. 修复要点

`units.py`
- 用 `decimal.Decimal` 全程精确计算；单位以毫克为基准的**整数指数**表示：`kg=10^6, g=10^3, mg=10^0`。
- 换算仅做 10 的整数次幂缩放（`Decimal(1).scaleb(shift)`），不引入除法舍入；缩放置于 `decimal.localcontext()` 内并设 `prec=200`（远大于输入最多 60 位有效数字），因此**调用方哪怕把精度设为 8 也不会影响结果**。
- `localcontext()` 使用上下文副本，退出时自动恢复：**不修改调用方上下文**（prec/rounding/traps/flags 全部不变，已在测试中断言）。
- 输入校验用正则 `\A(\d+)(?:\.(\d+))?\Z` + 位数上限（整数 ≤24、小数 ≤36）：负数、`NaN`、`Infinity`、科学计数法、空白、空串、非字符串一律 `ValueError`；非法单位同样 `ValueError`。
- 输出用 `format(result, "f")` 保证非科学计数法，再 `rstrip("0")`/`rstrip(".")` 去掉无意义小数零（不用 `normalize()`，避免产生 `1E+3`）。

`cli.py`
- 直接把 `convert()` 的精确字符串写入 `{"result": ...}`，**不再经过 float、不做定长格式化**。
- 非法输入走 stderr 输出 `{"error": ...}` 且返回码 2（不再抛裸 traceback）；正常路径返回 0，输出仅一行 JSON。

## 4. 实际运行结果（命令与观测）

```
$ cd /workspace && python3 -m unittest discover -s tests -v
...
Ran 16 tests in 0.081s

OK

$ echo '{"value":"1234","from":"mg","to":"g"}' | python3 cli.py
{"result": "1.234"}          # exit=0
$ echo '{"value":"123456789012345678901234","from":"kg","to":"mg"}' | python3 cli.py
{"result": "123456789012345678901234000000"}
$ echo '{"value":"1.005","from":"g","to":"g"}' | python3 cli.py
{"result": "1.005"}
$ echo '{"value":"1","from":"lb","to":"g"}' | python3 cli.py
{"error": "invalid source unit 'lb': expected one of ['g', 'kg', 'mg']"}   # exit=2
```

调用方 `prec=8` 的验证（`RUN_RESULTS.txt` 完整记录）：

```
convert('123456789012345678901234','kg','mg') -> 123456789012345678901234000000
convert('0.'+'9'*36,'mg','kg')                -> 0.000000999999999999999999999999999999999999
caller context unchanged: True | prec 8 | rounding ROUND_HALF_EVEN
ValueError OK for '-1' / 'NaN' / 'Infinity' / '1e3' / '' / unit 'lb'
24-digit ok: True ; 36-frac ok: True
```

## 5. 新增回归测试覆盖（`tests/test_regression.py`，15 项新增 + 原有 1 项）

- 精确性：基本换算、往返换算、去尾零、极大/极小值非科学计数法（24 位整数 ×10^6 → 30 位整数；36 位小数 mg→kg → 42 位小数）。
- **随机交叉校验**：固定种子 `20240516`，500 组随机输入（1–24 位整数、0–36 位小数、9 种单位组合）与独立整数算术期望值逐一比对，全部一致。
- 边界：24 位整数/36 位小数接受，25/37 位拒绝。
- 非法输入：负号、`NaN`/`nan`、`Infinity`/`inf`、`1e3`、`+1`、空格、`1.2.3`、`1,5`、`0x10`、空串、`None`、整数/浮点入参 → 均 `ValueError`；非法/大小写错误/空/`None` 单位 → `ValueError`。
- 上下文：`prec=8` 与 `prec=500` 下结果精确；三层嵌套调用前后 `(prec, rounding, traps, flags)` 快照完全相等。
- CLI：必需示例逐字节等于 `{"result": "1.234"}`、单行输出、大数不丢精度、`1.005` 不被四舍五入为 2 位。

## 6. 局限与说明

- 精度上界为“60 位有效数字 + 6 位十进制平移”，`prec=200` 有充足余量；若未来放宽位数上限需同步提高该常量。
- 换算仅在公制前缀之间（10 的幂），因此不存在无限循环小数；未涉及英制/非十进单位，`ValueError` 属预期行为。
- `convert` 对非字符串入参抛 `ValueError`（而非 `TypeError`），是本实现对“非法输入”契约的统一选择。
- 本报告与测试均为工程验证，非临床或计量合规认证。

## 7. 独立复核

复核子智能体（`4f97fda2-558f-4dde-9068-198cc718958e`）独立编写脚本、独立复跑测试与 CLI，报告写入 `VERIFY_REPORT.md`；结论见该文件。
