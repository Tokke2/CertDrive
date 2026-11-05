"""Remove the obsolete weekly Python updater task from Task Scheduler."""
from __future__ import annotations

import shutil
import subprocess
import sys

TASK_NAME = "IntygsDrive Python Weekly Update"


def remove_weekly_python_update_task() -> tuple[bool, str]:
    """Remove a previously installed IntygsDrive Python update task, if any.

    This only queries/deletes the named task. It never runs Python, pip, or the
    task's update script. Non-Windows platforms have no such scheduled task.
    """
    if sys.platform != "win32":
        return True, "No Windows Python update task exists on this platform."

    task_scheduler = shutil.which("schtasks.exe") or shutil.which("schtasks")
    if not task_scheduler:
        return False, "Windows Task Scheduler command was not found."

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        query = subprocess.run(
            [task_scheduler, "/Query", "/TN", TASK_NAME],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            creationflags=creationflags,
        )
        if query.returncode != 0:
            return True, "No previous weekly Python update task is registered."

        removed = subprocess.run(
            [task_scheduler, "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not remove the weekly Python task: {type(exc).__name__}: {exc}"

    if removed.returncode != 0:
        details = (removed.stderr or removed.stdout or "").strip()[-300:]
        return False, f"Could not remove the weekly Python task: {details or removed.returncode}"
    return True, "Removed the obsolete weekly Python update task."
