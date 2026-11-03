import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.support_bundle import write_support_bundle


class SupportBundleTests(unittest.TestCase):
    def test_export_contains_allowlisted_diagnostics_but_no_sensitive_values(self):
        diagnostics = {
            "app": {"name": "IntygsDrive", "version": "0.0.1", "current_commit": "a" * 40},
            "plugins": {"loaded": ["certificates"], "failed": {"bad_plugin": "C:/Users/Alice/private error"}},
            "label_printer": {
                "bpac_available": True,
                "installed_printer_count": 1,
                "printer_name": "Alice's Brother",
                "automatic_print_ready": True,
            },
            "customer_data": {"Alice Example": "customer record"},
            "paths": {"app": "C:/Users/Alice/Documents/CertDrive"},
            "recent_logs": ["sensitive certificate value"],
        }
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "support.zip"
            write_support_bundle(destination, diagnostics)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), {"diagnostics.json", "README.txt"})
                payload = json.loads(archive.read("diagnostics.json"))

        text = json.dumps(payload, ensure_ascii=False)
        self.assertIn("IntygsDrive", text)
        self.assertIn("bad_plugin", text)
        self.assertNotIn("Alice", text)
        self.assertNotIn("customer record", text)
        self.assertNotIn("private error", text)
        self.assertNotIn("sensitive certificate value", text)
        self.assertFalse(payload["privacy"]["raw_logs_included"])
        self.assertFalse(payload["privacy"]["customer_data_included"])


if __name__ == "__main__":
    unittest.main()
