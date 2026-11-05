import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from core import python_weekly_task


class WeeklyPythonTaskTests(unittest.TestCase):
    def test_powerShell_quote_escapes_apostrophes(self):
        self.assertEqual(python_weekly_task._ps_quote("D:\\Rick's App"), "'D:\\Rick''s App'")

    def test_non_windows_does_not_register_a_task(self):
        with patch.object(python_weekly_task.sys, "platform", "linux"):
            ok, message = python_weekly_task.ensure_weekly_python_update_task(".")
        self.assertFalse(ok)
        self.assertIn("Windows", message)

    def test_existing_task_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "core" / "python_weekly_update.ps1").write_text("# scheduled", encoding="utf-8")
            with (
                patch.object(python_weekly_task.sys, "platform", "win32"),
                patch.object(python_weekly_task.shutil, "which", side_effect=lambda name: name),
                patch.object(
                    python_weekly_task.subprocess,
                    "run",
                    return_value=CompletedProcess([], 0, stdout="existing", stderr=""),
                ) as run,
            ):
                ok, message = python_weekly_task.ensure_weekly_python_update_task(root)
        self.assertTrue(ok)
        self.assertIn("already registered", message)
        run.assert_called_once()

    def test_new_task_registers_weekly_monday_0800_with_start_when_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "core").mkdir()
            (root / "core" / "python_weekly_update.ps1").write_text("# scheduled", encoding="utf-8")
            results = [
                CompletedProcess([], 1, stdout="not found", stderr=""),
                CompletedProcess([], 0, stdout="", stderr=""),
            ]
            with (
                patch.object(python_weekly_task.sys, "platform", "win32"),
                patch.object(python_weekly_task.shutil, "which", side_effect=lambda name: name),
                patch.object(python_weekly_task.subprocess, "run", side_effect=results) as run,
            ):
                ok, message = python_weekly_task.ensure_weekly_python_update_task(root)
        self.assertTrue(ok)
        self.assertIn("Monday 08:00", message)
        script = run.call_args.args[0][-1]
        self.assertIn("-DaysOfWeek Monday", script)
        self.assertIn("AddHours(8)", script)
        self.assertIn("-StartWhenAvailable", script)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
