# 开发脚本索引

这个目录只保存开发、评测、构建和平台适配工具。桌面应用的患者功能位于 `src/`、`desktop/` 和 `integrations/`；发布包不会把这里的脚本作为患者界面的一部分。

## 目录

```text
scripts/
├── app/                   # 本地桌面应用启动入口与快捷方式
├── config/                # macOS、Ubuntu、Windows 分层快速配置
├── build/                 # 桌面构建、打包、字体和干净环境检查
├── platform/
│   └── windows/           # Windows 原生启动、隔离和安装包验收
├── evaluation/
│   ├── medical/           # 医疗问诊、计算、记录和恢复能力评测
│   └── execution/         # DSH 执行、交付、持久化和恢复能力评测
├── benchmarks/
│   ├── public/            # 公开任务、迁移流程和基线
│   └── *.py               # 公开基准共用的任务契约和案例定义
└── maintenance/           # DSH 源码、审核包和图形素材维护
```

根目录只保留本索引和 Python 包标记。新脚本应按用途放入对应目录，避免重新堆积到 `scripts/` 根目录。

## 常用入口

在项目根目录执行：

```bash
# macOS / Ubuntu：使用 .env 启动真实模型桌面应用
./scripts/app/start-desktop.sh --live

# 仅检查 .env，不启动桌面应用，也不调用模型
PYTHONPATH=src python3 scripts/app/start_live_desktop.py --check

# 创建桌面快捷方式
npm run desktop:shortcut

# 构建 Linux x64 或 Windows x64 桌面包
npm run desktop:build

# 查看医疗定向评测的输入、预算和输出参数
PYTHONPATH=src:. python3 scripts/evaluation/medical/evaluate_self_check_conversation.py --help

# 输出 DSH 执行基准的冻结清单；只有追加 --live 才会调用真实模型
PYTHONPATH=src:. python3 scripts/benchmarks/benchmark_execution.py --output /tmp/unused.json
```

Windows 的统一入口为：

```powershell
.\scripts\platform\windows\windows.ps1 -Mode Setup
.\scripts\platform\windows\windows.ps1 -Mode Start -Live
```

三个系统的分层配置说明分别位于 `config/macos/README.md`、`config/ubuntu/README.md` 和 `config/windows/README.md`。

## 使用约定

- 评测使用合成病例和本地夹具，但部分历史定向驱动本身就是有调用上限的真实模型评测。运行前先查看该脚本的 `--help`；提供 `--live` 参数的入口必须显式传入该参数才会调用真实模型。
- Python 脚本之间通过 `scripts.*` 包路径导入，命令从项目根目录运行时使用 `PYTHONPATH=src:.`。
- `docs/evaluations/`、`docs/review-packets/` 和 `docs/assets/` 中保存的是历史结果或校验快照，其中记录的旧脚本路径用于复现实验，不随目录重组改写。
- 移动入口脚本时，需要同步检查 `package.json`、`.github/workflows/`、测试和使用文档中的引用。
