# 知微 · medical-agent

中文门诊式问诊研究项目，将动态问诊、危险信号提示、就医分流、个人健康档案与随访接续在同一流程中。当前版本 **0.11.17**。

## 快速启动

开发时建议打开 [.vscode/medical-agent.code-workspace](.vscode/medical-agent.code-workspace)，只显示本项目。编辑器将 `.env.*` 折叠到 `.env` 下，将依赖锁文件与测试发现入口折叠到 `package.json` 下；点开箭头即可编辑。依赖、缓存和构建产物默认隐藏，文件仍保留在磁盘。当前文件归属见 [目录说明](docs/development/项目收敛与文件管理.md)。

Python 3.10+；桌面版和 DSH 需 Node 22.19+。命令均在项目根目录运行。

### macOS / Ubuntu

```bash
# 首次安装桌面依赖
npm ci --ignore-scripts
npm run desktop:setup

# 配置好 .env 后，启动真实模型
./scripts/app/start-desktop.sh --live

# 离线演示
./scripts/app/start-desktop.sh
```

首次生成配置：macOS 执行 `./scripts/config/macos/03-configure-full.sh`；Ubuntu 执行 `./scripts/config/ubuntu/03-configure-full.sh`。

### Windows

在项目根目录的 PowerShell 执行：

```powershell
.\scripts\platform\windows\windows.ps1 -Mode Setup
.\scripts\config\windows\03-configure-full.ps1
.\scripts\platform\windows\windows.ps1 -Mode Start -Live
```

Windows 入口负责安装项目专用运行时，无需 WSL。去掉 `-Live` 可进入离线演示。

## 配置

开发配置保留两层：`.env.common` 为通用配置，`.env.dsh` 为 DSH 覆盖配置。三个平台均提供通用、DSH、完整配置三个脚本。

`--live` 启动优先读取手工编辑的 `.env`；没有 `.env` 时才合并两层。也可显式指定文件或后端。普通启动不读取这些文件。

```bash
# 仅检查配置，不启动应用、不调用模型
PYTHONPATH=src python3 scripts/app/start_live_desktop.py --check

# 使用现有配置切换到直接模型，或指定另一份配置
./scripts/app/start-desktop.sh --live --backend compatible
./scripts/app/start-desktop.sh --live --env-file /path/to/local.env
```

软件设置页也能填写自己的模型 API；密钥可仅保留到应用关闭，桌面环境支持时可选择系统安全存储。配置优先级及平台细节见 [配置说明](scripts/config/README.md)。

## 功能入口

| 入口 | 内容 |
| --- | --- |
| 问诊 | 动态问题与可点击选项、图示自查、紧急求助、事实更正、结论单与就诊摘要 |
| 健康档案 | 个人报告与图片、候选识别及纠错、自测指标、时间轴与趋势 |
| 随访 | 复测、提醒、近况记录和问诊接续 |
| 研发视图 | 执行工作台、模型事件、计算能力与后台复核状态 |

直接模型与 DSH 使用同一个医疗控制器。DSH 执行能力保留文件、终端、代码、联网、子智能体与工作区恢复。模型服务、联网和后台复核按配置启用。

项目是研究原型，当前临床内容、验证范围和数据出域边界见 [系统现状](docs/getting-started/系统现状总览.md) 与 [数据出域与隐私](docs/architecture/数据出域与隐私.md)。

## 开发与验证

开发工具统一从 [config/requirements-dev.txt](config/requirements-dev.txt) 安装：

```bash
python3 -m pip install -r config/requirements-dev.txt
```

```bash
PYTHONPATH=src:. python3 -m pytest tests -q
npm run test:unit
npm run test:dsh
npm run lint:structure
npm run lint:python
npm run format:check
npm run test:ui
```

浏览器全量测试入口逐 spec 隔离服务与数据。定向验证示例：`npm run test:ui -- visual-consultation`。默认测试不需要真实 API；真实模型评测的参数和费用边界见 [脚本索引](scripts/README.md)。

## 文件导航

| 目录 | 职责 |
| --- | --- |
| `src/medagent/` | 医疗控制器、模型适配、数据服务与 Web 界面 |
| `desktop/` | Electron 主进程、凭据和系统集成 |
| `integrations/dsh/` | 官方 DSH 桥接、平台隔离和协议测试 |
| `scripts/` | 启动、配置、构建、评测与维护命令 |
| `tests/` | 按领域组织的 Python、前端与端到端测试 |
| `config/` | 开发依赖清单、Playwright 等工程配置；根目录保留标准发现入口 |
| `data/` | 当前知识图谱数据 |
| `research/` | 实验、基准任务数据与研究工具 |
| `docs/` | 现行设计、历史方案、评测报告和归档索引 |

从 [文档导航](docs/README.md) 按上手、架构、功能、开发查阅各子系统；测试配置见 [工程配置](config/README.md)，研究材料见 [研究目录](research/README.md)。代码约定见 [代码与命名规范](docs/development/代码与命名规范.md)，旧版运行详情和历史记录见 [历史记录](docs/archive/开发运行与版本记录.md)。

第三方组件与许可证说明见 [THIRD_PARTY_NOTICES.md](docs/development/THIRD_PARTY_NOTICES.md)。
