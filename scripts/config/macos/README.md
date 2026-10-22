# macOS 两层快速配置

这三个入口只生成本地开发配置，不安装系统依赖，也不会把 API Key 打印到终端。生成的 `.env`、`.env.common` 和 `.env.dsh` 均被 Git 忽略，并设置为仅当前用户可读写。

## 只配置通用层

```bash
./scripts/config/macos/01-configure-common.sh
```

生成 `.env.common`，并把 `.env` 切换为可直接启动的 Chat Completions 配置。通用层保存 API Key、文字模型、图片模型和直接模型端点。

## 添加 DSH 层

```bash
./scripts/config/macos/02-configure-dsh.sh
```

需要先有 `.env.common`。脚本生成 `.env.dsh`，再按“通用层 → DSH 覆盖层”的顺序合并为 `.env`。DSH 层只保存运行方式、Messages 端点和后台复核模式。

## 一次生成完整配置

```bash
./scripts/config/macos/03-configure-full.sh
```

一次完成两个分层并生成最终 `.env`，适合首次配置。随后检查并启动：

```bash
npm run dsh:check
./scripts/app/start-desktop.sh --live
```

重新运行任一脚本时，回车会保留已有值；API Key 使用隐藏输入。默认 `shadow` 只记录受限后台复核结果，`advisory` 应仅用于已经完成相应评测的开发实验。
