"""Register the per-user Monday 08:00 Python maintenance task on Windows."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

TASK_NAME = "IntygsDrive Python Weekly Update"


def _ps_quote(value: str) -> str:
    """Quote a literal for a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def ensure_weekly_python_update_task(root: Path | str) -> tuple[bool, str]:
    """Ensure a current-user Task Scheduler job runs every Monday at 08:00.

    Returns (success, message). It does not run Python or pip during app startup;
    it only registers or checks the weekly scheduled task.
    """
    if sys.platform != "win32":
        return False, "The weekly Python task is supported on Windows only."
    app_root = Path(root).resolve()
    updater_script = app_root / "core" / "python_weekly_update.ps1"
    if not updater_script.is_file():
        return False, "The weekly Python update script was not found."

    task_scheduler = shutil.which("schtasks.exe") or shutil.which("schtasks")
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not task_scheduler or not powershell:
        return False, "Windows Task Scheduler or Windows PowerShell was not found."

    query = subprocess.run(
        [task_scheduler, "/Query", "/TN", TASK_NAME],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if query.returncode == 0:
        return True, "The weekly Python update task is already registered."

    task_name_literal = _ps_quote(TASK_NAME)
    script_literal = _ps_quote(str(updater_script))
    script = f"""
$taskName = {task_name_literal}
$updateScript = {script_literal}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $updateScript + '"')
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At ([datetime]::Today.AddHours(8))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Days 6)
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Updates Python and IntygsDrive Python packages weekly on Monday at 08:00.' -Force | Out-Null
""".strip()
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not register the weekly task: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()[-500:]
        return False, f"Could not register the weekly task: {details or result.returncode}"
    return True, "Registered Python and package maintenance for Monday 08:00."
