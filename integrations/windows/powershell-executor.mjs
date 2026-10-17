// Reuse the pinned official job/output/cancellation implementation with a native shell.
import { LocalBashExecutor } from "@deepseek-ai/dsh-bash-local";
import { join } from "node:path";
import { nativePowerShellSource } from "./powershell-command.mjs";

export default class NativePowerShellExecutor extends LocalBashExecutor {
  execute(spec) {
    const shell = join(
      process.env.SystemRoot,
      "System32/WindowsPowerShell/v1.0/powershell.exe",
    );
    return this.executeArgv(spec, [
      shell,
      "-NoLogo",
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-NonInteractive",
      "-EncodedCommand",
      Buffer.from(nativePowerShellSource(spec), "utf16le").toString("base64"),
    ]);
  }
}
