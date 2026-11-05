import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from core import python_weekly_task


class WeeklyPythonTaskCleanupTests(unittest.TestCase):
    def test_legacy_script_only_removes_the_task_and_never_updates_packages(self):
        script = (
            Path(__file__).resolve().parents[1] / "core" / "python_weekly_update.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Unregister-ScheduledTask", script)
        self.assertNotIn("pip install", script.casefold())
        self.assertNotIn("pymanager", script.casefold())

    def test_non_windows_is_a_successful_noop(self):
        with (
            patch.object(python_weekly_task.sys, "platform", "linux"),
            patch.object(python_weekly_task.subprocess, "run") as run,
        ):
            ok, message = python_weekly_task.remove_weekly_python_update_task()
        self.assertTrue(ok)
        self.assertIn("No Windows", message)
        run.assert_not_called()

    def test_missing_task_is_not_an_error(self):
        with (
            patch.object(python_weekly_task.sys, "platform", "win32"),
            patch.object(python_weekly_task.shutil, "which", return_value="schtasks.exe"),
            patch.object(
                python_weekly_task.subprocess,
                "run",
                return_value=CompletedProcess([], 1, stdout="not found", stderr=""),
            ) as run,
        ):
            ok, message = python_weekly_task.remove_weekly_python_update_task()
        self.assertTrue(ok)
        self.assertIn("No previous", message)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][1], "/Query")

    def test_existing_weekly_python_task_is_deleted(self):
        results = [
            CompletedProcess([], 0, stdout="existing", stderr=""),
            CompletedProcess([], 0, stdout="deleted", stderr=""),
        ]
        with (
            patch.object(python_weekly_task.sys, "platform", "win32"),
            patch.object(python_weekly_task.shutil, "which", return_value="schtasks.exe"),
            patch.object(python_weekly_task.subprocess, "run", side_effect=results) as run,
        ):
            ok, message = python_weekly_task.remove_weekly_python_update_task()
        self.assertTrue(ok)
        self.assertIn("Removed", message)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["schtasks.exe", "/Delete", "/TN", python_weekly_task.TASK_NAME, "/F"],
        )

    def test_task_removal_failure_is_reported(self):
        results = [
            CompletedProcess([], 0, stdout="existing", stderr=""),
            CompletedProcess([], 1, stdout="", stderr="access denied"),
        ]
        with (
            patch.object(python_weekly_task.sys, "platform", "win32"),
            patch.object(python_weekly_task.shutil, "which", return_value="schtasks.exe"),
            patch.object(python_weekly_task.subprocess, "run", side_effect=results),
        ):
            ok, message = python_weekly_task.remove_weekly_python_update_task()
        self.assertFalse(ok)
        self.assertIn("access denied", message)


if __name__ == "__main__":
    unittest.main()
