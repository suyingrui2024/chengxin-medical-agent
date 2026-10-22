import { packager } from "@electron/packager";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  access,
  readFile,
  mkdir,
  mkdtemp,
  cp,
  readdir,
  rm,
} from "node:fs/promises";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const metadata = JSON.parse(
  await readFile(path.join(root, "package.json"), "utf8"),
);
async function findCachedWindowsElectronZip() {
  if (process.platform !== "win32" || !process.env.ELECTRON_CACHE)
    return undefined;
  const fileName = `electron-v${metadata.devDependencies.electron}-win32-x64.zip`;
  try {
    for (const entry of await readdir(process.env.ELECTRON_CACHE, {
      withFileTypes: true,
    })) {
      if (!entry.isDirectory()) continue;
      const candidate = path.join(
        process.env.ELECTRON_CACHE,
        entry.name,
        fileName,
      );
      try {
        await access(candidate);
        return path.dirname(candidate);
      } catch {}
    }
  } catch {}
  return undefined;
}

if (!["linux", "win32"].includes(process.platform) || process.arch !== "x64") {
  throw new Error("当前打包目标为 Linux x64 或 Windows x64。");
}

// Packager's pruning drops transitive peer dependencies in this DSH graph.
// Let npm materialize the locked production graph, then package it unchanged.
const scratchRoot = path.join(root, ".runtime");
await mkdir(scratchRoot, { recursive: true });
const staging = await mkdtemp(path.join(scratchRoot, "desktop-production-"));
try {
  for (const name of [
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "README.md",
    "THIRD_PARTY_NOTICES.md",
    "src",
    "desktop",
    "integrations",
  ])
    await cp(path.join(root, name), path.join(staging, name), {
      recursive: true,
      filter: (source) =>
        !source.includes("__pycache__") &&
        !source.endsWith(".pyc") &&
        !source.split(path.sep).includes("node_modules"),
    });
  const npmArguments = [
    "ci",
    "--omit=dev",
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
  ];
  const windows = process.platform === "win32";
  const npmCli = process.env.MEDAGENT_NPM_CLI;
  if (windows && !npmCli)
    throw new Error(
      "请使用 scripts/platform/windows/windows.ps1 -Mode Build，确保采用项目专用运行时。",
    );
  execFileSync(
    windows ? process.execPath : "npm",
    windows ? [npmCli, ...npmArguments] : npmArguments,
    { cwd: staging, stdio: "inherit" },
  );
  if (windows) {
    // A Windows distribution carries its own interpreter and Node runtime.
    for (const name of ["python", "node", "isolation"]) {
      await cp(
        path.join(root, ".runtime/windows", name),
        path.join(staging, "runtime", name),
        { recursive: true },
      );
    }
  }
  // Setup verifies Electron before caching it. Reuse that cache on Windows so
  // Packager does not block on an additional SHASUMS256 network request.
  const electronZipDir = await findCachedWindowsElectronZip();
  // Electron Packager otherwise expands more than 1 GB under the system TEMP.
  // Keep the build-only workspace on the project drive; DSH tests retain their
  // own host-temp behavior because container-owned session folders need it.
  const packagerTemp = windows
    ? path.join(root, ".runtime", "windows", "desktop-packager-temp")
    : undefined;
  if (packagerTemp) await mkdir(packagerTemp, { recursive: true });
  const outputs = await packager({
    dir: staging,
    name: `Chengxin-${metadata.version}`,
    executableName: "Chengxin",
    platform: process.platform,
    arch: "x64",
    out: path.join(root, "dist", "desktop"),
    overwrite: true,
    asar: false, // Python and DSH need real filesystem paths.
    prune: false,
    electronVersion: metadata.devDependencies.electron,
    electronZipDir,
    tmpdir: packagerTemp,
    ignore: [
      /^\/(?:dist|build|vendor|docs|tests|experiments|test-results|playwright-report|data|scripts|workspaces|\.runtime|\.venv|\.git|\.agents|\.codex)(?:\/|$)/,
      /(?:^|\/)\.env(?:[ .]|$)/,
      /(?:^|\/)__pycache__(?:\/|$)/,
      /\.py[co]$/,
    ],
  });
  console.log(
    `桌面应用已生成：${path.join(outputs[0], windows ? "Chengxin.exe" : "Chengxin")}`,
  );
  console.log(
    windows
      ? "保留整个目录；Windows Python 和 Node 已随包附带，无需 WSL。"
      : "保留整个应用目录；默认离线模式需要系统 Python 3.10+，DSH 另需系统 Node 22.19+。",
  );
} finally {
  await rm(staging, { recursive: true, force: true });
}
