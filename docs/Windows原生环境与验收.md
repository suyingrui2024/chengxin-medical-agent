# Windows 原生环境与验收

更新：2026-10-18。适配分支：`windows-native`。Windows 直接运行 Electron、CPython、Node 和 Microsoft MXC 的原生执行器；不通过 WSL。

## 当前状态

| 验收范围 | 状态 |
| --- | --- |
| 独立运行时、真实桌面、患者计算与问诊接续 | 已通过 Windows 实测 |
| 便携包、安装器、桌面／开始菜单快捷方式、卸载清理 | 已通过 Windows Server x64 实测 |
| Windows 11 文件／环境隔离与 Python／venv／pip | 已通过 x64 兼容环境实测 |
| 完整 SDK 命令执行、子智能体验证及交付接续 | 命令能运行；产物实际落盘与完整会话接续尚未通过验收 |
| 打包后的完整 SDK 执行 | 尚未完成验收 |
| Windows 10、实体 Windows 11 x64 | 尚未实际验证 |

## 安装与启动

工程预览安装包为 `Chengxin-Setup-0.11.17-Windows-x64.exe`，按当前用户安装。桌面与安装流程已有实测，完整执行工作台仍待验收。应用携带 Python 和 Node，安装后从桌面或开始菜单启动，在界面配置模型服务。安装目录不包含开发者的 `.env`。关闭窗口会关闭后端并回收其子进程；卸载程序只处理安装内容，工作区及用户主动导出的文件另行保留。

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

命令的 PowerShell provider 使用以指定工作目录为根的 `ChengxinWorkspace:` 临时驱动器。文件操作优先使用相对路径；向原生程序传物理路径时使用 `$PWD.ProviderPath`，`workdir` 参数仍使用实际 Windows 路径。调用 npm 使用 `npm.cmd`。这种 provider 视图不扩大操作系统授权，仍须通过下述端到端验收。

## 工程边界

