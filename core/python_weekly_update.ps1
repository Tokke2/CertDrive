$ErrorActionPreference = 'Stop'
$AppRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$MainScript = (Join-Path $AppRoot 'main.pyw')
$Requirements = (Join-Path $AppRoot 'requirements.txt')
$LogDirectory = Join-Path $AppRoot 'logs'
$LogPath = Join-Path $LogDirectory 'python-maintenance.log'
$ReadyMarker = Join-Path $AppRoot 'js\python-runtime-ready.txt'
$TaskName = 'IntygsDrive Python Weekly Update'

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
function Write-UpdateLog([string] $Message) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$stamp $Message"
}

Write-UpdateLog "Weekly Python maintenance started."
$Manager = Get-Command 'pymanager.exe' -ErrorAction SilentlyContinue
if (-not $Manager) {
    $Manager = Get-Command 'pymanager' -ErrorAction SilentlyContinue
}
if (-not $Manager) {
    Write-UpdateLog 'Skipped: Python Install Manager (pymanager) is not installed or not on PATH.'
    exit 2
}
if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
    Write-UpdateLog 'Failed: requirements.txt was not found.'
    exit 3
}

# Do not replace interpreter/package files while the application is using them.
# The task waits for an explicit process exit (including exit from the system tray).
$Deadline = (Get-Date).AddDays(6)
while ((Get-Date) -lt $Deadline) {
    $RunningApp = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($MainScript, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Select-Object -First 1
    if (-not $RunningApp) { break }
    Start-Sleep -Seconds 30
}
if ($RunningApp) {
    Write-UpdateLog 'Deferred: IntygsDrive did not exit within six days; retrying next Monday.'
    exit 4
}

# Ensure the latest stable managed runtime is available, then update only the
# app's declared direct dependencies in that default runtime.
$env:PYTHON_MANAGER_CONFIRM = 'false'
$InstallOutput = & $Manager.Source install --update default 2>&1
$InstallExitCode = $LASTEXITCODE
foreach ($Line in $InstallOutput) { Write-UpdateLog ([string]$Line) }
if ($InstallExitCode -ne 0) {
    Write-UpdateLog "Failed: Python runtime update returned exit code $InstallExitCode."
    exit $InstallExitCode
}

$PipOutput = & $Manager.Source exec -V:default -m pip install --upgrade --disable-pip-version-check -r $Requirements 2>&1
$PipExitCode = $LASTEXITCODE
foreach ($Line in $PipOutput) { Write-UpdateLog ([string]$Line) }
if ($PipExitCode -ne 0) {
    Write-UpdateLog "Failed: app dependency update returned exit code $PipExitCode."
    exit $PipExitCode
}

$VersionOutput = & $Manager.Source exec -V:default -c 'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>&1
$VersionExitCode = $LASTEXITCODE
if ($VersionExitCode -ne 0) {
    foreach ($Line in $VersionOutput) { Write-UpdateLog ([string]$Line) }
    Write-UpdateLog "Failed: could not verify the updated Python runtime (exit code $VersionExitCode)."
    exit $VersionExitCode
}
$Version = [string]($VersionOutput | Select-Object -Last 1)
Set-Content -LiteralPath $ReadyMarker -Encoding ASCII -Value $Version
Write-UpdateLog "Completed: Python $Version and declared app packages updated successfully."
exit 0
