# Ubuntu 两层快速配置

三个入口共用项目的安全配置生成器，只生成本地开发配置，不安装系统依赖，也不会把 API Key 打印到终端。`.env`、`.env.common` 和 `.env.dsh` 均被 Git 忽略，并设置为仅当前用户可读写。

## 只配置通用层

```bash
./scripts/config/ubuntu/01-configure-common.sh
```

生成 `.env.common`，并把 `.env` 切换为可直接启动的 Chat Completions 配置。

## 添加 DSH 层

```bash
./scripts/config/ubuntu/02-configure-dsh.sh
```

需要先有 `.env.common`。脚本生成 `.env.dsh`，再按“通用层 → DSH 覆盖层”的顺序合并为 `.env`。

## 一次生成完整配置

```bash
./scripts/config/ubuntu/03-configure-full.sh
npm run dsh:check
./scripts/start-desktop.sh --live
```

完整入口会一次生成两个分层和最终 `.env`。重新运行时，回车保留已有值；API Key 使用隐藏输入。脚本要求项目支持的 Python 3.10+；DSH 启动另要求 Node 22.19+ 和已经安装的项目依赖。
