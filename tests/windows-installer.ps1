#requires -Version 5.1
# Exercise actual child-process argv, including PowerShell 5.1 quoting behavior.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $projectRoot 'scripts/windows.ps1'
$errorsFound = $null
$tokensFound = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokensFound, [ref]$errorsFound)
if ($errorsFound.Count) { throw ($errorsFound | Out-String) }
foreach ($name in @('ConvertTo-ChengxinArgument', 'Invoke-ChengxinCommand')) {
    $definition = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -eq $name }
    if (@($definition).Count -ne 1) { throw "Missing helper: $name" }
    Invoke-Expression $definition.Extent.Text
}
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('chengxin-argv-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
try {
    $program = Join-Path $scratch "argument probe's script.cjs"
    $output = Join-Path $scratch 'arguments.json'
    [IO.File]::WriteAllText($program, 'require("node:fs").writeFileSync(process.argv[2],JSON.stringify(process.argv.slice(3)));')
    $node = if ($env:OS -eq 'Windows_NT') { Join-Path $projectRoot '.runtime/windows/node/node.exe' } else { (Get-Command node).Source }
    $han = [string][char]0x4E2D + [string][char]0x6587
    $values = @('', 'contains spaces', "a project's path", 'quoted "value"', 'C:\trailing\', '$(do-not-evaluate)', $han)
    Invoke-ChengxinCommand $node (@($program, $output) + $values)
    $decoded = Get-Content -LiteralPath $output -Raw -Encoding UTF8 | ConvertFrom-Json
    $actual = @($decoded)
    if ($actual.Count -ne $values.Count) { throw 'Native argv length changed.' }
    for ($i = 0; $i -lt $values.Count; $i++) {
        if ($actual[$i] -cne $values[$i]) { throw "Native argument $i changed." }
    }
    $rejected = $false
    try { Invoke-ChengxinCommand $node @('-e', 'process.exit(7)') } catch { $rejected = $true }
    if (-not $rejected) { throw 'Failed child command was accepted.' }
    @{passed=$true; arguments=$values.Count; failed_exit_rejected=$true; powershell=$PSVersionTable.PSVersion.ToString()} | ConvertTo-Json -Compress
} finally {
    Remove-Item -LiteralPath $scratch -Recurse -Force
}
