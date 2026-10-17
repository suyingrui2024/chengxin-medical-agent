const { app, BrowserWindow, Menu, dialog, session } = require("electron");
const { spawn } = require("node:child_process");
const { randomBytes } = require("node:crypto");
const path = require("node:path");
const fs = require("node:fs");
const readline = require("node:readline");

const projectRoot = path.resolve(__dirname, "..");
const { version } = require("../package.json");
app.setName("澄心问诊");
app.setPath(
  "userData",
  process.env.MEDAGENT_DESKTOP_PROFILE ||
    path.join(app.getPath("appData"), `chengxin-medagent-${version}`),
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

function pythonCommand() {
  if (process.env.MEDAGENT_PYTHON) return process.env.MEDAGENT_PYTHON;
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

function startBackend(token) {
  return new Promise((resolve, reject) => {
    backend = spawn(pythonCommand(), ["-u", "-m", "medagent.desktop_service"], {
      cwd: projectRoot,
      detached: process.platform !== "win32",
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
      env: {
        ...process.env,
        PYTHONPATH: path.join(projectRoot, "src"),
        PYTHONDONTWRITEBYTECODE: "1",
        MEDAGENT_EXECUTION_ROOT:
          process.env.MEDAGENT_EXECUTION_ROOT ||
          path.join(app.getPath("desktop"), "澄心工作区"),
      },
    });
    const timer = setTimeout(() => reject(new Error("启动超时")), 15000);
    const lines = readline.createInterface({ input: backend.stdout });
    let ready = false;
    lines.on("line", (line) => {
      if (ready) return;
      try {
        const message = JSON.parse(line);
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
    backend.on("error", () => {
      clearTimeout(timer);
      reject(new Error("未能启动 Python"));
    });
    backend.on("exit", () => {
      clearTimeout(timer);
      if (!ready) reject(new Error("本地问诊服务未能启动"));
      else if (!stopping) {
        dialog.showErrorBox(
          "问诊服务已停止",
          "请关闭应用后重新打开。内存会谈无法跨进程恢复。",
        );
        app.quit();
      }
    });
    backend.stdin.on("error", () => {});
    backend.stderr.on("data", () => {}); // Do not write patient data or provider errors to desktop logs.
    backend.stdin.write(`${JSON.stringify({ token })}\n`);
  });
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
  const privateSession = session.fromPartition("chengxin-desktop");
  privateSession.setPermissionRequestHandler(
    (_contents, _permission, callback) => callback(false),
  );
  privateSession.setPermissionCheckHandler(() => false);
  window = new BrowserWindow({
    title: "澄心问诊",
    width: 1440,
    height: 980,
    minWidth: 900,
    minHeight: 680,
    backgroundColor: "#f5f6f2",
    icon: path.join(__dirname, "icon.png"),
    show: false,
    webPreferences: {
      session: privateSession,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
      devTools: !app.isPackaged,
    },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.webContents.on("will-navigate", (event, url) => {
    if (!readyOrigin || new URL(url).origin !== readyOrigin)
      event.preventDefault();
  });
  window.on("page-title-updated", (event) => event.preventDefault());
  window.once("ready-to-show", () => window.show());
  await window.loadFile(path.join(__dirname, "starting.html"));
  const token = randomBytes(32).toString("hex");
  readyOrigin = await startBackend(token);
  privateSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders };
    if (new URL(details.url).origin === readyOrigin)
      headers["X-MedAgent-Token"] = token;
    callback({ requestHeaders: headers });
  });
  await window.loadURL(readyOrigin);
}

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
              label: "关于澄心问诊",
              click: () =>
                dialog.showMessageBox(window, {
                  type: "info",
                  title: "澄心问诊",
                  message: "澄心问诊 · 桌面研究原型",
                  detail:
                    "本地独立窗口。默认离线演示；会谈和模型配置仅在进程内保存。",
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
    } catch {
      dialog.showErrorBox(
        "无法打开澄心问诊",
        "请确认已安装 Python 3.10+，并保留完整的应用目录。可设置 MEDAGENT_PYTHON 指向 Python 可执行文件。模型依赖安装方法请查看项目说明。",
      );
      app.quit();
    }
  });
}
