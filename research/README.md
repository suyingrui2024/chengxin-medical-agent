# 研究材料

本目录保存独立实验、基准任务数据和研究工具。应用运行代码位于 `src/`、`desktop/` 与 `integrations/`，这里的内容不随桌面发布包交付。

| 目录 | 内容 |
| --- | --- |
| `benchmarks/` | SWE-lite 基准任务清单、固定样本和环境定义 |
| `experiments/structured-ui/` | A2UI 协议验证实验 |
| `tools/visual-audit/` | 浏览器界面树走查工具 |

基准的执行命令仍集中在 `scripts/benchmarks/`：该目录负责“运行评测”，这里负责“评测使用的数据”。这样普通开发者不必在数据目录里寻找启动命令。

界面走查说明见 [visual audit](tools/visual-audit/README.md)，默认输出移到 `test-results/visual-audit/`。固定实验结果仍保存在 `docs/evaluations/`；新源码快照和依赖包使用外部归档，不复制到研究目录。
