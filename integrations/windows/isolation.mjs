// Windows native executor from Microsoft's pinned MIT-licensed MXC distribution.
// Use its versioned JSON CLI, not a Node-24-only SDK or any WSL backend.
import {
  existsSync,
  mkdirSync,
  writeFileSync,
  realpathSync,
  unlinkSync,
} from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join, resolve, win32 } from "node:path";
import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const realpathAdapter = join(root, "integrations/windows/realpath-adapter.cjs");

export function windowsArgument(value) {
  return (
    '"' +
    String(value)
      .replace(/(\\*)"/g, '$1$1\\"')
      .replace(/(\\+)$/, "$1$1") +
    '"'
  );
}

export function executorPath() {
  for (const base of ["runtime/isolation", ".runtime/windows/isolation"]) {
    const candidate = join(root, base, "wxc-exec.exe");
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    "Windows native isolation runtime is missing. Run scripts/windows.ps1 -Mode Setup.",
  );
}

export function requestFor({
  workspace,
  state,
  modules,
  integration,
  env,
  command,
  readonly = [],
}) {
  for (const directory of [
    workspace,
    state,
    modules,
    integration,
    ...readonly,
  ]) {
    if (!win32.isAbsolute(directory) || !existsSync(directory))
      throw new Error("Invalid Windows runtime path");
  }
  const home = join(state, "home"),
    temporary = join(state, "tmp");
  mkdirSync(home, { recursive: true });
  mkdirSync(temporary, { recursive: true });
  const systemRoot = process.env.SystemRoot || process.env.WINDIR;
  if (!systemRoot || !win32.isAbsolute(systemRoot))
    throw new Error("Windows system runtime is unavailable");
  const childEnv = {
    SystemRoot: systemRoot,
    WINDIR: systemRoot,
    SystemDrive: win32.parse(systemRoot).root.slice(0, 2),
    ComSpec: join(systemRoot, "System32/cmd.exe"),
    PATH: [
      dirname(realpathSync(process.execPath)),
      env.CHENGXIN_PYTHON ? dirname(env.CHENGXIN_PYTHON) : "",
      join(systemRoot, "System32"),
      join(systemRoot, "System32/WindowsPowerShell/v1.0"),
    ]
      .filter(Boolean)
      .join(";"),
    HOME: home,
    USERPROFILE: home,
    APPDATA: join(home, "AppData/Roaming"),
    LOCALAPPDATA: join(home, "AppData/Local"),
    TEMP: temporary,
    TMP: temporary,
    CHENGXIN_TEMP: temporary,
    DSH_HOME: home,
    NO_COLOR: "1",
    // Node's realpath walk lstats drive-root metadata before loading a file.
    // Keep the host-canonicalized, read-only bundle paths rather than granting
    // recursive read access to C:\ merely to satisfy module resolution.
    NODE_OPTIONS:
      "--preserve-symlinks --preserve-symlinks-main --require=" +
      JSON.stringify(realpathAdapter.replaceAll("\\", "/")),
    PYTHONUTF8: "1",
    PYTHONNOUSERSITE: "1",
  };
  for (const [name, value] of Object.entries(env))
    if (!Object.hasOwn(childEnv, name)) childEnv[name] = value;
  for (const name of ["APPDATA", "LOCALAPPDATA"])
    mkdirSync(childEnv[name], { recursive: true });
  return {
    version: "1.0.0",
    containerId: "Chengxin-" + randomBytes(16).toString("hex"),
    containment: "processcontainer",
    fallback: { allowDaclMutation: false },
    lifecycle: { destroyOnExit: true, preservePolicy: false },
    process: {
      commandLine: command.map(windowsArgument).join(" "),
      cwd: workspace,
      env: Object.entries(childEnv).map(([key, value]) => `${key}=${value}`),
      inheritDefaultEnv: false,
      timeout: 1820000,
    },
    filesystem: {
      readwritePaths: [workspace, state],
      readonlyPaths: [
        modules,
        integration,
        realpathAdapter,
        join(root, "package.json"),
        ...readonly,
      ],
      deniedPaths: [],
    },
    network: {
      egress: { default: "allow" },
      ingress: { default: "allow", hostLoopback: "allow" },
    },
    // PowerShell and Windows' x64 compatibility loader need Win32k during
    // initialization. Retain MXC's default container-scoped UI handles/atoms,
    // clipboard denial, input denial and system-setting restrictions.
    ui: { disable: false, clipboard: "none", injection: false },
    processContainer: { leastPrivilege: false, capabilities: [] },
    telemetry: { enabled: false },
  };
}

export function nativeLaunch(options) {
  const request = requestFor(options);
  const base = resolve(options.state, "../..", ".native-policies");
  mkdirSync(base, { recursive: true });
  const file = join(base, request.containerId + ".json");
  writeFileSync(file, JSON.stringify(request), { mode: 0o600, flag: "wx" });
  try {
    const raw = execFileSync(executorPath(), ["--probe", file], {
      encoding: "utf8",
      timeout: 15000,
      windowsHide: true,
    });
    const probe = JSON.parse(raw);
    // No host ACL augmentation, WSL, permissive audit, or unrestricted fallback.
    if (
      probe.error ||
      probe.tier !== "base-container" ||
      probe.needsDaclAugmentation
    ) {
      const failure = new Error(
        "Windows native process isolation and loopback support require current Windows 11 updates.",
      );
      failure.probe = probe;
      throw failure;
    }
    return {
      command: executorPath(),
      args: [file],
      policyFile: file,
      isolation: "windows-processcontainer",
    };
  } catch (error) {
    try {
      unlinkSync(file);
    } catch {}
    throw new Error(
      "Windows native execution isolation is unavailable; update Windows and run its installation checks.",
      { cause: error },
    );
  }
}
