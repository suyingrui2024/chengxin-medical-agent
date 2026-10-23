const {
  app,
  BrowserWindow,
  Menu,
  dialog,
  session,
  ipcMain,
  nativeTheme,
  safeStorage,
  shell,
} = require("electron");
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const readline = require("node:readline");
const { pathToFileURL } = require("node:url");
const {
  readAppearance,
  saveAppearance,
  valid: validAppearance,
  migrateLegacyAppearance,
} = require("./appearance.cjs");
const { createModelCredentialStore } = require("./model-credentials.cjs");

const projectRoot = path.resolve(__dirname, "..");
app.setName("知微");
// userData deliberately carries no version suffix: credentials (created
// below against the same directory) and patient data already avoid versioned
// paths, and a versioned userData reset the saved appearance on every
// upgrade. One appearance.json is migrated from the newest legacy versioned
// directory; the old directories themselves are kept, not deleted.
const userDataDirectory =
  process.env.MEDAGENT_DESKTOP_PROFILE ||
  path.join(app.getPath("appData"), "chengxin-medagent");
app.setPath("userData", userDataDirectory);
if (!process.env.MEDAGENT_DESKTOP_PROFILE)
  migrateLegacyAppearance(app.getPath("appData"), userDataDirectory);
const modelCredentials = createModelCredentialStore(
  safeStorage,
  userDataDirectory,
);
app.setAppUserModelId("org.medagent.chengxin");
if (process.platform === "linux") {
  app.setDesktopName("org.medagent.Chengxin.desktop");
  app.commandLine.appendSwitch("class", "Chengxin");
}

let window;
let backend;
let stopping = false;
let readyOrigin;
let quitting = false;
let choosingProject = false;
const projectRequests = new Map();

function defaultExecutionRoot() {
  const desktop = app.getPath("desktop");
  const current = path.join(desktop, "知微工作区");
  const legacy = path.join(desktop, "澄心工作区");
  return fs.existsSync(current) || !fs.existsSync(legacy) ? current : legacy;
}

function nativeAppearance(value) {
  nativeTheme.themeSource =
    value === "system" ? "system" : value === "night" ? "dark" : "light";
  const resolved =
    value === "system"
      ? nativeTheme.shouldUseDarkColors
        ? "night"
        : "forest"
      : value;
  return {
    forest: "#f5f6f2",
    ocean: "#f2f6fa",
    iris: "#f6f4fa",
    sand: "#f8f4ed",
    slate: "#f3f4f5",
    night: "#111722",
  }[resolved];
}

function trustedRendererSender(event) {
  return (
    !stopping &&
    event.sender === window?.webContents &&
    event.senderFrame === window.webContents.mainFrame &&
    (event.senderFrame.url ===
      pathToFileURL(path.join(__dirname, "starting.html")).href ||
      (readyOrigin && new URL(event.senderFrame.url).origin === readyOrigin))
  );
}

ipcMain.on("appearance:read", (event) => {
  event.returnValue = trustedRendererSender(event)
    ? readAppearance(app.getPath("userData"))
    : null;
});
ipcMain.handle("appearance:save", (event, value, ...args) => {
  if (!trustedRendererSender(event) || args.length || !validAppearance(value))
    throw new Error("只能保存当前应用的外观选项");
  saveAppearance(app.getPath("userData"), value);
  window.setBackgroundColor(nativeAppearance(value));
  return { saved: true };
});

ipcMain.handle("model-credentials:status", (event, ...args) => {
  if (!trustedRendererSender(event) || args.length)
    throw new Error("只能从当前应用检查模型凭据");
  return modelCredentials.status();
});
ipcMain.handle("model-credentials:save", (event, config, ...args) => {
  if (!trustedRendererSender(event) || args.length)
    throw new Error("只能从当前应用保存模型凭据");
  return modelCredentials.save(config);
});
ipcMain.handle("model-credentials:clear", (event, ...args) => {
  if (!trustedRendererSender(event) || args.length)
    throw new Error("只能从当前应用删除模型凭据");
  return modelCredentials.clear();
});

