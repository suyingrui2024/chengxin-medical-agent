// Reuse the pinned official job/output/cancellation implementation with a native shell.
import { LocalBashExecutor } from "@deepseek-ai/dsh-bash-local";
import { join } from "node:path";

export default class NativePowerShellExecutor extends LocalBashExecutor {
  execute(spec) {
    const shell = join(
      process.env.SystemRoot,
      "System32/WindowsPowerShell/v1.0/powershell.exe",
    );
    const source =
      "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; $global:LASTEXITCODE=0; " +
      // PowerShell's provider location can differ from the Win32 process cwd
      // under ProcessContainer. Set the exact requested directory explicitly.
      "Microsoft.PowerShell.Management\\Set-Location -LiteralPath '" +
      spec.workdir.replaceAll("'", "''") +
      "'; & {\n" +
      spec.command +
      "\n}; exit $global:LASTEXITCODE";
    return this.executeArgv(spec, [
      shell,
      "-NoLogo",
      "-NoProfile",
      "-ExecutionPolicy",
      "Bypass",
      "-NonInteractive",
      "-EncodedCommand",
      Buffer.from(source, "utf16le").toString("base64"),
    ]);
  }
}
