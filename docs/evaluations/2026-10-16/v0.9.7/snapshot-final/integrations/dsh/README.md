# DSH 后端

此目录包含真实官方 DSH SDK/CLI 的医疗决策适配和完整执行底座。运行时使用统一固定的 `0.2.0-rc.2` 包；研究源码快照单独记录在 `../../vendor/upstream.lock.json`。

医疗决策路径：

- `bridge.mjs`：通过官方 `DeepSeekHarness` 启动医疗覆盖配置，返回最后提交的文本和运行事件。
- `medical.patch.yml`：关闭默认编码工具、日志上传与插件清单上传。
- `check.mjs`：解析真实 `--dump-config` 输出，确认禁用项；只把 `!!js` 当作文本读取，不执行配置表达式来做检查。
- `tests/runtime-smoke.mjs`：真实 DSH + 本机确定模型响应，检查零宿主工具、事件和回收。
- `tests/bridge.test.mjs`：环境隔离与覆盖配置单元测试。

完整执行路径：

- `execution-worker.mjs`：官方完整 SDK 生命周期、密钥隔离网关、共享预算和有限截断恢复。
- `execution.patch.yml`：保留官方完整工具，配置研究执行行为。
- `execution-capabilities.mjs`：向主任务与子任务注册能力查询、受信计算工具，经宿主网关调用固定 Python 实现；不执行模型指定代码。见 [回执协议](../../docs/注册计算工具与执行回执.md)。
- `isolated-entry.mjs`：在 bubblewrap 内启动官方 CLI，只挂载当前工作区及必要运行资料。
- `execution-control.mjs`：私有控制 socket、取消及后台状态、动态预算提示。
- `execution-sdk.mjs`：复用官方 SDK 服务器，在原会话存在时调用官方 `agents.resume`；需要恢复却缺失历史时拒绝新建空白替代。只用于执行工作台，医疗最小桥接仍保留原配置。
- `execution-observations.mjs`：读取 SDK 规范工具结果，分开记录进程状态和终端文字；与主/子会话、SDK 回合及调用标识关联，经控制 socket 分页传给宿主。属于研发运行观察，不是医疗计算回执。
- `execution-shell.mjs`：继承官方 sandbox Bash 执行器，默认启用 `pipefail`；完整 profile 配置前台默认 10 秒、最多 30 秒后交还后台任务 ID，保留官方任务与取消生命周期。`job_output` 等待同样最多 30 秒。
- `check-artifacts.mjs`：在相同文件隔离下执行宿主选定的独立产物检查。

完整执行工作台实际支持文件、终端、代码、联网及子智能体，不与医疗问诊路径的空工具配置混用。说明与测试见 [通用执行底座](../../docs/通用执行底座与能力评测设计.md)。

安装与检查：

```bash
npm ci --ignore-scripts
npm run dsh:check
npm run test:dsh
npm run test:dsh:integration
RUN_DSH_INTEGRATION=1 PYTHONPATH=src python3 -m unittest tests.integration.test_dsh_e2e -v
```

以上命令在项目根目录运行。合成测试无需外部模型密钥。启动实际模型问诊见 [项目说明](../../README.md)。

医疗桥接传输协议：Python 用 stdin 传入 `{"messages":[...]}`；桥接用 stdout 返回 `{"final_response":"...","events":[...]}`。患者数据不放进 shell 命令或参数，也不经过 `eval`。此路径的医疗工具由 Python 控制器执行，因此其 DSH 内部模型工具列表为空；完整执行路径另有持久 JSONL 控制协议和真实工具循环。

固定发行版使用 DeepSeek Messages 适配器，需提供该协议端点；一般 Chat Completions 服务使用项目的 `compatible` 后端。桥接每次决策启动独立 DSH home，调用结束后关闭并删除；不隐式读取用户的 `~/.dsh`。

上游源码与 npm 包的 API 可能存在差异。升级时同时更新 CLI、SDK、协议包和锁文件，重新运行真实集成测试；不要只改一个版本或用 `--legacy-peer-deps` 掩盖冲突。
