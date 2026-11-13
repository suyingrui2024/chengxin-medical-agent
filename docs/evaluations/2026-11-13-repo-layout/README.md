# 仓库目录整理验收

本轮只整理文件归属、入口配置和路径引用，沿用 2026-11-12 的本地修改，不提交、不推送、不启动远端 CI，也不调用真实模型。

| 原位置 | 当前归属 |
| --- | --- |
| 根目录的实际 Playwright 配置 | `config/playwright/`；根目录只保留自动发现入口 |
| `playwright.ci.config.mjs` | 移除转发文件，CI 直接共用 UI 配置 |
| 根目录 `benchmarks/`、`experiments/`、`tools/` | `research/` 下同名分类 |
| `vendor/upstream.lock.json` | `integrations/dsh/upstream.lock.json` |
| 可选上游源码检出 | `.runtime/upstream/` |
| 平铺的现行文档 | `docs/getting-started/`、`architecture/`、`features/`、`development/` |
| 旧版开发运行与版本汇编 | `docs/archive/` |
| 知识图谱新评测报告 | `docs/evaluations/knowledge-graph/` |

编辑器入口为根目录 `medical-agent.code-workspace`，只加载当前项目；`.vscode/settings.json` 隐藏生成物，不删除文件。旧空目录壳已移除，研究文件、许可证及固定基准数据保留。

文档索引递归检查现行目录，新增布局测试检查重复根目录、当前 Markdown 链接和工作区范围；Node 测试从临时目录导入三份配置，核对测试目录、输出目录和服务工作目录。CI、NPM 命令、真实模型评测驱动、桌面打包排除规则及文档生成脚本均已更新路径。

最终本地结果见同目录 `verification.json`。桌面和真实模型配置仅做测试发现检查，不将其表述为原生平台或真实 API 验收。
