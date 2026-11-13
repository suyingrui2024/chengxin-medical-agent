# 工程配置

`playwright/` 集中保存 UI、桌面和真实模型测试配置。开发用模型密钥仍位于本地 `.env`，生成脚本见 [两层配置](../scripts/config/README.md)。两者用途不同，不将敏感配置搬入本目录。

| 配置 | 用途 | 命令 |
| --- | --- | --- |
| `requirements-dev.txt` | Python 开发、测试与静态检查依赖 | `python3 -m pip install -r config/requirements-dev.txt` |
| `playwright/ui.config.mjs` | 本地与 CI 共用的浏览器配置 | `npm run test:ui` |
| `playwright/desktop.config.mjs` | Electron 桌面测试 | `npm run test:desktop` |
| `playwright/live.config.mjs` | 显式真实模型测试 | 由 `scripts/evaluation/medical/test_live_desktop.py` 调用 |

根目录 `playwright.config.mjs` 只作为工具自动发现入口转发 UI 配置。原 `playwright.ci.config.mjs` 已移除，CI 与本地使用同一份 UI 配置。

路径通过 `playwright/paths.mjs` 从配置文件位置解析为绝对路径，不依赖启动命令所在目录。浏览器测试数据、日志和截图统一写入 `test-results/`；全量测试用脚本逐 spec 隔离，避免多个测试共享患者记录。

编辑器设置与工作区文件位于 `.vscode/`。根目录保留工具直接读取的 `.env*`、Git 配置、`package*.json`、`pyproject.toml` 和 Playwright 发现入口；通过文件嵌套折叠关联文件，不改变启动与配置读取路径。
