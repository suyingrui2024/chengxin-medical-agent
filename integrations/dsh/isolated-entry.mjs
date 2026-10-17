// SDK-owned launcher. Only system runtimes, the selected workspace and its state
// are mounted. Files inside a selected project are accessible; other host folders,
// the application's root .env and the host user's HOME are not mounted.
import { spawn } from "node:child_process";
import { realpathSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { nativeLaunch } from "../windows/isolation.mjs";
import { unlinkSync } from "node:fs";

export function isolatedLaunch({
  workspace,
  state,
  nodeRoot,
  modules,
  integration,
  env,
  argv,
  command,
}) {
  if (process.platform === "win32") {
    return nativeLaunch({
      workspace,
      state,
      modules,
      integration,
      env,
      command: command || [
        process.execPath,
        resolve(modules, "@deepseek-ai/dsh/lib/bin.js"),
        ...argv,
      ],
      readonly: [
        dirname(process.execPath),
        dirname(env.CHENGXIN_PYTHON),
        dirname(integration),
        env.CHENGXIN_NATIVE_PATCH,
      ].filter(Boolean),
    });
  }
  for (const path of [workspace, state, nodeRoot, modules, integration]) {
    if (typeof path !== "string" || !path.startsWith("/") || !existsSync(path))
      throw new Error("Invalid runtime mount");
  }
  const args = [
    "--unshare-user",
    "--unshare-pid",
    "--unshare-ipc",
    "--unshare-uts",
    "--die-with-parent",
    "--new-session",
    "--clearenv",
  ];
  for (const path of ["/usr", "/lib", "/lib64"])
    if (existsSync(path)) args.push("--ro-bind", path, path);
  args.push(
    "--symlink",
    "usr/bin",
    "/bin",
    "--symlink",
    "usr/sbin",
    "/sbin",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--tmpfs",
    "/tmp",
    "--dir",
    "/etc",
  );
  for (const path of [
    "/etc/ssl",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/etc/nsswitch.conf",
    "/etc/localtime",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/matplotlibrc",
    "/etc/fonts",
  ])
    if (existsSync(path)) args.push("--ro-bind", realpathSync(path), path);
  args.push(
    "--ro-bind",
    nodeRoot,
    "/opt/node",
    "--ro-bind",
    modules,
    "/opt/medical-agent/node_modules",
    "--ro-bind",
    integration,
    "/opt/medical-agent/integrations/dsh",
    "--bind",
    workspace,
    "/workspace",
    "--bind",
    state,
    "/state",
  );
  const childEnv = {
    PATH: "/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
    HOME: "/state/home",
    LANG: "C.UTF-8",
    DSH_HOME: "/state/home",
    NO_COLOR: "1",
    ...env,
  };
  for (const [key, value] of Object.entries(childEnv))
    args.push("--setenv", key, value);
  args.push(
    "--chdir",
    "/workspace",
    "--",
    ...(command || [
      "/opt/node/bin/node",
      "/opt/medical-agent/node_modules/@deepseek-ai/dsh/lib/bin.js",
      ...argv,
    ]),
  );
  return { command: "/usr/bin/bwrap", args };
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const integration = dirname(fileURLToPath(import.meta.url));
    const root = resolve(integration, "../..");
    const launch = isolatedLaunch({
      workspace: process.env.CHENGXIN_WORKSPACE,
      state: process.env.CHENGXIN_STATE,
      nodeRoot: resolve(dirname(realpathSync(process.execPath)), ".."),
      modules: resolve(root, "node_modules"),
      integration,
      env: {
        DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY,
        DEEPSEEK_BASE_URL: process.env.DEEPSEEK_BASE_URL,
        CHENGXIN_MODEL: process.env.CHENGXIN_MODEL,
        CHENGXIN_RESUME_SESSION_ID:
          process.env.CHENGXIN_RESUME_SESSION_ID || "",
        ...(process.platform === "win32"
          ? {
              CHENGXIN_PYTHON: process.env.CHENGXIN_PYTHON,
              CHENGXIN_NATIVE_PATCH: process.env.CHENGXIN_NATIVE_PATCH,
              CHENGXIN_CONTROL_PORT: process.env.CHENGXIN_CONTROL_PORT,
              CHENGXIN_CONTROL_TOKEN: process.env.CHENGXIN_CONTROL_TOKEN,
            }
          : {}),
        ...Object.fromEntries(
          ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY"]
            .filter((key) => process.env[key])
            .map((key) => [key, process.env[key]]),
        ),
      },
      argv: process.argv.slice(2),
    });
    const child = spawn(launch.command, launch.args, {
      stdio: "inherit",
      env:
        process.platform === "win32"
          ? {
              PATH: process.env.PATH,
              SystemRoot: process.env.SystemRoot,
              TEMP: process.env.TEMP,
              TMP: process.env.TMP,
            }
          : { PATH: "/usr/bin:/bin" },
    });
    for (const signal of ["SIGTERM", "SIGINT"])
      process.on(signal, () => child.kill(signal));
    child.on("error", () => {
      process.stderr.write("Isolated runtime launch failed\n");
      process.exitCode = 1;
    });
    child.on("exit", (code, signal) => {
      if (launch.policyFile) {
        try {
          unlinkSync(launch.policyFile);
        } catch {}
      }
      process.exitCode = code ?? (signal ? 1 : 0);
    });
  } catch {
    process.stderr.write("Isolated runtime configuration failed\n");
    process.exitCode = 1;
  }
}
