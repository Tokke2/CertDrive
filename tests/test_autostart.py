import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import (
    AutostartManager,
    ConfigManager,
    _build_vbs_launcher,
    _create_windows_startup_shortcut,
    _windows_startup_shortcut_path,
    _windows_startup_vbs_path,
)
_TRAY_MODELS_PATH = Path(__file__).resolve().parents[1] / "plugins" / "system_tray" / "models.py"
_TRAY_MODELS_SPEC = importlib.util.spec_from_file_location("_intygsdrive_tray_models", _TRAY_MODELS_PATH)
assert _TRAY_MODELS_SPEC is not None and _TRAY_MODELS_SPEC.loader is not None
_TRAY_MODELS = importlib.util.module_from_spec(_TRAY_MODELS_SPEC)
sys.modules[_TRAY_MODELS_SPEC.name] = _TRAY_MODELS
_TRAY_MODELS_SPEC.loader.exec_module(_TRAY_MODELS)
TrayConfigHelper = _TRAY_MODELS.TrayConfigHelper


class StartupDefaultsTests(unittest.TestCase):
    def test_autostart_and_minimized_start_are_enabled_by_default(self):
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.start_with_os"])
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.start_minimized"])
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.close_to_tray"])
        self.assertTrue(ConfigManager.DEFAULT_SETTINGS["app.minimize_to_tray"])


class TrayConfigCompatibilityTests(unittest.TestCase):
    def test_settings_aliases_load_and_stay_in_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            config = ConfigManager(Path(directory) / "settings.json")
            try:
                config.set("app.tray.minimize_to_tray", False)
                config.set("app.close_to_tray", False)
                tray = TrayConfigHelper(config)
                self.assertFalse(tray.minimize_to_tray)
                self.assertFalse(tray.close_to_tray)

                tray.minimize_to_tray = True
                tray.close_to_tray = True
                self.assertTrue(config.get("tray.minimize_to_tray"))
                self.assertTrue(config.get("app.tray.minimize_to_tray"))
                self.assertTrue(config.get("app.minimize_to_tray"))
                self.assertTrue(config.get("tray.close_to_tray"))
                self.assertTrue(config.get("app.close_to_tray"))
                self.assertTrue(config.get("app.tray.close_to_tray"))
            finally:
                config.close()


class WindowsShortcutIconTests(unittest.TestCase):
    def test_startup_shortcut_pins_intygsdrive_icon(self):
        with tempfile.TemporaryDirectory() as roaming:
            with patch.dict(os.environ, {"APPDATA": roaming}):
                shortcut = _windows_startup_shortcut_path()
                def create_shortcut(command, **_kwargs):
                    shortcut.parent.mkdir(parents=True, exist_ok=True)
                    shortcut.touch()
                    self.assertIn("IconLocation", command[-1])
                    self.assertIn("wscript.exe", command[-1])
                    self.assertIn(r"C:\Program\IntygsDrive\Icon\icon.ico,0", command[-1])

                with patch("core.config.subprocess.run", side_effect=create_shortcut):
                    self.assertTrue(_create_windows_startup_shortcut(
                        target=r"C:\Windows\System32\wscript.exe",
                        arguments=r"//B //NoLogo C:\Users\Test\AppData\Roaming\IntygsDrive\autostart.vbs",
                        working_directory=r"C:\Program\IntygsDrive",
                        icon_path=r"C:\Program\IntygsDrive\Icon\icon.ico",
                    ))
                self.assertTrue(shortcut.is_file())


class VbsAutostartTests(unittest.TestCase):
    def test_launcher_runs_python_exe_hidden_without_pythonw(self):
        script = _build_vbs_launcher(
            r"C:\Python 3.12\python.exe",
            [r"C:\Program Files\IntygsDrive\main.pyw"],
            r"C:\Program Files\IntygsDrive",
        )
        self.assertIn("\r\n", script)
        self.assertNotIn("pythonw.exe", script.casefold())
        self.assertIn("\r\n", script)
        self.assertIn("\r\n", script)

    def test_vbs_script_is_stored_outside_startup_so_it_launches_only_once(self):
        with tempfile.TemporaryDirectory() as roaming:
            with patch.dict(os.environ, {"APPDATA": roaming}):
                self.assertNotEqual(
                    _windows_startup_vbs_path().parent,
                    _windows_startup_shortcut_path().parent,
                )


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
                self.assertIn(f"Icon={repo_root / 'Icon' / 'icon.png'}", content)
                self.assertIn("Terminal=false", content)
                self.assertTrue(desktop.stat().st_mode & 0o100)
                self.assertTrue(manager.is_enabled())

                self.assertTrue(manager.disable())
                self.assertFalse(desktop.exists())


if __name__ == "__main__":
    unittest.main()
