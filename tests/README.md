# 测试套件

154 个 Python 测试文件、1910 个用例，88 个 Playwright spec（ui 52 / desktop 33 / live 3）。这份文件解释**怎么跑、什么会跳过、东西放哪**。

## 跑什么

| 命令 | 内容 | 耗时 |
|---|---|---|
| `npm run test:python` | 全量 Python（1910 个） | ~65 秒 |
| `npm run test:coverage` | 同上 + branch 覆盖率（当前 85%） | ~2 分钟 |
| `npm run lint:structure` | 架构门禁：单文件行数 + 依赖方向 | <1 秒 |
| `npm run test:ui` | 52 个前端 spec（每文件独立起服务） | ~20 分钟 |
| `npm run test:desktop` | 33 个 Electron spec | — |
| `npm run test:execution` | 执行沙箱，需 `RUN_DSH_EXECUTION=1` | — |
| `npm run test:kg` | 只跑知识图谱 | — |

CI 跑的是：两个门禁 → 全量 pytest + release gate → 全量浏览器 → format:check。

## 本地绿 ≠ 全绿

`pytest` 有 **71 个 skip**，本地几乎不可能全跑：

| 原因 | 数量 | 在哪能跑 |
|---|---|---|
| 需要 Linux bubblewrap / 隔离 SDK | ~50 | Linux，`RUN_DSH_EXECUTION=1` |
| 需要真实 Windows 内核 / ProcessContainer | 10 | `windows-native.yml`（windows-11-arm） |
| 显式长耗时检查（142s / 185s） | 3 | 手动 |
| macOS 文件系统不支持不可解码文件名 | 1 | Linux |
| 本地 HTTP fixture / 真实 DSH | 2 | 手动 |

**所以本地全绿只说明"不依赖隔离沙箱的那部分是对的"。** 执行沙箱那一大块（`execution/` 的进程生命周期、bwrap、DSH 运行时）本地一行都跑不到——这也是 `execution/session.py` 覆盖率停在 78% 的原因，不是没人测。

## 东西放哪

```
tests/
  conftest.py         共享 fixture：clean_medagent_env、demo_server、demo_server_factory
  support/
    builders.py       closed_state / make_fact / add_fact（构造病例状态）
    scripted.py       ScriptBackend / cmd / fact / make_engine / Provider（脚本化模型）
  fixtures/           语料与 UI 夹具（conclusion_cases_v0.json 等）
  ui/ desktop/ live/  Playwright spec
```

**加新测试前先看 `support/`**：`closed_state` 和 `fact` 这类构造器以前被复制了三十遍，改一次 `CaseState` 要改三十个文件。

`conftest.py` 的 fixture **不是 autouse**：`clean_medagent_env` 会清掉 `MEDAGENT_*`，如果全局生效，会改变那些自己设置这些变量的测试。

## 文件名的历史包袱

文件名里有 `test_batch_a_vocab`、`test_round3_close`、`test_hypothesis_close` 这种——**它们对应设计文档的验收批次，不是目录结构**。

按领域找测试：

| 领域 | 代表文件 |
|---|---|
| 结论单 | `test_conclusion*.py`、`test_batch_a/b/c_*.py`、`test_round3/4_*.py`、`test_self_care_oral.py` |
| 问诊引擎 | `test_agent*.py`、`test_consultation*.py`、`test_turn_pipeline.py`、`test_dual_engine_safety.py` |
| 知识与推理 | `test_kg.py`、`test_reasoning.py`、`test_hypothesis_ranking.py`、`test_question_ordering_contract.py` |
| 计算与执行 | `test_formula_engine.py`、`test_scale_engine.py`、`test_capabilities.py`、`test_execution*.py` |
| 病历与会话 | `test_patient_records*.py`、`test_workspace*.py`、`test_session_persistence.py` |
| 联网与报告 | `test_web_tools.py`、`test_report_interpretation*.py`、`test_education.py` |
| 内核单测 | `test_text_match.py`、`test_presentation.py`、`test_task_intents.py`、`test_location_answers.py` |
| 架构门禁 | `test_import_rules.py` |
| 平台差异 | `test_platform.py`（翻转 `_WINDOWS`/`_MACOS`，两个分支都能测） |
| 属性测试 | `test_properties.py`（hypothesis） |

## 两类"不只是例子"的测试

- **`test_platform.py`** 把 `infra/platform.py` 的 `_WINDOWS`/`_MACOS` 翻过来，所以 Windows 的 45 秒启动预算、`windows-processcontainer` 标签、Windows 保护目录列表**在 macOS 上也有覆盖**。
- **`test_properties.py`** 用 hypothesis 打两个边界：`validate_adaptive_question` 只能抛 `AdaptiveQuestionError`（其他异常逃逸 = 路由 500），`safe_bytes` 接受的路径必须真的在根目录内。它第一次跑就抓到一个真 bug（路径含 NUL 字节时抛 `ValueError`）。

## 加测试时的三条约定

1. **构造病例状态用 `tests/support/builders.py`**，不要再写一份 `closed_state`。
2. **断言失败要能定位**：用 `pytest.raises(..., match="...")` 断言错误文案，不要只断言"抛了异常"。
3. **不要用 `unittest discover`**：它只收集 `TestCase`，会静默跳过 pytest 风格的模块（历史上 84 项因此从未执行）。