function pythonCommand() {
  if (process.env.MEDAGENT_PYTHON) return process.env.MEDAGENT_PYTHON;
  if (process.platform === "win32") {
    for (const relative of [
      "runtime/python/python.exe",
      ".runtime/windows/python/python.exe",
    ]) {
      const bundled = path.join(projectRoot, relative);
      if (fs.existsSync(bundled)) return bundled;
    }
  }
  const candidate = path.join(
    projectRoot,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
  return fs.existsSync(candidate)
    ? candidate
    : process.platform === "win32"
      ? "python"
      : "python3";
}

// Startup failures carry a coarse kind so the error dialog can give
// actionable guidance per cause. The sidecar's stderr is never surfaced:
// it can contain patient data or provider error details.
class BackendStartupError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

function startBackend(token) {
  return new Promise((resolve, reject) => {
    const runtimeEnv = { ...process.env };
    const remembered =
      process.env.MEDAGENT_DESKTOP_LIVE === "1"
        ? null
        : modelCredentials.read();
    if (remembered) {
      Object.assign(runtimeEnv, {
        MEDAGENT_DESKTOP_LIVE: "1",
        MEDAGENT_BACKEND: remembered.backend,
        MEDAGENT_LLM_BASE_URL: remembered.base_url,
        MEDAGENT_LLM_API_KEY: remembered.api_key,
        MEDAGENT_LLM_MODEL: remembered.model,
        MEDAGENT_VISION_MODEL: remembered.vision_model,
      });
    }
    if (process.platform === "win32") {
      for (const relative of ["runtime/node", ".runtime/windows/node"]) {
        const directory = path.join(projectRoot, relative);
        if (fs.existsSync(path.join(directory, "node.exe"))) {
          runtimeEnv.PATH =
            directory + path.delimiter + (runtimeEnv.PATH || "");
          break;
        }
      }
      runtimeEnv.PYTHONUTF8 = "1";
    }
    backend = spawn(
      pythonCommand(),
      [
        "-I",
        "-u",
        "-X",
        "utf8",
        "-c",
        "import sys;sys.path.insert(0,sys.argv[1]);from medagent.desktop_service import main;main()",
        path.join(projectRoot, "src"),
      ],
      {
        cwd: projectRoot,
        detached: process.platform !== "win32",
        windowsHide: true,
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...runtimeEnv,
          PYTHONPATH: path.join(projectRoot, "src"),
          PYTHONDONTWRITEBYTECODE: "1",
          MEDAGENT_EXECUTION_ROOT:
            process.env.MEDAGENT_EXECUTION_ROOT || defaultExecutionRoot(),
        },
      },
    );
    const timer = setTimeout(
      () => reject(new BackendStartupError("ready_timeout", "启动超时")),
      15000,
    );
    const lines = readline.createInterface({ input: backend.stdout });
    let ready = false;
    lines.on("line", (line) => {
      try {
        const message = JSON.parse(line);
        if (ready) {
          if (message.type === "project_opened") {
            const pending = projectRequests.get(message.request_id);
            if (pending) {
              projectRequests.delete(message.request_id);
              clearTimeout(pending.timer);
              if (message.error) pending.reject(new Error(message.error));
              else pending.resolve(message.snapshot);
            }
          }
          return;
        }
        if (
          message.type !== "ready" ||
          !Number.isInteger(message.port) ||
          message.port < 1 ||
          message.port > 65535
        )
          return;
        ready = true;
        clearTimeout(timer);
        resolve(`http://127.0.0.1:${message.port}`);
      } catch {
        // Sidecar stdout is a bounded startup protocol, never rendered as HTML.
      }
    });
    backend.on("error", (error) => {
      clearTimeout(timer);
      // ENOENT here means the interpreter itself could not be found (not
      // installed, or removed after the environment check passed).
      reject(
        new BackendStartupError(
          error?.code === "ENOENT" ? "interpreter_missing" : "spawn_error",
          "未能启动 Python",
        ),
      );
    });
    backend.on("exit", () => {
      for (const pending of projectRequests.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error("本地服务已经停止"));
      }
      projectRequests.clear();
      clearTimeout(timer);
      if (!ready)
        reject(
          new BackendStartupError(
            "exited_before_ready",
            "本地问诊服务未能启动",
          ),
        );
      else if (!stopping) {
        dialog.showErrorBox(
          "问诊服务已停止",
          "请关闭应用后重新打开。已结束的会谈与随访已保存在本机，进行中的问诊无法跨进程恢复。",
        );
        app.quit();
      }
    });
    backend.stdin.on("error", () => {});
    backend.stderr.on("data", () => {}); // Do not write patient data or provider errors to desktop logs.
    backend.stdin.write(`${JSON.stringify({ token })}\n`);
  });
}

