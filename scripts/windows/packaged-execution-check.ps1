#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ApplicationDirectory, [switch]$PathWithSpaces)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$astErrors = $null
$astTokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot 'scripts/windows.ps1'), [ref]$astTokens, [ref]$astErrors)
if ($astErrors.Count) { throw ($astErrors | Out-String) }
foreach ($name in @('ConvertTo-ChengxinArgument', 'Invoke-ChengxinCommand')) {
    $function = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -eq $name }
    Invoke-Expression $function.Extent.Text
}
$originalApplication = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
$application = $originalApplication
$moved = $false
$oldPath = $env:PATH
try {
    if ($PathWithSpaces) {
        $application = Join-Path (Split-Path -Parent $originalApplication) ('Chengxin app with spaces ' + [Guid]::NewGuid().ToString('N'))
        Move-Item -LiteralPath $originalApplication -Destination $application
        $moved = $true
    }
    $packageRoot = Join-Path $application 'resources/app'
    $python = Join-Path $packageRoot 'runtime/python/python.exe'
    $nodeRoot = Join-Path $packageRoot 'runtime/node'
    foreach ($file in @($python, (Join-Path $nodeRoot 'node.exe'), (Join-Path $packageRoot 'integrations/dsh/execution-worker.mjs'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing packaged runtime: $file" }
    }
    $env:PATH = $nodeRoot + ';' + (Split-Path -Parent $python) + ';' + $oldPath
    $bootstrap = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
    Invoke-ChengxinCommand $python @('-I', '-X', 'utf8', '-c', $bootstrap, (Join-Path $packageRoot 'src'), (Join-Path $projectRoot 'tests/test_windows_execution.py'))
    @{passed=$true; packaged_execution=$true; python=$python; node=(Join-Path $nodeRoot 'node.exe'); source=$packageRoot} | ConvertTo-Json -Compress
} finally {
    $env:PATH = $oldPath
    if ($moved) { Move-Item -LiteralPath $application -Destination $originalApplication }
}
