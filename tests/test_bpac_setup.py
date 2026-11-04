import importlib.util
import sys
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

    def test_installer_operations_are_rejected_off_windows(self):
        with patch.object(_SETUP.sys, "platform", "linux"):
            self.assertFalse(_SETUP.verify_bpac_msi("client.msi")[0])
            self.assertFalse(_SETUP.launch_bpac_msi_as_admin("client.msi")[0])


if __name__ == "__main__":
    unittest.main()