function startupFailureGuidance(error) {
  const windows = process.platform === "win32";
  switch (error?.kind) {
    case "interpreter_missing":
      return windows
        ? "没有找到可用的 Python 运行时。请先把发行包完整解压到一个目录再运行，不要只复制其中的单个文件；也可运行 scripts/platform/windows/windows.ps1 -Mode Check 检查 Windows 运行环境。"
        : "没有找到可用的 Python 解释器。请确认已安装 Python 3.10+ 并已加入 PATH，或设置 MEDAGENT_PYTHON 指向 Python 可执行文件后重新打开。";
    case "exited_before_ready":
      return windows
        ? "本地问诊服务在启动完成前退出。请运行 scripts/platform/windows/windows.ps1 -Mode Check 检查 Windows 运行环境，并确认发行包目录完整、随包 Python 和 Node 未被安全软件删除或隔离。"
        : "本地问诊服务在启动完成前退出。请确认已按项目说明安装 Python 依赖，并保留完整的应用目录后重新打开。";
    case "ready_timeout":
      return "本地问诊服务启动超时。可能被安全软件拦截或当前系统负载较高，请稍后重新打开；若反复出现，请确认安全软件允许本应用启动子进程并访问本机回环地址。";
    default:
      return windows
        ? "请运行 scripts/platform/windows/windows.ps1 -Mode Check 检查 Windows 运行环境；使用发行包时请先解压整个目录，保留随包 Python 和 Node。"
        : "请确认已安装 Python 3.10+，并保留完整的应用目录。可设置 MEDAGENT_PYTHON 指向 Python 可执行文件。模型依赖安装方法请查看项目说明。";
  }
}

async function stopBackend() {
  stopping = true;
  const child = backend;
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const finished = new Promise((resolve) => child.once("exit", resolve));
  child.stdin.end();
  const signalGroup = (signal) => {
    try {
      if (process.platform === "win32") child.kill(signal);
      else process.kill(-child.pid, signal);
    } catch {}
  };
  const term = setTimeout(() => signalGroup("SIGTERM"), 2000);
  const kill = setTimeout(() => signalGroup("SIGKILL"), 4000);
  await finished;
  // A model subprocess may outlive the HTTP server; reap its process group too.
  signalGroup("SIGTERM");
  clearTimeout(term);
  clearTimeout(kill);
}

