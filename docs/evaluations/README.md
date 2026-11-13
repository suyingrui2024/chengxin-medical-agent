# 评测报告与冻结材料

本目录保留阶段报告、原始结果、截图及其历史引用。冻结源码、下载的依赖与运行环境已从工作树移至本地归档，避免源码目录和搜索索引反复扫描旧程序。

`archive-manifest.json` 记录原路径、字节数、SHA256 和归档前 Git 提交。此次未改写 Git 历史；旧提交仍可用于完整复现，因此已有仓库的 `.git` 体积不会随工作树整理而缩小。

本机副本位于项目同级 `_archives/chengxin-medical-agent/<commit>/files/`。恢复单文件：

```bash
python3 scripts/maintenance/evaluation_archive.py --restore docs/evaluations/原文件路径
# 检查整套本地副本的大小与 SHA256
python3 scripts/maintenance/evaluation_archive.py --verify
```

恢复先使用本机副本，没有副本时从清单固定的 Git 对象读取，验证 SHA256 后写回原路径。已有不同内容时拒绝覆盖。迁移电脑时可以携带 `_archives`，或保留能访问清单提交的 Git 历史。

当前运行不依赖这些冻结文件。新增评测只提交摘要、复现命令和必要输入；依赖包、环境副本与整套源码放在外部归档，勿复制进新报告目录。
