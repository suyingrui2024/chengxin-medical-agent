#requires -Version 5.1
[CmdletBinding()]
param([switch]$NonInteractive)

$arguments = @{ Mode = 'full'; NonInteractive = $NonInteractive }
& (Join-Path $PSScriptRoot 'Invoke-LayeredConfig.ps1') @arguments
exit $LASTEXITCODE
