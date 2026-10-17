# v0.10.4 显式交付检查与实际回执

本轮增加 `delivery_check`，复用官方 DSH 的 Bash 与后台作业，保存真实退出状态、检查名称、命令摘要及所属回合。主、子任务共用原调用预算和截止时间。必需检查未通过时，最多一次原会话纠错；仍未解决则保持未完成。仅检查报告使用 `required=false`，不自动扩展为修改代码。同命令的必需声明不能降级。

完成判断分别核对规范工具调用和实际检查回执。缺少回执属于未知；先前成功不能满足后来丢失结果的重试。同一会话按事件序号判断先后，跨会话按规范事件时间判断；同毫秒或未知时间不能确定先后时保持未知，可用再次实际复验解除。根回复不代表尚在后台运行的所有子任务均已完成，也不自动使后续文件修改后的旧成功失效。

桌面显示简短检查状态、定位实际输出，并保留失败历史与离线恢复；模型请求在开始时计入标题栏，不等提供商响应。Linux x64 应用位于 `dist/desktop/Chengxin-0.10.4-linux-x64/Chengxin`，旧 0.10.3 程序与归档保留。

## 验证范围

较早完整后端运行 207 项全部通过，包含真实 185 秒任务；24 组患者界面通过。最终回执身份修正后，20 项交付检查／预算／恢复定向回归通过（其中交付检查 13 项），源码和重新打包版各 14 组原生检查通过。直接 Node 检查 15 项通过；该批 Node 检查在最终宿主回执身份修正前执行，最终宿主差异由上述定向与原生回归覆盖。包审计核对 93 个项目文件与 544 个适用生产依赖版本。未把最后的定向回归宣称为重新运行完整后端集合。

真实 DeepSeek 三个明确流程的合成工程场景通过，共 14 次请求（6＋6＋2），全部 HTTP 200，调用期间执行源码冻结一致：

| 场景 | 实际结果 |
| --- | --- |
| 数学测试预期错误 | 原实现保留，最小修正测试预期；同命令退出 1→0 |
| 当前版本 API 参数错误 | 原 API 实现保留，最小修正参数名；同命令退出 1→0 |
| 仅检查并报告 | 检查退出 1，原文件保留，不自动修复 |

独立验收另外核对精确编辑契约、保护文件、实际测试收集与冷恢复记录。把断言削弱成恒真后，即使命令退出 0，仍被独立契约拒绝。真实模型自己修正了前两个场景，未触发“提前结束后自动纠错”；后者由确定模型的真实 SDK 测试验证。不能据这三个明确流程场景宣称宽泛编码优势、效率提高或医疗有效性。

真实调用后又修改了请求计数，以及必需声明／回执身份完成门；`real-provider/round1/source` 保留调用时源码，`snapshot-final` 保存最终源码。最终代码对保存的助手文本和有序工具调用离线重放，三个场景通过，共 14 次本地夹具请求、零外部模型请求；从独立临时目录恢复的 121 个运行文件也核对并重放通过。这不是重新调用真实模型，也不是逐字重放推理与原始提供商传输帧。

## 失败保留与文件

`v0104-missing-check-baseline.log` 保留必需检查无法取得结果却被判完成的失败；首次修正后 19 项定向回归通过。随后新增测试复现“重试缺失沿用旧成功”，见 `v0104-missing-retry-baseline.log`；最终修正后 20 项定向回归通过。原始代码和中间版本分别留在 `before-missing-receipt-fix`、`first-missing-receipt-fix`。其他早期记录包括评测脚本导入路径、夹具场景分类、相对工作路径及原生定位器错误，均保留，不算真实模型失败或能力改善证据。

`verification.json` 列出阶段与最终源码哈希。`real-provider/round1.json`、各场景 `requests.json`、候选文件和源码快照保留真实调用证据；`v0104-receipt-identity-*` 是最终修正后的结果。`screenshots` 保存实际原生界面。`credential-scan.json` 扫描配置密钥和归档文件名；`archive-manifest.json` 保存除自身外的所有文件校验值。源码快照不含密钥、病例或 Node 依赖。

## 离线复现

需要既有锁定 Node 依赖、Python、Linux bwrap；原生检查另需当前图形会话。从项目根目录执行，不调用外部模型：

```bash
RUN_DSH_EXECUTION=1 PYTHONPATH=src python3 -m unittest tests.test_execution_delivery tests.test_execution_budget tests.test_execution_recovery -v
python3 docs/evaluations/2026-10-16/v0.10.4/replay.py --output /tmp/delivery-replay-new.json
python3 docs/evaluations/2026-10-16/v0.10.4/restore_and_replay.py --dependencies node_modules --output /tmp/delivery-restored-new.json
python3 scripts/verify_desktop_package.py
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority MEDAGENT_DESKTOP_BINARY="$PWD/dist/desktop/Chengxin-0.10.4-linux-x64/Chengxin" npm run test:desktop
```

输出路径必须是新文件。真实调用评测脚本为 `scripts/evaluate_delivery_checks.py`，重新执行会消耗 API，应使用全新输出路径并显式限制总调用数。当前普通命令退出 0 不能证明测试数量、断言质量、需求覆盖或临床正确性；仅已声明检查进入完成门。整体达到 DSH／Codex、临床效果和患者理解度仍未证明。上一轮 160 次等待策略实验与早期公开任务结果不被本轮覆盖或重新评分。
