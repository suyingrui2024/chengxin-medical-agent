# 两层开发配置

通用层 `.env.common` 保存模型、密钥、视觉模型和通用调用选项；DSH 层 `.env.dsh` 保存 DSH 端点、后台复核与子智能体选项。完整配置脚本同时生成兼容入口 `.env`。

三个系统的脚本都调用 `configure.py`，它与启动器共用 `medagent.infra.env_config` 的解析器和端点转换。配置重新生成时保留受支持的高级选项，不执行配置文本，不输出密钥。

| 平台 | 通用层 | DSH 层 | 完整配置 |
| --- | --- | --- | --- |
| macOS | `macos/01-configure-common.sh` | `macos/02-configure-dsh.sh` | `macos/03-configure-full.sh` |
| Ubuntu | `ubuntu/01-configure-common.sh` | `ubuntu/02-configure-dsh.sh` | `ubuntu/03-configure-full.sh` |
| Windows | `windows/01-configure-common.ps1` | `windows/02-configure-dsh.ps1` | `windows/03-configure-full.ps1` |

显式真实模型启动的优先级：

1. `--env-file` 指定的文件，缺失或错误时直接报错，不回退；
2. 项目根目录 `.env`；
3. `.env` 不存在时，读取 `.env.common` 并应用 `.env.dsh`；显式 `--backend compatible` 时只读取通用层。

`--backend compatible|dsh` 只覆盖本次进程的后端与官方 DeepSeek 对应端点，不修改配置文件。其他自定义服务端点保留原值。普通离线启动不读取本地配置。

macOS/Ubuntu 日常命令：`./scripts/app/start-desktop.sh --live`。Windows 使用 `scripts/platform/windows/windows.ps1 -Mode Start -Live`。可运行 `PYTHONPATH=src python3 scripts/app/start_live_desktop.py --check` 只检查、不调用模型。