async function createWindow() {
  // The persist: prefix is load-bearing: without it the partition is
  // discarded when the app exits, taking localStorage (font size, web
  // appearance choices) with it, while the settings page promises those
  // choices are kept for the next launch. Nothing persistent is lost by
  // switching — the old partition never survived a restart by definition.
  const privateSession = session.fromPartition("persist:chengxin-desktop");
  const { createClipboardPermission } = require("./clipboard-permission.cjs");
  const allowClipboard = createClipboardPermission(
    () => window?.webContents,
    () => readyOrigin,
  );
  privateSession.setPermissionRequestHandler(
    (contents, permission, callback, details) =>
      callback(
        allowClipboard(contents, permission, details.requestingUrl, details),
      ),
  );
  privateSession.setPermissionCheckHandler(allowClipboard);
  window = new BrowserWindow({
    title: "知微",
    width: 1440,
    height: 980,
    minWidth: 900,
    minHeight: 680,
    backgroundColor: nativeAppearance(readAppearance(app.getPath("userData"))),
    icon: path.join(__dirname, "icon.png"),
    show: false,
    webPreferences: {
      session: privateSession,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "preload.cjs"),
      webSecurity: true,
      spellcheck: false,
      devTools: !app.isPackaged,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (readyOrigin && new URL(url).origin === readyOrigin) return;
    event.preventDefault();
    if (url === "tel:120") void shell.openExternal(url).catch(() => {});
  });
  window.on("page-title-updated", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  await window.loadFile(path.join(__dirname, "starting.html"));
  if (quitting || stopping || window.isDestroyed()) return;
  const token = randomBytes(32).toString("hex");
  readyOrigin = await startBackend(token);
  if (quitting || stopping || window.isDestroyed()) {
    await stopBackend();
    return;
  }
  privateSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    if (new URL(details.url).origin === readyOrigin)
      headers["X-MedAgent-Token"] = token;
    callback({ requestHeaders: headers });
  });
  await window.loadURL(readyOrigin);
}

ipcMain.handle("execution:open-project", async (event, ...args) => {
  if (
    args.length ||
    choosingProject ||
    stopping ||
    !readyOrigin ||
    event.sender !== window?.webContents ||
    event.senderFrame !== window.webContents.mainFrame ||
    new URL(event.senderFrame.url).origin !== readyOrigin
  )
    throw new Error("只能从当前应用选择项目");
  choosingProject = true;
  try {
    const selected = await dialog.showOpenDialog(window, {
      title: "选择要直接编辑的项目文件夹",
      buttonLabel: "使用此项目",
      message: "智能体可读取和修改所选目录中的文件，修改直接保存在原位置。",
      properties: ["openDirectory"],
    });
    if (selected.canceled || selected.filePaths.length !== 1)
      return { cancelled: true };
    const directory = selected.filePaths[0];
    const snapshot = await new Promise((resolve, reject) => {
      const request_id = randomBytes(16).toString("hex");
      const timer = setTimeout(() => {
        projectRequests.delete(request_id);
        reject(new Error("打开项目超时，请刷新工作区列表后重试"));
      }, 15000);
      projectRequests.set(request_id, { resolve, reject, timer });
      backend.stdin.write(
        JSON.stringify({ type: "open_project", request_id, directory }) + "\n",
        (error) => {
          if (!error) return;
          clearTimeout(timer);
          projectRequests.delete(request_id);
          reject(new Error("项目未能发送到本地服务"));
        },
      );
    });
    return { cancelled: false, snapshot };
  } finally {
    choosingProject = false;
  }
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (window?.isMinimized()) window.restore();
    window?.focus();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    quitting = true;
    stopBackend().finally(() => app.quit());
  });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        {
          label: "应用",
          submenu: [
            {
              label: "关于知微",
              click: () =>
                dialog.showMessageBox(window, {
                  type: "info",
                  title: "知微",
                  message: "知微 · 个人健康记录与问诊助手",
                  detail:
                    "本地独立窗口。默认离线演示；模型密钥可仅用于本次运行，或由系统安全凭据存储加密保存。",
                }),
            },
            { type: "separator" },
            { label: "退出", role: "quit" },
          ],
        },
        {
          label: "编辑",
          submenu: [
            { role: "undo", label: "撤销" },
            { role: "redo", label: "重做" },
            { type: "separator" },
            { role: "cut", label: "剪切" },
            { role: "copy", label: "复制" },
            { role: "paste", label: "粘贴" },
            { role: "selectAll", label: "全选" },
          ],
        },
        {
          label: "视图",
          submenu: [
            { role: "resetZoom", label: "实际大小" },
            { role: "zoomIn", label: "放大" },
            { role: "zoomOut", label: "缩小" },
            { type: "separator" },
            { role: "togglefullscreen", label: "全屏" },
          ],
        },
      ]),
    );
    try {
      await createWindow();
    } catch (error) {
      if (quitting || stopping) return;
      dialog.showErrorBox("无法打开知微", startupFailureGuidance(error));
      app.quit();
    }
  });
}
