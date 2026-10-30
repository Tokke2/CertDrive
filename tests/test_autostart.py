import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import (
    AutostartManager,
    ConfigManager,
    _create_windows_startup_shortcut,
    _windows_startup_shortcut_path,
)


class StartupDefaultsTests(unittest.TestCase):
    def test_autostart_and_minimized_start_are_enabled_by_default(self):
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.start_with_os"])
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.start_minimized"])


class WindowsShortcutIconTests(unittest.TestCase):
    def test_startup_shortcut_pins_intygsdrive_icon(self):
        with tempfile.TemporaryDirectory() as roaming:
            with patch.dict(os.environ, {"APPDATA": roaming}):
                shortcut = _windows_startup_shortcut_path()
                def create_shortcut(command, **_kwargs):
                    shortcut.parent.mkdir(parents=True, exist_ok=True)
                    shortcut.touch()
                    self.assertIn("IconLocation", command[-1])
                    self.assertIn(r"C:\Program\IntygsDrive\Icon\icon.ico,0", command[-1])

                with patch("core.config.subprocess.run", side_effect=create_shortcut):
                    self.assertTrue(_create_windows_startup_shortcut(
                        target=r"C:\Python\pythonw.exe",
                        arguments=r"C:\Program\IntygsDrive\main.pyw",
                        working_directory=r"C:\Program\IntygsDrive",
                        icon_path=r"C:\Program\IntygsDrive\Icon\icon.ico",
                    ))
                self.assertTrue(shortcut.is_file())


class LinuxAutostartTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux-specific XDG test")
    def test_xdg_entry_uses_app_command_and_shared_icon(self):
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as config_home:
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home}):
                manager = AutostartManager(app_path=repo_root)
                self.assertTrue(manager.enable())

                desktop = Path(config_home) / "autostart" / "intygsgenerator.desktop"
                content = desktop.read_text(encoding="utf-8")
                self.assertIn("[Desktop Entry]", content)
                self.assertIn(f"Icon={repo_root / 'Icon' / 'icon.svg'}", content)
                self.assertIn("Terminal=false", content)
                self.assertTrue(desktop.stat().st_mode & 0o100)
                self.assertTrue(manager.is_enabled())

                self.assertTrue(manager.disable())
                self.assertFalse(desktop.exists())


if __name__ == "__main__":
    unittest.main()
