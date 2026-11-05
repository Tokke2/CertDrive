param(
    [Parameter(Mandatory = $true)][string] $InstallerPath,
    [Parameter(Mandatory = $true)][string] $PythonPath,
    [Parameter(Mandatory = $true)][string] $MainScript,
    [Parameter(Mandatory = $true)][string] $AppRoot,
    [Parameter(Mandatory = $true)][int] $ParentPid
)

$ErrorActionPreference = 'Stop'
$LogPath = Join-Path $AppRoot 'js\bpac-install.log'

function Write-InstallLog([string] $Message) {
    try {
        $directory = Split-Path -Parent $LogPath
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    } catch {
        # Logging failure must not prevent installation or restart.
    }
}

try {
    # Do not replace b-PAC files while IntygsDrive still has the COM component loaded.
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Wait-Process -Id $ParentPid -Timeout 120 -ErrorAction SilentlyContinue
    }
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        throw 'IntygsDrive did not close before the installer timeout.'
    }

    $Msiexec = Join-Path $env:WINDIR 'System32\msiexec.exe'
    $Arguments = '/i "' + $InstallerPath + '" /norestart'
    # /i is deliberately interactive: no silent install and no automatic EULA acceptance.
    $Process = Start-Process -FilePath $Msiexec -ArgumentList $Arguments -Verb RunAs -Wait -PassThru
    if ($Process.ExitCode -in @(0, 1641, 3010)) {
        Write-InstallLog "b-PAC installation completed with Windows Installer code $($Process.ExitCode)."
    } else {
        Write-InstallLog "b-PAC installation ended with Windows Installer code $($Process.ExitCode)."
    }
} catch {
    # This includes a UAC cancellation. Restart the existing app so it is not left closed.
    Write-InstallLog "b-PAC installation was canceled or failed ($($_.Exception.GetType().Name))."
}

try {
    Start-Sleep -Seconds 1
    $MainArgument = '"' + $MainScript + '"'
    Start-Process -FilePath $PythonPath -ArgumentList $MainArgument -WorkingDirectory $AppRoot
    Write-InstallLog 'IntygsDrive restarted after the b-PAC installer finished.'
} catch {
    Write-InstallLog "IntygsDrive restart failed ($($_.Exception.GetType().Name))."
}
