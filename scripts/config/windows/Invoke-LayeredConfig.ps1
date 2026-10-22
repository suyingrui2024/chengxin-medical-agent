#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('common', 'dsh', 'full')]
    [string]$Mode,
    [switch]$NonInteractive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$configRoot = Split-Path -Parent $PSScriptRoot
$scriptsRoot = Split-Path -Parent $configRoot
$projectRoot = Split-Path -Parent $scriptsRoot
$python = Join-Path $projectRoot '.runtime\windows\python\python.exe'
$generator = Join-Path $configRoot 'configure.py'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Windows portable Python is missing. Run .\scripts\platform\windows\windows.ps1 -Mode Setup first.'
}

$arguments = @('-I', '-X', 'utf8', $generator, $Mode, '--project-root', $projectRoot)
if ($NonInteractive) { $arguments += '--non-interactive' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
exit 0
