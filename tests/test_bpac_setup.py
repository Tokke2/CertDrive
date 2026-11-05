import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


_SETUP_PATH = (
    Path(__file__).resolve().parents[1]
    / "plugins"
    / "certificates"
    / "bpac_setup.py"
)
_SPEC = importlib.util.spec_from_file_location("_certdrive_bpac_setup_test", _SETUP_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Could not load b-PAC setup module for tests")
_SETUP = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SETUP
_SPEC.loader.exec_module(_SETUP)


class BpacSetupTests(unittest.TestCase):
    def test_download_page_is_brothers_official_https_site(self):
        parsed = urlparse(_SETUP.BPAC_DOWNLOAD_URL)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "support.brother.com")

    def test_valid_brother_signed_64_bit_bpac_msi_is_accepted(self):
        accepted, message = _SETUP.validate_bpac_signature_report(
            {
                "Status": "Valid",
                "Subject": "CN=Brother Industries, Ltd., O=Brother Industries, Ltd.",
                "Product": "Brother b-PAC Client Component",
                "ProductVersion": "3.4.017",
                "Template": "x64;1033",
            },
            expected_bits=64,
        )
        self.assertTrue(accepted)
        self.assertIn("64-bitars", message)

    def test_valid_32_bit_msi_is_accepted_for_32_bit_app(self):
        accepted, _message = _SETUP.validate_bpac_signature_report(
            {
                "Status": "Valid",
                "Subject": "O=Brother Industries, Ltd.",
                "Product": "b-PAC Client Component",
                "ProductVersion": "3.4.017",
                "Template": "Intel;1033",
            },
            expected_bits=32,
        )
        self.assertTrue(accepted)

    def test_untrusted_publisher_wrong_package_and_wrong_bitness_are_rejected(self):
        base = {
            "Status": "Valid",
            "Subject": "O=Brother Industries, Ltd.",
            "Product": "b-PAC Client Component",
            "ProductVersion": "3.4.017",
            "Template": "x64;1033",
        }
        self.assertFalse(
            _SETUP.validate_bpac_signature_report(
                {**base, "Status": "NotSigned"}, expected_bits=64
            )[0]
        )
        self.assertFalse(
            _SETUP.validate_bpac_signature_report(
                {**base, "Subject": "O=Unknown Publisher"}, expected_bits=64
            )[0]
        )
        self.assertFalse(
            _SETUP.validate_bpac_signature_report(
                {**base, "Product": "Brother Printer Driver"}, expected_bits=64
            )[0]
        )
        self.assertFalse(
            _SETUP.validate_bpac_signature_report(base, expected_bits=32)[0]
        )

    def test_installed_version_comparison_handles_numeric_components(self):
        self.assertEqual(_SETUP.compare_versions("3.4.016", "3.4.017"), -1)
        self.assertEqual(_SETUP.compare_versions("3.4.017", "3.4.017"), 0)
        self.assertEqual(_SETUP.compare_versions("3.4.018", "3.4.017"), 1)
        self.assertEqual(_SETUP.compare_versions("3.4.17.0", "3.4.017"), 0)
        self.assertIsNone(_SETUP.compare_versions("unknown", "3.4.017"))

    def test_old_or_unverifiable_msi_version_is_rejected(self):
        report = {
            "Status": "Valid",
            "Subject": "O=Brother Industries, Ltd.",
            "Product": "Brother b-PAC Client Component",
            "Template": "x64;1033",
        }
        old_ok, old_message = _SETUP.validate_bpac_signature_report(
            {**report, "ProductVersion": "3.4.016"}, expected_bits=64
        )
        unknown_ok, unknown_message = _SETUP.validate_bpac_signature_report(
            report, expected_bits=64
        )
        self.assertFalse(old_ok)
        self.assertIn("äldre", old_message)
        self.assertFalse(unknown_ok)
        self.assertIn("versionen kunde inte verifieras", unknown_message)

    def test_installed_bpac_version_is_read_from_both_registry_views(self):
        winreg = types.ModuleType("winreg")
        winreg.HKEY_LOCAL_MACHINE = "HKLM"
        winreg.HKEY_CURRENT_USER = "HKCU"
        winreg.KEY_READ = 1
        winreg.KEY_WOW64_64KEY = 2
        winreg.KEY_WOW64_32KEY = 4
        uninstall_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
        records = {
            ("HKLM", 2): {"DisplayName": "Brother b-PAC Client Component", "DisplayVersion": "3.4.016"},
            ("HKCU", 4): {"DisplayName": "Unrelated app", "DisplayVersion": "9.9.9"},
        }

        def open_key(root, path, *_args):
            if root in {"HKLM", "HKCU"}:
                if path != uninstall_path:
                    raise OSError("key not found")
                return ("parent", root, _args[-1] & 6)
            return ("entry", root[1], path, root[2])

        def enum_key(parent, index):
            _kind, root, view = parent
            if index == 0 and (root, view) in records:
                return "product"
            raise OSError("no more keys")

        def query_value(key, name):
            _kind, root, _subkey, view = key
            values = records[(root, view)]
            if name not in values:
                raise OSError("value not found")
            return values[name], 1

        winreg.OpenKey = open_key
        winreg.EnumKey = enum_key
        winreg.QueryValueEx = query_value
        winreg.CloseKey = lambda _key: None
        with patch.object(_SETUP.sys, "platform", "win32"), patch.dict(
            sys.modules, {"winreg": winreg}
        ):
            self.assertEqual(_SETUP.installed_bpac_version(), "3.4.016")

    def test_restart_install_launches_detached_powershell_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "app root"
            helper = root / "core" / "bpac_install_helper.ps1"
            helper.parent.mkdir(parents=True)
            helper.touch()
            main_script = root / "main.pyw"
            main_script.touch()
            installer = Path(directory) / "Brother b-PAC.msi"
            installer.touch()
            windows = Path(directory) / "Windows"
            powershell = windows / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            powershell.parent.mkdir(parents=True)
            powershell.touch()
            with (
                patch.object(_SETUP.sys, "platform", "win32"),
                patch.dict("os.environ", {"WINDIR": str(windows)}),
                patch.object(_SETUP.subprocess, "Popen") as launch,
            ):
                started, message = _SETUP.launch_bpac_msi_as_admin(
                    installer, app_root=root, parent_pid=12345
                )

        self.assertTrue(started, message)
        command = launch.call_args.args[0]
        self.assertIn(str(helper), command)
        self.assertIn(str(installer), command)
        self.assertEqual(command[-1], "12345")
        self.assertIn("öppnas igen", message)
        self.assertTrue(launch.call_args.kwargs["close_fds"])

    def test_post_install_helper_requires_uac_and_restarts_without_silent_install(self):
        helper = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "bpac_install_helper.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("-Verb RunAs -Wait -PassThru", helper)
        self.assertIn("/i \"", helper)
        self.assertIn("Start-Process -FilePath $PythonPath", helper)
        self.assertNotIn("/qn", helper.casefold())
        self.assertIn("UAC cancellation", helper)

    def test_installer_operations_are_rejected_off_windows(self):
        with patch.object(_SETUP.sys, "platform", "linux"):
            self.assertFalse(_SETUP.verify_bpac_msi("client.msi")[0])
            self.assertFalse(_SETUP.launch_bpac_msi_as_admin("client.msi")[0])


if __name__ == "__main__":
    unittest.main()
