#requires -Version 5.1
[CmdletBinding()]
param([switch]$NonInteractive)

$arguments = @{ Mode = 'dsh'; NonInteractive = $NonInteractive }
& (Join-Path $PSScriptRoot 'Invoke-LayeredConfig.ps1') @arguments
exit $LASTEXITCODE
