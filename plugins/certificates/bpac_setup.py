"""Safe, user-approved setup helpers for Brother's b-PAC client component."""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from pathlib import Path
import struct
from typing import Any


BPAC_DOWNLOAD_URL = (
    "https://support.brother.com/g/s/es/dev/en/bpac/download/"
    "index.html?c=eu_ot&lang=en&navi=offall&comple=on&redirect=on"
)
_MAX_MSI_BYTES = 100 * 1024 * 1024
# Latest stable version listed on Brother's official b-PAC download page when
# this updater was last reviewed. Keep in sync when updating the official MSI.
BPAC_TARGET_VERSION = "3.4.017"


def _version_parts(value: Any) -> tuple[int, ...] | None:
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)", str(value or ""))
    if match is None:
        return None
    try:
        return tuple(int(part) for part in match.group(1).split("."))
    except ValueError:
        return None


def compare_versions(left: Any, right: Any) -> int | None:
    """Compare dotted numeric versions; return None if either is not parseable."""
    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    if left_parts is None or right_parts is None:
        return None
    width = max(len(left_parts), len(right_parts))
    left_parts += (0,) * (width - len(left_parts))
    right_parts += (0,) * (width - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def installed_bpac_version() -> str | None:
    """Read Brother b-PAC's installed version from Windows uninstall records."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    uninstall_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    views = tuple(dict.fromkeys((
        getattr(winreg, "KEY_WOW64_64KEY", 0),
        getattr(winreg, "KEY_WOW64_32KEY", 0),
    )))
    candidates: list[str] = []
    for root in roots:
        for view in views:
            try:
                parent = winreg.OpenKey(
                    root, uninstall_path, 0, winreg.KEY_READ | view
                )
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        subkey = winreg.OpenKey(parent, subkey_name)
                        try:
                            display_name = str(winreg.QueryValueEx(subkey, "DisplayName")[0])
                            version = str(
                                winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                            )
                        finally:
                            winreg.CloseKey(subkey)
                    except OSError:
                        continue
                    if "b-pac" in display_name.casefold() or "bpac" in display_name.casefold():
                        if _version_parts(version) is not None:
                            candidates.append(version.strip())
            finally:
                winreg.CloseKey(parent)
    if not candidates:
        return None
    return max(candidates, key=lambda value: _version_parts(value) or ())


def application_bitness() -> int:
    """Return the bitness of the running Python/app process (32 or 64)."""
    return struct.calcsize("P") * 8


def validate_bpac_signature_report(
    report: dict[str, Any], *, expected_bits: int | None = None
) -> tuple[bool, str]:
    """Validate PowerShell's Authenticode and Windows Installer metadata report."""
    expected_bits = expected_bits or application_bitness()
    status = str(report.get("Status", ""))
    subject = str(report.get("Subject", ""))
    product = str(report.get("Product", ""))
    template = str(report.get("Template", "")).casefold()
    if status.casefold() != "valid" or "brother industries" not in subject.casefold():
        return False, "Installationsfilen saknar en giltig Brother Industries-signatur."
    if "b-pac" not in product.casefold():
        return False, "Den signerade MSI-filen verkar inte vara b-PAC Client Component."
    expected_platform = "x64" if expected_bits == 64 else "intel"
    if expected_platform not in template:
        return False, (
            f"MSI-filen matchar inte appens {expected_bits}-bitarsarkitektur "
            "(Brother kräver samma bitness)."
        )
    product_version = str(report.get("ProductVersion", "")).strip()
    if _version_parts(product_version) is None:
        return False, "MSI-versionen kunde inte verifieras; filen installeras inte."
    if compare_versions(product_version, BPAC_TARGET_VERSION) == -1:
        return False, (
            f"MSI-filen innehåller b-PAC {product_version}, som är äldre än "
            f"den rekommenderade versionen {BPAC_TARGET_VERSION}."
        )
    return True, (
        f"{product} {product_version} — Brother Industries, giltig signatur, "
        f"{expected_bits}-bitars"
    )


def verify_bpac_msi(path: Path | str) -> tuple[bool, str]:
    """Verify an MSI's Authenticode signature is valid and published by Brother."""
    installer = Path(path)
    if sys.platform != "win32":
        return False, "b-PAC kan bara installeras på Windows."
    if installer.suffix.casefold() != ".msi":
        return False, "Välj Brothers b-PAC Client Component-installationsfil (.msi)."
    try:
        size = installer.stat().st_size
    except OSError as exc:
        return False, f"Installationsfilen kunde inte läsas: {exc}"
    if not installer.is_file() or size < 1024 * 1024 or size > _MAX_MSI_BYTES:
        return False, "Filen verkar inte vara en giltig b-PAC-klientinstallation."

    powershell = Path(os.environ.get("WINDIR", r"C:\Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    if not powershell.is_file():
        return False, "Windows PowerShell saknas; utgivarens signatur kan inte verifieras."
    # The selected path is inserted as a PowerShell single-quoted literal.
    # Doubling apostrophes prevents paths from becoming executable syntax.
    quoted_path = str(installer.resolve()).replace("'", "''")
    script = f"""
$p = '{quoted_path}'
$s = Get-AuthenticodeSignature -LiteralPath $p
$subject = if ($s.SignerCertificate) {{ $s.SignerCertificate.Subject }} else {{ '' }}
$wi = New-Object -ComObject WindowsInstaller.Installer
$db = $wi.OpenDatabase($p, 0)
$view = $db.OpenView("SELECT `Value` FROM `Property` WHERE `Property`='ProductName'")
$view.Execute()
$record = $view.Fetch()
$product = if ($record) {{ $record.StringData(1) }} else {{ '' }}
$versionView = $db.OpenView("SELECT `Value` FROM `Property` WHERE `Property`='ProductVersion'")
$versionView.Execute()
$versionRecord = $versionView.Fetch()
$productVersion = if ($versionRecord) {{ $versionRecord.StringData(1) }} else {{ '' }}
$template = $db.SummaryInformation(0).Property(7)
[PSCustomObject]@{{ Status = [string]$s.Status; Subject = [string]$subject; Product = [string]$product; ProductVersion = [string]$productVersion; Template = [string]$template }} | ConvertTo-Json -Compress
""".strip()
    try:
        result = subprocess.run(
            [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Brother-signaturen kunde inte kontrolleras: {exc}"
    if result.returncode != 0:
        return False, "Brother-signaturen kunde inte kontrolleras."
    try:
        report: dict[str, Any] = json.loads(result.stdout.strip())
    except (ValueError, TypeError):
        return False, "Windows returnerade inget giltigt signaturresultat."
    return validate_bpac_signature_report(report)


def launch_bpac_msi_as_admin(
    path: Path | str,
    *,
    app_root: Path | str | None = None,
    parent_pid: int | None = None,
) -> tuple[bool, str]:
    """Run the MSI with UAC and optionally relaunch IntygsDrive after it finishes."""
    if sys.platform != "win32":
        return False, "b-PAC kan bara installeras på Windows."
    installer = Path(path).resolve()
    if installer.suffix.casefold() != ".msi" or not installer.is_file():
        return False, "Installationsfilen saknas eller är inte en MSI-fil."
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    if app_root is not None and parent_pid is not None:
        root = Path(app_root).resolve()
        helper = root / "core" / "bpac_install_helper.ps1"
        main_script = root / "main.pyw"
        powershell = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if not helper.is_file() or not main_script.is_file():
            return False, "Installationshjälparen eller IntygsDrives startfil saknas."
        if not powershell.is_file():
            return False, "Windows PowerShell hittades inte."
        command = [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(helper),
            str(installer),
            sys.executable,
            str(main_script),
            str(root),
            str(int(parent_pid)),
        ]
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        try:
            subprocess.Popen(
                command,
                cwd=str(root),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"Installationshjälparen kunde inte startas: {type(exc).__name__}."
        return True, (
            "Installationshjälparen startades. IntygsDrive stängs nu och öppnas igen "
            "när Windows-installationen är klar."
        )

    # Backward-compatible direct launch for callers that do not request a restart.
    msiexec = windows / "System32" / "msiexec.exe"
    if not msiexec.is_file():
        return False, "Windows Installer (msiexec.exe) hittades inte."
    parameters = f'/i "{installer}" /norestart'
    try:
        shell_execute = ctypes.windll.shell32.ShellExecuteW  # type: ignore[attr-defined]
        shell_execute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell_execute.restype = ctypes.c_void_p
        result = shell_execute(None, "runas", str(msiexec), parameters, None, 1)
    except (AttributeError, OSError, ctypes.ArgumentError) as exc:
        return False, f"Windows-installationen kunde inte startas: {exc}"
    result_code = int(result or 0)
    if result_code <= 32:
        if result_code == 1223:
            return False, "Installationen avbröts vid UAC-frågan."
        return False, f"Windows kunde inte starta installationsprogrammet (kod {result_code})."
    return True, "Windows Installer startades med administratörsgodkännande."
