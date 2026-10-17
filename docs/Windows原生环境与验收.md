# Windows 原生环境与验收

更新：2026-10-17。适配分支：`windows-native`。Windows 直接运行 Electron、CPython、Node 和 Microsoft MXC 的原生执行器；不通过 WSL。

## 安装与启动

最终用户使用 `Chengxin-Setup-0.11.17-Windows-x64.exe`，按当前用户安装。应用携带 Python 和 Node，安装后从桌面或开始菜单启动，在界面配置模型服务。安装目录不包含开发者的 `.env`。关闭窗口会关闭后端并回收其子进程；卸载程序只处理安装内容，工作区及用户主动导出的文件另行保留。

开发者在克隆后的项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows.ps1 -Mode Setup
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows.ps1 -Mode Start
```

`Setup` 下载 SHA256 固定的 Python 3.14.8、Node 22.23.3 和 MXC 1.0.1 原生执行器到 `.runtime/windows`，安装锁定依赖和 Electron，并检查实际窗口。无须预装 Python、Node、Conda 或 pip，不修改全局 PATH。源码安装默认创建演示与显式加载 `.env` 的两个快捷方式；自动化可加 `-NoShortcut`。首次安装需要访问官方发行源与 npm。

可选命令：

| 模式 | 用途 |
| --- | --- |
| `Check` | 检查版本、DSH、后端鉴权与真实桌面患者计算交互 |
| `Test` | PowerShell 参数传递、Win32 文件边界、锁与进程树回收 |
| `ExecutionTest` | 实际原生隔离、Python／venv／pip、完整 DSH 工具与接续 |
| `Start -Live` | 显式读取源码根目录的本地 `.env` 启动 |
| `Build` | 生成完整便携目录 `dist/desktop/Chengxin-0.11.17-win32-x64` |
| `Installer` | 用 Inno Setup 7.1+ 编译既有便携目录，输出到 `dist/windows` |

## 平台范围

桌面安装入口面向 Windows 10 19044+／Windows 11 x64；Windows 11 Arm 使用 Windows 自带的 x64 兼容层，当前没有独立 Arm64 原生包。Windows 10 尚未经过本轮真实机器验证。

执行工作台要求更高：宿主必须通过实际 ProcessContainer 策略探测，随后还需实际运行验证。微软列出的初始支持为 Windows 11 24H2 build 26100.9278 或 25H2 build 26200.9278，更新版本的功能也由运行时探测决定。[官方版本表](https://github.com/microsoft/mxc/blob/main/docs/backends/process-container/os-version-support.md)、[联网能力说明](https://github.com/microsoft/mxc/blob/main/docs/backends/process-container/networking.md)。

Windows Server 2025 的本轮镜像虽然版本号较新，却没有相应原生 API。因此桌面通过不意味着执行沙箱通过。执行器拒绝退回需要全局 ACL 修改的兼容方案，也不以无隔离执行替代。

Windows 使用原生 PowerShell 执行命令。上游工具名称仍为 `bash`，系统提示明确说明实际 Shell；用户任务应使用 Windows 命令与路径。Python 自带 venv／pip，但 NumPy、SciPy 等项目依赖不预装，任务应在自己的工作区配置环境。

## 工程边界

- Electron 渲染器启用 sandbox 和 contextIsolation，关闭 Node integration。
- 后端使用随机回环端口与实例凭证，未授权请求返回 401；密钥不放在启动参数或安装包中。
- 文件浏览使用相对目录句柄，拒绝目录联接、reparse point 和备用数据流。
- 进程挂起后先加入 Windows Job Object，再继续执行；应用关闭时回收自己的进程树。
- 命令执行使用 ProcessContainer，只对工作区和私有状态授予写权限，运行时为只读；模型服务通过宿主回环网关访问，执行进程只持有虚拟服务令牌。
- 原生 PowerShell 和 x64 兼容加载器需要界面初始化。MXC 保留容器内的 UI 能力，并继续禁用剪贴板、输入注入与默认跨容器 UI 交互；不等于完全禁用 Win32k。[官方 UI 策略与实现](https://github.com/microsoft/mxc/blob/main/src/mxc-sdk/src/core/mxc_common/ui_policy.rs)。

## 真实测试记录

以下检查使用本地合成模型服务，不调用外部 DeepSeek API，不能作为临床验收。

| 提交／工作流 | 已观察结果 |
| --- | --- |
| `79b6e67`／[39031745942](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39031745942) | Windows Server x64：安装、DSH 最小实际运行、401 鉴权、原生窗口、实际字体、3 项 Win32／Job 检查、便携包及包内运行通过；当时使用的 Python 后来已替换为完整便携发行版 |
| `d87df0d`／[39032717413](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39032717413) | Server 2025 build 26100.33438 缺少 BaseContainer API；拒绝执行并保留诊断，没有降低隔离要求 |
| `7c1f77d`／[39033094864](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39033094864) | Windows 11 Arm、x64 应用：安装、实际窗口和 3 项 Win32 检查通过；原生执行因界面初始化限制失败，随后调整策略 |
| `0abccea`／[39033319060](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39033319060) | Server 完整 Python 便携包启动通过；Inno Setup 6 编译深层依赖路径失败，随后改为固定校验的 Inno Setup 7.1 |

后续隔离修复、患者计算交互、venv／pip 与安装／卸载的最终结果仍待相应 Windows 工作流完成后记录。当前不能宣称 Windows 完整执行工作台或安装器已经验收。

## 本次分支交接

按用户要求，当前更改保存到 `windows-native` 分支，未合并 `main`，后续由用户继续修改。本次提交使用 `[skip ci]`，不再自动启动新一轮验证。

`844984c` 的 Windows 11 实测已通过患者计算交互与原生隔离边界检查：工作区外文件拒绝、宿主环境变量不可见、Python 执行和宿主模型网关访问通过。完整 DSH 测试随后失败，快照读取中的已关闭 stdin 异常遮住了启动诊断。

交接提交包含已关闭管道的异常处理和独立回归检查（本机 1 项通过），以及尚待验证的 Windows 适配：插件路径使用 `file:` URL，状态／停止复用 SDK JSON-RPC 通道，安装器检查开始菜单快捷方式和卸载后的快捷方式清理。新增 SDK 控制通道尚未完成集成回归，也未在真实 Windows 上复验；早先通过的检查不覆盖这些后续更改。Inno Setup 7.1、venv／pip 和安装／卸载的最新 Windows 工作流结果需后续查看。

Linux 回归：完整 DSH 执行与交付检查 30 项通过；项目目录保护与桌面生命周期 7 项通过；真实桌面计算、继续问诊、历史恢复及三类实际字体检查通过。Linux 结果不替代 Windows 验证。
