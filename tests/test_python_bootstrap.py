import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIX_START = ROOT / "Starta IntygsDrive.sh"
WINDOWS_START = ROOT / "Starta IntygsDrive.bat"
MAC_START = ROOT / "Starta IntygsDrive.command"


class PythonBootstrapTests(unittest.TestCase):
    def test_windows_launcher_checks_for_python_before_offering_download(self):
        source = WINDOWS_START.read_text(encoding="utf-8").casefold()
        self.assertLess(source.index("py -3 -c"), source.index("choice /c yn"))
        self.assertIn("python3 -c", source)
        self.assertIn("python -c", source)
        self.assertIn("https://www.python.org/downloads/windows/", source)
        self.assertIn("goto check_python", source)

    def test_unix_launcher_does_not_prompt_when_python_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / "python3"
            call_log = Path(directory) / "launched.txt"
            fake_python.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                "printf '%s\\n' \"$*\" > \"$PYTHON_CALL_LOG\"\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:/usr/bin:/bin"
            environment["PYTHON_CALL_LOG"] = str(call_log)
            result = subprocess.run(
                ["/bin/sh", str(UNIX_START), "--test-argument"],
                input="",
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Python hittades inte", result.stdout)
            self.assertIn("main.pyw --test-argument", call_log.read_text(encoding="utf-8"))

    def test_unix_launcher_offers_official_download_only_when_python_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("python3", "python"):
                fake_python = Path(directory) / name
                fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                fake_python.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:/usr/bin:/bin"
            result = subprocess.run(
                ["/bin/sh", str(UNIX_START)],
                input="n\n",
                capture_output=True,
                text=True,
                env=environment,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Python hittades inte", result.stdout)
            self.assertIn("officiella sidan", result.stdout)
            self.assertNotIn("python.org/downloads", result.stdout)

    def test_macos_entry_uses_shared_unix_startup_script(self):
        source = MAC_START.read_text(encoding="utf-8")
        self.assertIn("Starta IntygsDrive.sh", source)

    def test_old_standalone_python_installer_is_not_included(self):
        self.assertFalse((ROOT / "Installera senaste Python.bat").exists())
        self.assertTrue(UNIX_START.is_file())
        self.assertTrue(WINDOWS_START.is_file())
        self.assertTrue(MAC_START.is_file())


if __name__ == "__main__":
    unittest.main()
