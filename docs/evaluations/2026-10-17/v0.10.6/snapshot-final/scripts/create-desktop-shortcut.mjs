import { writeFile, chmod } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// This workspace lives on the Desktop; do not write to a system installation directory.
const desktop = path.dirname(root);
if (path.basename(desktop) !== "Desktop") {
  throw new Error(
    "请将项目放在 Desktop 下，或手动使用 scripts/start-desktop.sh 启动。",
  );
}
function escapeValue(value) {
  if (/[\r\n]/.test(value)) throw new Error("路径中不能含换行符");
  return value.replaceAll("\\", "\\\\");
}
function quoteExec(value) {
  return `"${escapeValue(value).replace(/["`$]/g, "\\$&").replaceAll("%", "%%")}"`;
}
const launcher = path.join(desktop, "澄心问诊.desktop");
const script = path.join(root, "scripts", "start-desktop.sh");
await chmod(script, 0o755);
await writeFile(
  launcher,
  `[Desktop Entry]\nVersion=1.0\nType=Application\nName=澄心问诊\nComment=独立桌面问诊研究工作台\nExec=${quoteExec(script)}\nPath=${escapeValue(root)}\nIcon=${escapeValue(path.join(root, "desktop", "icon.png"))}\nTerminal=false\nStartupNotify=true\nStartupWMClass=Chengxin\nCategories=Science;\n`,
);
await chmod(launcher, 0o755);
console.log(`桌面启动图标已创建：${launcher}`);
console.log("GNOME 首次使用可能需要右键选择“允许启动”。");
