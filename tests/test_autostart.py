import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import AutostartManager


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
