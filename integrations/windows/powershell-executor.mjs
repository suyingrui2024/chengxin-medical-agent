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
      // A workspace-rooted PSDrive avoids PowerShell normalizing every parent
      // from C:\, which would require access outside the selected directory.
      // This changes only the shell's provider view, never the OS policy.
      "$null=Microsoft.PowerShell.Management\\New-PSDrive -Name ChengxinWorkspace -PSProvider FileSystem -Scope Global -Root '" +
      spec.workdir.replaceAll("'", "''") +
      "'; Microsoft.PowerShell.Management\\Set-Location -LiteralPath 'ChengxinWorkspace:\\'; [Environment]::CurrentDirectory='" +
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
