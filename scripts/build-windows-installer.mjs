import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

if (process.platform !== "win32" || process.arch !== "x64")
  throw new Error(
    "Build this installer on Windows with the x64 project runtime",
  );
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { version } = JSON.parse(
  readFileSync(join(root, "package.json"), "utf8"),
);
const source = join(root, "dist/desktop", `Chengxin-${version}-win32-x64`);
for (const relative of [
  "Chengxin.exe",
  "resources/app/runtime/python/python.exe",
  "resources/app/runtime/node/node.exe",
  "resources/app/runtime/isolation/wxc-exec.exe",
]) {
  if (!existsSync(join(source, relative)))
    throw new Error(`Windows application is incomplete: ${relative}`);
}
const compiler = [
  process.env.MEDAGENT_ISCC,
  ...[process.env["ProgramFiles(x86)"], process.env.ProgramFiles]
    .filter(Boolean)
    .map((directory) => join(directory, "Inno Setup 6/ISCC.exe")),
].find((file) => file && existsSync(file));
if (!compiler)
  throw new Error(
    "Install Inno Setup 6.7+ or set MEDAGENT_ISCC to its ISCC.exe; portable application build is already available.",
  );
execFileSync(
  compiler,
  [
    `/DAppVersion=${version}`,
    `/DAppSource=${source}`,
    `/DOutputDirectory=${join(root, "dist/windows")}`,
    join(root, "desktop/windows-installer.iss"),
  ],
  { cwd: root, stdio: "inherit", windowsHide: true },
);
console.log(
  join(root, "dist/windows", `Chengxin-Setup-${version}-Windows-x64.exe`),
);
