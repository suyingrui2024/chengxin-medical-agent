# 测试套件

158 个 Python 测试文件、2006 个收集节点（参数化用例计为多个节点；本机运行 1935 passed、71 skipped），88 个 Playwright spec（ui 52 / desktop 33 / live 3）。这份文件解释**怎么跑、什么会跳过、东西放哪**。

## 跑什么

| 命令 | 内容 | 耗时 |
|---|---|---|
| `npm run test:python` | 全量 Python（2006 个收集节点） | ~79 秒 |
| `npm run test:coverage` | 同上 + branch 覆盖率（当前 85%） | ~2 分钟 |
| `npm run lint:structure` | 架构门禁：单文件行数 + 依赖方向 + 大文件基线 | ~4 秒 |
| `npm run lint:python` | ruff 最小集：未定义名、语法错、坏 `__all__` | <1 秒 |
| `npm run test:ui` | 52 个前端 spec（每文件独立起服务） | ~20 分钟 |
| `npm run test:desktop` | 33 个 Electron spec | — |
| `npm run test:execution` | 执行沙箱，需 `RUN_DSH_EXECUTION=1` | — |
| `npm run test:kg` | 只跑知识图谱 | — |

CI 跑的是：三个门禁（行数、依赖方向、ruff）→ 全量 pytest + release gate → 全量浏览器 → format:check。

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

测试文件数量主要增加导航成本，不等于运行成本或冗余。当前全量 Python 用例约 69 秒；只有同一行为契约、相同输入条件与相同预期被重复验证时才合并。不同风险情境即使断言相似，也应保留独立测试，便于失败定位。文件持续增长时优先按领域目录分组，并保留可直接运行的 pytest 节点名；不要为减少文件数把不同契约塞进巨型模块。

`conftest.py` 的 fixture **不是 autouse**：`clean_medagent_env` 会清掉 `MEDAGENT_*`，如果全局生效，会改变那些自己设置这些变量的测试。

## 按领域找测试

文件名现在描述**测什么**。少数仍带批次号的文件，对照表见下一节。

| 领域 | 代表文件 |
|---|---|
| 结论单 | `test_conclusion*.py`、`test_close_*.py`、`test_denied_clarification_memory.py`、`test_self_care_oral.py`、`test_direction_guards.py`、`test_pathway_close_gate.py` |
| 问诊引擎 | `test_agent*.py`、`test_consultation*.py`、`test_engine_question_gating.py`、`test_controller_safety_gates.py`、`test_turn_pipeline.py`、`test_dual_engine_safety.py` |
| 知识与推理 | `test_kg.py`、`test_reasoning.py`、`test_triage_scan_gating.py`、`test_pathway_vocabulary.py`、`test_hypothesis_ranking.py`、`test_question_ordering_contract.py` |
| 计算与执行 | `test_formula_engine.py`、`test_scale_engine.py`、`test_capabilities.py`、`test_execution*.py` |
| 病历与会话 | `test_patient_records*.py`、`test_records_measurements_and_reminders.py`、`test_attachment_and_baseline.py`、`test_workspace*.py`、`test_session_events_cursor.py` |
| 联网与报告 | `test_web_tools.py`、`test_report_interpretation*.py`、`test_education.py` |
| 内核单测 | `test_text_match.py`、`test_presentation.py`、`test_task_intents.py`、`test_location_answers.py`、`test_worry_and_goal.py` |
| 架构门禁 | `test_import_rules.py` |
| 平台差异 | `test_platform.py`（翻转 `_WINDOWS`/`_MACOS`，两个分支都能测） |
| 属性测试 | `test_properties.py`（hypothesis） |

## 文件名的历史包袱

文件名里的批次号（`batch_a`、`round3`、`wave2`）记录的是**设计文档的验收批次**，
不是目录结构。2026-11 把 20 个只有批次号的文件按内容重命名了：

| 旧名 | 新名 |
| --- | --- |
| `test_batch_a_controller` | `test_controller_safety_gates` |
| `test_batch_a_scan` | `test_triage_scan_gating` |
| `test_batch_a_vocab` | `test_pathway_vocabulary` |
| `test_batch_b_rework` | `test_evidence_encoding_and_groups` |
| `test_batch_b_worry` | `test_worry_and_goal` |
| `test_batch_c_burden` | `test_burden_screening` |
| `test_batch_c_close` | `test_close_copy` |
| `test_batch_c_content` | `test_conclusion_framing` |
| `test_conclusion_batch_b` | `test_conclusion_worry_evidence` |
| `test_conclusion_v13` | `test_conclusion_directions` |
| `test_conclusion_wave2_content` | `test_conclusion_pathway_gaps` |
| `test_agent_wave2_engine` | `test_engine_question_gating` |
| `test_p2_integration` | `test_attachment_and_baseline` |
| `test_p2_point0` | `test_backend_switch_and_handoff` |
| `test_p3_p4_integration` | `test_records_measurements_and_reminders` |
| `test_round3_c10_clarification_denial` | `test_denied_clarification_memory` |
| `test_round3_close` | `test_close_contradiction_and_walkthrough` |
| `test_round3_g12_close` | `test_close_discriminators` |
| `test_round3_infant_gi` | `test_infant_gi_gate` |
| `test_round4_fix_j09_n12` | `test_residual_filter_and_transcript_denial` |
| `test_e2e_p2_w3` | `test_conclusion_course_and_intermittent` |

批次号保留在各文件的模块 docstring 里（例如 `test_triage_scan_gating.py` 开头写着
"Batch A scan layer (W1)"），所以 `git log --follow` 之外还有文字线索。

## 两类"不只是例子"的测试

- **`test_platform.py`** 把 `infra/platform.py` 的 `_WINDOWS`/`_MACOS` 翻过来，所以 Windows 的 45 秒启动预算、`windows-processcontainer` 标签、Windows 保护目录列表**在 macOS 上也有覆盖**。
- **`test_properties.py`** 用 hypothesis 打两个边界：`validate_adaptive_question` 只能抛 `AdaptiveQuestionError`（其他异常逃逸 = 路由 500），`safe_bytes` 接受的路径必须真的在根目录内。它第一次跑就抓到一个真 bug（路径含 NUL 字节时抛 `ValueError`）。

## 加测试时的三条约定

1. **构造病例状态用 `tests/support/builders.py`**，不要再写一份 `closed_state`。
2. **断言失败要能定位**：用 `pytest.raises(..., match="...")` 断言错误文案，不要只断言"抛了异常"。
3. **不要用 `unittest discover`**：它只收集 `TestCase`，会静默跳过 pytest 风格的模块（历史上 84 项因此从未执行）。
