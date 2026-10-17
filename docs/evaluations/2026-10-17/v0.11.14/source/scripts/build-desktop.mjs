import { packager } from "@electron/packager";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { readFile, mkdir, mkdtemp, cp, rm } from "node:fs/promises";
import { execFileSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const metadata = JSON.parse(
  await readFile(path.join(root, "package.json"), "utf8"),
);
if (process.platform !== "linux" || process.arch !== "x64") {
  throw new Error(
    "当前打包配置已针对 Linux x64 编写；其他平台需单独验证后增加。 ",
  );
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
        !source.includes("__pycache__") && !source.endsWith(".pyc"),
    });
  execFileSync(
    "npm",
    ["ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
    { cwd: staging, stdio: "inherit" },
  );
  const outputs = await packager({
    dir: staging,
    name: `Chengxin-${metadata.version}`,
    executableName: "Chengxin",
    platform: "linux",
    arch: "x64",
    out: path.join(root, "dist", "desktop"),
    overwrite: true,
    asar: false, // Python and DSH need real filesystem paths.
    prune: false,
    electronVersion: metadata.devDependencies.electron,
    ignore: [
      /^\/(?:dist|build|vendor|docs|tests|experiments|test-results|playwright-report|data|scripts|workspaces|\.runtime|\.venv|\.git|\.agents|\.codex)(?:\/|$)/,
      /(?:^|\/)\.env(?:[ .]|$)/,
      /(?:^|\/)__pycache__(?:\/|$)/,
      /\.py[co]$/,
    ],
  });
  console.log(`桌面应用已生成：${path.join(outputs[0], "Chengxin")}`);
  console.log(
    "保留整个应用目录；默认离线模式需要系统 Python 3.10+，DSH 另需系统 Node 22.19+。",
  );
} finally {
  await rm(staging, { recursive: true, force: true });
}
