#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$astErrors = $null
$astTokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot 'scripts/platform/windows/windows.ps1'), [ref]$astTokens, [ref]$astErrors)
if ($astErrors.Count) { throw ($astErrors | Out-String) }
foreach ($name in @('ConvertTo-ChengxinArgument', 'Invoke-ChengxinCommand')) {
    $function = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -eq $name }
    Invoke-Expression $function.Extent.Text
}
$metadata = Get-Content (Join-Path $projectRoot 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $metadata.version
$shortcutName = $metadata.productName + '.lnk'
$setup = Join-Path $projectRoot "dist/windows/Chengxin-Setup-$version-Windows-x64.exe"
$target = Join-Path $projectRoot '.runtime/installed application with spaces'
$oldBinary = $env:MEDAGENT_DESKTOP_BINARY
$oldPython = $env:MEDAGENT_PYTHON
try {
    Invoke-ChengxinCommand $setup @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR=' + $target), '/TASKS=desktopicon')
    $env:MEDAGENT_DESKTOP_BINARY = Join-Path $target 'Chengxin.exe'
    $env:MEDAGENT_PYTHON = $null
    Invoke-ChengxinCommand (Join-Path $projectRoot '.runtime/windows/node/node.exe') @('scripts/platform/windows/gui-check.mjs')
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) $shortcutName
    if (-not (Test-Path -LiteralPath $shortcutPath)) { throw 'Installer did not create its desktop shortcut.' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    # Shell links may expose a short (8.3) path; compare native file identity, not spelling.
    $installedPython = Join-Path $target 'resources/app/runtime/python/python.exe'
    $verifyTarget = 'import os,sys; actual,expected=sys.argv[1:]; assert os.path.samefile(actual,expected), f"Shortcut target differs: {actual!r} != {expected!r}"'
    Write-Host ('Desktop shortcut target: ' + $shortcut.TargetPath + ' expected: ' + $env:MEDAGENT_DESKTOP_BINARY)
    Invoke-ChengxinCommand $installedPython @('-I', '-c', $verifyTarget, $shortcut.TargetPath, $env:MEDAGENT_DESKTOP_BINARY)
    $menuPath = Join-Path ([Environment]::GetFolderPath('Programs')) $shortcutName
    if (-not (Test-Path -LiteralPath $menuPath)) { throw 'Installer did not create its Start menu shortcut.' }
    $menuTarget = $shell.CreateShortcut($menuPath).TargetPath
    Write-Host ('Start menu shortcut target: ' + $menuTarget + ' expected: ' + $env:MEDAGENT_DESKTOP_BINARY)
    Invoke-ChengxinCommand $installedPython @('-I', '-c', $verifyTarget, $menuTarget, $env:MEDAGENT_DESKTOP_BINARY)
    Invoke-ChengxinCommand (Join-Path $target 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (Test-Path (Join-Path $target 'Chengxin.exe')) { throw 'Uninstaller did not remove the installed executable.' }
    if ((Test-Path -LiteralPath $shortcutPath) -or (Test-Path -LiteralPath $menuPath)) { throw 'Uninstaller left application shortcuts behind.' }
    @{passed=$true; installer=$true; bundled_runtime_launched=$true; desktop_shortcut=$true; start_menu_shortcut=$true; uninstalled=$true} | ConvertTo-Json -Compress
} finally {
    $env:MEDAGENT_DESKTOP_BINARY = $oldBinary
    $env:MEDAGENT_PYTHON = $oldPython
}
