#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$astErrors = $null
$astTokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot 'scripts/windows.ps1'), [ref]$astTokens, [ref]$astErrors)
if ($astErrors.Count) { throw ($astErrors | Out-String) }
foreach ($name in @('ConvertTo-ChengxinArgument', 'Invoke-ChengxinCommand')) {
    $function = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -eq $name }
    Invoke-Expression $function.Extent.Text
}
$version = (Get-Content (Join-Path $projectRoot 'package.json') -Raw | ConvertFrom-Json).version
$setup = Join-Path $projectRoot "dist/windows/Chengxin-Setup-$version-Windows-x64.exe"
$target = Join-Path $projectRoot '.runtime/installed application with spaces'
$oldBinary = $env:MEDAGENT_DESKTOP_BINARY
$oldPython = $env:MEDAGENT_PYTHON
try {
    Invoke-ChengxinCommand $setup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR=' + $target), '/TASKS=desktopicon')
    $env:MEDAGENT_DESKTOP_BINARY = Join-Path $target 'Chengxin.exe'
    $env:MEDAGENT_PYTHON = $null
    Invoke-ChengxinCommand (Join-Path $projectRoot '.runtime/windows/node/node.exe') @('scripts/windows/gui-check.mjs')
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Chengxin.lnk'
    if (-not (Test-Path -LiteralPath $shortcutPath)) { throw 'Installer did not create its desktop shortcut.' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    if ($shortcut.TargetPath -ne $env:MEDAGENT_DESKTOP_BINARY) { throw 'Installed shortcut points at the wrong executable.' }
    $menuPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Chengxin.lnk'
    if (-not (Test-Path -LiteralPath $menuPath)) { throw 'Installer did not create its Start menu shortcut.' }
    if ($shell.CreateShortcut($menuPath).TargetPath -ne $env:MEDAGENT_DESKTOP_BINARY) { throw 'Start menu shortcut points at the wrong executable.' }
    Invoke-ChengxinCommand (Join-Path $target 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (Test-Path (Join-Path $target 'Chengxin.exe')) { throw 'Uninstaller did not remove the installed executable.' }
    if ((Test-Path -LiteralPath $shortcutPath) -or (Test-Path -LiteralPath $menuPath)) { throw 'Uninstaller left application shortcuts behind.' }
    @{passed=$true; installer=$true; bundled_runtime_launched=$true; desktop_shortcut=$true; start_menu_shortcut=$true; uninstalled=$true} | ConvertTo-Json -Compress
} finally {
    $env:MEDAGENT_DESKTOP_BINARY = $oldBinary
    $env:MEDAGENT_PYTHON = $oldPython
}
