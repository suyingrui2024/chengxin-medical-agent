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

function Remove-ChengxinTestCopy {
    param([Parameter(Mandatory=$true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # PowerShell 5.1's Remove-Item can fail on the deeply nested dependency
    # paths within an Electron package.  The Windows extended-length path API
    # removes only this test copy and is safe for those dependency trees.
    $extendedPath = if ($Path.StartsWith('\\?\')) { $Path } else { '\\?\' + $Path }
    [System.IO.Directory]::Delete($extendedPath, $true)
}

function Copy-ChengxinTestPackage {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )
    # Unlike Copy-Item in Windows PowerShell 5.1, robocopy has long-path
    # support.  Electron's dependency tree otherwise exceeds MAX_PATH when
    # this validation deliberately adds a directory name with spaces.
    $robocopy = Join-Path $env:SystemRoot 'System32\robocopy.exe'
    & $robocopy $Source $Destination '/E' '/COPY:DAT' '/DCOPY:T' '/R:1' '/W:1' '/NJH' '/NJS' '/NDL' '/NFL' '/NP'
    if ($LASTEXITCODE -gt 7) { throw "Unable to copy the test package (robocopy exit code $LASTEXITCODE)." }
}

$originalApplication = (Resolve-Path -LiteralPath $ApplicationDirectory).Path
$application = $originalApplication
$copied = $false
$oldPath = $env:PATH
$oldPython = $env:MEDAGENT_PYTHON
$oldExecutionRoot = $env:CHENGXIN_EXECUTION_ROOT
try {
    if ($PathWithSpaces) {
        $application = Join-Path (Split-Path -Parent $originalApplication) ('Chengxin app with spaces ' + [Guid]::NewGuid().ToString('N'))
        # Keep the original package in place.  Moving a large Electron directory
        # can be interrupted by an antivirus scan, leaving a split package during
        # test cleanup.  The temporary copy stays on the E: project drive and is
        # removed after the validation finishes.
        Copy-ChengxinTestPackage $originalApplication $application
        $copied = $true
    }
    $packageRoot = Join-Path $application 'resources/app'
    $python = Join-Path $packageRoot 'runtime/python/python.exe'
    $nodeRoot = Join-Path $packageRoot 'runtime/node'
    foreach ($file in @($python, (Join-Path $nodeRoot 'node.exe'), (Join-Path $packageRoot 'integrations/dsh/execution-worker.mjs'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing packaged runtime: $file" }
    }
    $env:PATH = $nodeRoot + ';' + (Split-Path -Parent $python) + ';' + $oldPath
    $env:MEDAGENT_PYTHON = $python
    $env:CHENGXIN_EXECUTION_ROOT = $packageRoot
    $bootstrap = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
    Invoke-ChengxinCommand $python @('-I', '-X', 'utf8', '-c', $bootstrap, (Join-Path $packageRoot 'src'), (Join-Path $projectRoot 'tests/test_windows_execution.py'))
    Invoke-ChengxinCommand (Join-Path $nodeRoot 'node.exe') @((Join-Path $projectRoot 'integrations/dsh/tests/root-wakeup-runtime.mjs'))
    @{passed=$true; packaged_execution=$true; python=$python; node=(Join-Path $nodeRoot 'node.exe'); source=$packageRoot} | ConvertTo-Json -Compress
} finally {
    $env:PATH = $oldPath
    $env:MEDAGENT_PYTHON = $oldPython
    $env:CHENGXIN_EXECUTION_ROOT = $oldExecutionRoot
    if ($copied -and (Test-Path -LiteralPath $application)) {
        Remove-ChengxinTestCopy $application
    }
}
