# v0.10.0 原生项目绑定验收

本轮补齐“打开本地项目”：通过系统文件夹选择器直接绑定已有目录，根任务和子任务在原目录执行，变更基线与 DSH 运行资料仍保存在应用执行根目录。没有对患者问诊开放任意命令，也不增加任意 HTTP 路径接口。

最终验证：174 项后端、21 组浏览器、源码与打包版各 10 组原生检查、7 项桥接断言及格式检查通过。包含真实 185 秒命令；目录绑定另有 6 项单元检查、实际 SDK 原位修改与子任务差异检查、无凭据恢复、取消与受保护目录、HTTP 路径拒绝、900 像素布局。原生用例每次 7 个本机确定模型请求，本轮外部提供商请求为 0。

独立进程另验证了同目录 inode 跨索引占用拒绝、释放后接管和原文件保留。85 个包内源码及随附文件与当前项目匹配，544 个适用生产依赖版本一致；包校验属于自身源码、生产元数据和版本核对，不是全部第三方文件完整性审计。

`snapshot-final/` 保留最终运行代码、测试及相关构建／评测辅助文件；原始公共任务与既往分数不重新归入本版本。截图来自合成临时项目，模型状态和真实环境文件不在归档。归档不是包含全部第三方源码和二进制的独立发行包；完整旧公共任务预检仍依赖仓库对应固定资料与依赖。使用与目录边界见 [设计](../../../本地项目目录与原位执行.md)。

从对应版本项目根目录执行：

```bash
RUN_DSH_EXECUTION=1 RUN_DSH_LONG_EXECUTION=1 RUN_DSH_INTEGRATION=1 \
  PYTHONPATH=src python3 -m unittest discover -s tests -v
npm run test:ui
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority npm run test:desktop
DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority \
  MEDAGENT_DESKTOP_BINARY="$PWD/dist/desktop/Chengxin-0.10.0-linux-x64/Chengxin" npm run test:desktop
python3 scripts/verify_desktop_package.py
```

原生与浏览器测试顺序运行，避免共享截图产物互相覆盖。`archive-manifest.json` 记录所有其他归档文件的 SHA-256。新增目录入口不等于整体 DSH／Codex 任务表现达标，临床验证和更广泛能力对照仍未完成。