- Electron 渲染器启用 sandbox 和 contextIsolation，关闭 Node integration。
- 后端使用随机回环端口与实例凭证，未授权请求返回 401；密钥不放在启动参数或安装包中。
- 文件浏览使用相对目录句柄，拒绝目录联接、reparse point 和备用数据流。
- 进程挂起后先加入 Windows Job Object，再继续执行；应用关闭时回收自己的进程树。
- 命令执行使用 ProcessContainer，只对工作区和私有状态授予写权限，运行时为只读；模型服务通过宿主回环网关访问，执行进程只持有虚拟服务令牌。
- Node 的真实路径查询以及被系统拒绝的目录属性查询，通过宿主提供的受限元数据接口完成。接口只接受已声明运行时／工作区及其精确祖先目录，不提供内容读取、目录列表或写入；实际文件访问继续由操作系统隔离。子进程携带的元数据凭证独立于模型凭证，不能调用模型或其他宿主工具。
- 固定版本的 DSH Job 启动器会清除 `NODE_*` 环境变量；兼容层仅为这个确切启动入口补入可信 Node 启动参数，保留原有 IPC、凭证清理和 Job Object 回收。普通命令的启动参数保持由其调用者决定。
- 原生 PowerShell 和 x64 兼容加载器需要界面初始化。MXC 保留容器内的 UI 能力，并继续禁用剪贴板、输入注入与默认跨容器 UI 交互；不等于完全禁用 Win32k。[官方 UI 策略与实现](https://github.com/microsoft/mxc/blob/main/src/mxc-sdk/src/core/mxc_common/ui_policy.rs)。

## 真实测试记录

以下检查使用本地合成模型服务，不调用外部 DeepSeek API，不能作为临床验收。

| 提交／工作流 | 已观察结果 |
| --- | --- |
| `79b6e67`／[39031745942](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39031745942) | Windows Server x64：安装、DSH 最小实际运行、401 鉴权、原生窗口、实际字体、3 项 Win32／Job 检查、便携包及包内运行通过；当时使用的 Python 后来已替换为完整便携发行版 |
| `d87df0d`／[39032717413](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39032717413) | Server 2025 build 26100.33438 缺少 BaseContainer API；拒绝执行并保留诊断，没有降低隔离要求 |
| `7c1f77d`／[39033094864](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39033094864) | Windows 11 Arm、x64 应用：安装、实际窗口和 3 项 Win32 检查通过；原生执行因界面初始化限制失败，随后调整策略 |
| `0abccea`／[39033319060](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39033319060) | Server 完整 Python 便携包启动通过；Inno Setup 6 编译深层依赖路径失败，随后改为固定校验的 Inno Setup 7.1 |
| `7f87892`／[39033962848](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39033962848) | Server x64 整个任务通过，包括 Inno Setup 7.1 编译、安装到带空格路径、随包运行时启动、桌面快捷方式及卸载；Windows 11 隔离边界和 venv／pip 通过，完整 DSH 仍因启动失败和管道关闭竞态未通过 |
| `4da4a62`／[39051097522](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39051097522) | Server x64 整个任务通过，含桌面与开始菜单快捷方式、卸载清理。Windows 11 25H2 Arm 上的 x64 应用通过环境、窗口、Win32 与元数据检查；SDK 完成模型交互、子会话创建及注册单位换算，但命令的 Job 启动器退出，交付检查正确判定失败 |
| `daed034`／[39052010696](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39052010696) | Windows 11 Job 启动器修复通过；完整执行继续失败，PowerShell 系统模块未加载 |
| `0a818e6`／[39052406909](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39052406909) | Windows 11 原生隔离与 SDK 启动通过；命令因 PowerShell 文件系统位置错误失败。后续 `6f12c46` 显式设置物理路径仍被拒绝访问，改为工作区临时驱动器 |
| `3c80271`／[39053147200](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39053147200) | 工作区临时驱动器使原生命令与子智能体中的 Python 命令运行，但文件版本核对失败；后续修复目录遍历文件标识、跨语言精度和 Windows 时间属性一致性 |
| `90b1210`／[39054263569](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39054263569) | 两套 Windows 的 4 项 Win32 检查和 3 项 Node 元数据／启动器检查通过，包括修改内容并恢复原时间戳的真实哈希验证。Windows 11 SDK 检查命令返回成功，但宿主检查发现产物未出现在预期工作区；端到端测试因此失败，不能把 SDK 上报完成当作验收完成 |
| `c24ca33`／[39054860472](https://github.com/suyingrui2024/chengxin-medical-agent/actions/runs/39054860472) | 补充原生进程 cwd 绑定和产物实际落盘诊断，正在复验；没有通过证据前，完整执行工作台继续列为未验收 |

安装器、快捷方式和卸载清理已有上述 Server x64 实测；完整命令执行及打包版完整执行仍需通过，当前不能宣称 Windows 完整执行工作台已验收。

微软上游也记录了 PowerShell provider 位置与进程 cwd 不一致的问题，以及把驱动器根目录加入只读策略会扩大内容读取范围的风险。本项目不采用整盘只读授权或全局 ACL 修改来解决这类兼容问题。[工作目录问题](https://github.com/microsoft/mxc/issues/825)、[根目录策略问题与维护者说明](https://github.com/microsoft/mxc/issues/1455)。

Windows 属性比较统一使用实际文件句柄查询：目录遍历的缓存结果会把标识置零，CPython 3.14 路径查询还会将 birth time 保留为 ctime，而句柄查询返回 ChangeTime。不能直接混用。目录身份在 Python／Node 之间按十进制字符串传输，避免 128 位文件标识经过 JavaScript Number 时丢失精度；边界解码后继续执行完整身份比较。[DirEntry 文档](https://docs.python.org/3.14/library/os.html#os.DirEntry.stat)、[CPython 路径查询源码](https://github.com/python/cpython/blob/v3.14.8/Modules/posixmodule.c)、[句柄查询源码](https://github.com/python/cpython/blob/v3.14.8/Python/fileutils.c)。

## 前次分支交接

前次按用户要求，将当时更改保存到 `windows-native` 分支，未合并 `main`。交接提交 `d8e0dff` 使用 `[skip ci]`，未自动启动新一轮验证。现已按用户后续要求继续适配。

`844984c` 的 Windows 11 实测已通过患者计算交互与原生隔离边界检查：工作区外文件拒绝、宿主环境变量不可见、Python 执行和宿主模型网关访问通过。完整 DSH 测试随后失败，快照读取中的已关闭 stdin 异常遮住了启动诊断。

交接提交包含已关闭管道的异常处理和独立回归检查（本机 1 项通过），以及当时尚待验证的 Windows 适配：插件路径使用 `file:` URL，状态／停止复用 SDK JSON-RPC 通道，安装器检查开始菜单快捷方式和卸载后的快捷方式清理。早先通过的检查不覆盖这些后续更改。

## 接续验证

`b504a61` 将两套系统的状态与停止统一到 SDK 管道，移除额外控制监听器。本机真实完整 DSH／交付检查与已关闭管道回归共 31 项通过，目录保护及生命周期另外 7 项通过。真实桌面计算、返回问诊、历史恢复和字体检查通过（本地图形会话 `DISPLAY=:1`）。Windows 正在重新验证，后续结果需据实际工作流补充。

新增 `scripts/windows/packaged-execution-check.ps1` 使用打包目录中的解释器、Node 和 Python 源码运行完整 Windows 执行场景，避免只测试源码环境。检查包括写代码、实际执行、子智能体验证、交付检查及原会话接续。

Linux 回归：完整 DSH 执行与交付检查 30 项通过；项目目录保护与桌面生命周期 7 项通过；真实桌面计算、继续问诊、历史恢复及三类实际字体检查通过。Linux 结果不替代 Windows 验证。

## 合并外观改动后的验证

本轮将 `main` 的六套皮肤与图示改动合入 `windows-native`（合并提交 `10a07fe`）。合并后 macOS 上的浏览器回归 96 项、原生桌面回归 3 项及 Node 路径边界检查 3 项通过。完整离线 Python 回归为 340 项通过、60 项跳过、6 项失败；这 6 项在合并前的 `main` 同样失败，涉及 Linux `/proc`、文件名与临时目录路径假设，以及本机缺少研究工具运行环境，并非 Windows 验收证据。

`scripts/windows/gui-check.mjs` 新增六套皮肤的实际切换与偏好文件检查、关闭后重新启动恢复检查，并使用含空格和中文的配置目录。源码与便携包的现有 Windows 工作流都会运行此检查；本机已通过，真实 Windows 结果待取得对应 Actions 日志后补充。完整 SDK 产物落盘、会话接续与打包版执行仍按上表保留未验收状态。
