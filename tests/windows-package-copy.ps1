#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot 'scripts/platform/windows/packaged-execution-check.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$function = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -eq 'Copy-ChengxinTestPackage' }
if (-not $function) { throw 'Package copy function is missing' }
Invoke-Expression $function.Extent.Text
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('Chengxin copy status ' + [Guid]::NewGuid().ToString('N'))
try {
    $source = Join-Path $scratch 'source app'
    $destination = Join-Path $scratch 'copied app'
    [IO.Directory]::CreateDirectory($source) | Out-Null
    [IO.File]::WriteAllText((Join-Path $source 'fixture.txt'), 'synthetic package')
    # A fresh real copy returns Robocopy's success code 1, which must not
    # become the surrounding PowerShell/Actions process exit status.
    $global:LASTEXITCODE = 99
    Copy-ChengxinTestPackage $source $destination
    if ($global:LASTEXITCODE -ne 0) { throw 'Successful new-file copy leaked a native exit code' }
    if ([IO.File]::ReadAllText((Join-Path $destination 'fixture.txt')) -ne 'synthetic package') { throw 'Copy did not preserve file content' }
    Copy-ChengxinTestPackage $source $destination
    if ($global:LASTEXITCODE -ne 0) { throw 'Successful unchanged copy leaked a native exit code' }
    $failed = $false
    try {
        Copy-ChengxinTestPackage (Join-Path $scratch 'missing source') $destination
    } catch {
        if ($_.Exception.Message -notlike 'Unable to copy the test package*') { throw }
        $failed = $true
    }
    if (-not $failed) { throw 'A real copy failure was accepted' }
    # The preceding nonzero status was an expected assertion, not a failure
    # of this regression script. Unexpected failures above remain terminating.
    $global:LASTEXITCODE = 0
    @{passed=$true; copied_content=$true; success_status_normalized=$true; copy_failure_rejected=$true} | ConvertTo-Json -Compress
} finally {
    if (Test-Path -LiteralPath $scratch) { [IO.Directory]::Delete($scratch, $true) }
}
