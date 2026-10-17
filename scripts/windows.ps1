#requires -Version 5.1
<#! Native Windows setup. No WSL, global PATH changes or administrator install. !#>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Check', 'Start', 'Build', 'Test')]
    [string]$Mode = 'Setup',
    [switch]$Live,
    [switch]$NoShortcut
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot '.runtime\windows'
$node = Join-Path $runtimeRoot 'node\node.exe'
$python = Join-Path $runtimeRoot 'python\python.exe'
$npmCli = Join-Path $runtimeRoot 'node\node_modules\npm\bin\npm-cli.js'
$previousPath = $env:PATH
$previousEncoding = [Console]::OutputEncoding
$previousPipelineEncoding = $OutputEncoding
$previousPython = $env:MEDAGENT_PYTHON
$previousNpm = $env:MEDAGENT_NPM_CLI
$previousElectronMode = $env:ELECTRON_RUN_AS_NODE
$previousSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol

function ConvertTo-ChengxinArgument {
    param([AllowEmptyString()][string]$Value)
    $result = New-Object Text.StringBuilder
    [void]$result.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void]$result.Append(('\' * (2 * $slashes + 1)))
        } else {
            [void]$result.Append(('\' * $slashes))
        }
        [void]$result.Append($character)
        $slashes = 0
    }
    [void]$result.Append(('\' * (2 * $slashes)))
    [void]$result.Append('"')
    return $result.ToString()
}

function Invoke-ChengxinCommand {
    param([string]$Executable, [string[]]$CommandArguments)
    # PowerShell 5.1 otherwise strips embedded quotes from python -c / node -e.
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (($CommandArguments | ForEach-Object { ConvertTo-ChengxinArgument $_ }) -join ' ')
    $start.UseShellExecute = $false
    $start.WorkingDirectory = $projectRoot
    $process = [Diagnostics.Process]::Start($start)
    try {
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Command failed (exit $($process.ExitCode)): $Executable" }
    } finally { $process.Dispose() }
}

function Install-ChengxinRuntime {
    param([string]$Name, $Specification)
    $target = Join-Path $runtimeRoot $Name
    $receipt = Join-Path $target 'chengxin-runtime.json'
    if (Test-Path -LiteralPath $target) {
        if (-not (Test-Path -LiteralPath $receipt -PathType Leaf)) { throw "Incomplete runtime: $target. Move it aside before rerunning Setup." }
        $installed = Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($installed.sha256 -ne $Specification.sha256) { throw "Runtime version changed: $Name. Move its old directory aside, then rerun Setup." }
        return
    }
    $stage = Join-Path $runtimeRoot ('download-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        $archive = Join-Path $stage 'runtime.zip'
        Write-Host "Downloading official $Name $($Specification.version)..."
        Invoke-WebRequest -UseBasicParsing -Uri $Specification.url -OutFile $archive
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Specification.sha256) { throw "SHA256 verification failed: $Name" }
        $expanded = Join-Path $stage 'expanded'
        if ($Name -eq 'isolation') {
            New-Item -ItemType Directory -Path $expanded | Out-Null
            Invoke-ChengxinCommand (Join-Path $env:SystemRoot 'System32\tar.exe') @('-xzf', $archive, '-C', $expanded)
        } else {
            Expand-Archive -LiteralPath $archive -DestinationPath $expanded
        }
        $content = $expanded
        if ($Name -in @('node', 'isolation')) { $content = Join-Path $expanded $Specification.archive_directory }
        if ($Name -eq 'isolation') {
            $selected = Join-Path $stage 'native-executor'
            New-Item -ItemType Directory -Path $selected | Out-Null
            foreach ($binary in @('wxc-exec.exe', 'wxc-host-prep.exe')) {
                Copy-Item -LiteralPath (Join-Path $content $binary) -Destination $selected
            }
            Copy-Item -LiteralPath (Join-Path $expanded 'package\LICENSE.md') -Destination (Join-Path $selected 'LICENSE.md')
            $content = $selected
        }
        $specJson = $Specification | ConvertTo-Json -Depth 5
        [IO.File]::WriteAllText((Join-Path $content 'chengxin-runtime.json'), $specJson, (New-Object Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $content -Destination $target
    } finally {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
}

function Invoke-ChengxinPython {
    param([string]$Script, [string[]]$ScriptArguments = @())
    $bootstrap = 'import runpy,sys;sys.path.insert(0,sys.argv.pop(1));p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
    Invoke-ChengxinCommand $python (@('-I', '-X', 'utf8', '-c', $bootstrap, (Join-Path $projectRoot 'src'), $Script) + $ScriptArguments)
}

function Test-ChengxinInstallation {
    Invoke-ChengxinCommand $python @('-I', '-X', 'utf8', '-c', 'import sys,ssl,ctypes;assert sys.version_info[:3]==(3,14,8);print("Windows Python:",sys.version.split()[0])')
    Invoke-ChengxinCommand $node @('-e', 'if(process.platform!=="win32"||process.versions.node!=="22.23.3")process.exit(1);console.log("Windows Node:",process.versions.node)')
    Invoke-ChengxinCommand $node @('integrations/dsh/check.mjs')
    Invoke-ChengxinCommand $node @('integrations/dsh/tests/runtime-smoke.mjs')
    Invoke-ChengxinPython (Join-Path $PSScriptRoot 'windows\sidecar-check.py')
    Invoke-ChengxinCommand $node @('scripts/windows/gui-check.mjs')
}

function New-ChengxinShortcut {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    foreach ($entry in @(@{Name='Chengxin'; Extra=''}, @{Name='Chengxin Live'; Extra=' -Live'})) {
        $shortcut = $shell.CreateShortcut((Join-Path $desktop ($entry.Name + '.lnk')))
        $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'windows.ps1') + '" -Mode Start' + $entry.Extra
        $shortcut.WorkingDirectory = $projectRoot
        $shortcut.Description = 'Chengxin native Windows desktop'
        $shortcut.WindowStyle = 7
        $shortcut.Save()
    }
}

try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or [Environment]::OSVersion.Version.Build -lt 19044) { throw 'Windows 10 19044+ or Windows 11 is required.' }
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This installation targets Windows x64. Use 64-bit PowerShell on an x64 PC.' }
    if ($Live -and $Mode -ne 'Start') { throw '-Live is only valid with -Mode Start.' }
    $OutputEncoding = New-Object Text.UTF8Encoding($false)
    [Console]::OutputEncoding = $OutputEncoding
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Push-Location -LiteralPath $projectRoot
    try {
        if ($Mode -eq 'Setup') {
            New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
            $lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'windows\runtime-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
            Install-ChengxinRuntime 'python' $lock.python
            Install-ChengxinRuntime 'node' $lock.node
            Install-ChengxinRuntime 'isolation' $lock.isolation
        }
        if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $node -PathType Leaf)) { throw 'Run .\scripts\windows.ps1 -Mode Setup first.' }
        $env:PATH = (Split-Path -Parent $node) + [IO.Path]::PathSeparator + $previousPath
        $env:MEDAGENT_PYTHON = $python
        $env:MEDAGENT_NPM_CLI = $npmCli
        Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
        if ($Mode -eq 'Setup') {
            Invoke-ChengxinCommand $node @($npmCli, 'ci', '--ignore-scripts', '--no-audit', '--no-fund')
            Invoke-ChengxinCommand $node @('node_modules/electron/install.js')
            Test-ChengxinInstallation
            if (-not $NoShortcut) { New-ChengxinShortcut }
            Write-Host 'Native Windows installation checks passed. Start with -Mode Start; add -Live to explicitly load .env.'
        } elseif ($Mode -eq 'Check') {
            Test-ChengxinInstallation
        } elseif ($Mode -eq 'Build') {
            Invoke-ChengxinCommand $node @('scripts/build-desktop.mjs')
        } elseif ($Mode -eq 'Test') {
            & (Join-Path $projectRoot 'tests\windows-installer.ps1')
            Invoke-ChengxinPython (Join-Path $projectRoot 'tests\test_windows_native.py')
            Invoke-ChengxinCommand $node @('scripts/windows/isolation-check.mjs')
        } elseif ($Live) {
            Invoke-ChengxinPython (Join-Path $PSScriptRoot 'start_live_desktop.py')
        } else {
            Invoke-ChengxinCommand (Join-Path $projectRoot 'node_modules\electron\dist\electron.exe') @($projectRoot)
        }
    } finally { Pop-Location }
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    $env:PATH = $previousPath
    $env:MEDAGENT_PYTHON = $previousPython
    $env:MEDAGENT_NPM_CLI = $previousNpm
    $env:ELECTRON_RUN_AS_NODE = $previousElectronMode
    [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
    [Console]::OutputEncoding = $previousEncoding
    $OutputEncoding = $previousPipelineEncoding
}
