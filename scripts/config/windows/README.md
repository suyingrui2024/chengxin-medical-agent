# Windows 两层快速配置

先在普通 PowerShell 中完成项目运行时安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows.ps1 -Mode Setup
```

三个配置入口使用项目自带的便携 Python，不依赖系统 Python，不输出 API Key。生成的 `.env`、`.env.common` 和 `.env.dsh` 均被 Git 忽略。

## 只配置通用层

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\config\windows\01-configure-common.ps1
```

生成 `.env.common`，并把 `.env` 切换为直接模型配置。

## 添加 DSH 层

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\config\windows\02-configure-dsh.ps1
```

需要先有 `.env.common`。脚本生成 `.env.dsh` 并合并为完整 `.env`。

## 一次生成完整配置

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\config\windows\03-configure-full.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows.ps1 -Mode Check
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows.ps1 -Mode Start -Live
```

完整入口会一次生成两个分层和最终 `.env`。重新运行时，回车保留已有值；API Key 使用隐藏输入。
