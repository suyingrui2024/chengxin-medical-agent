// Shared shell initialization for SDK jobs and the real isolation probe.
export function nativePowerShellSource(spec) {
  return (
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
    "\n}; exit $global:LASTEXITCODE"
  );
}
