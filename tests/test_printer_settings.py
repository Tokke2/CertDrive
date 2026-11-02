import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "printer_settings.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_printer_settings", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class PrinterSettingsTests(unittest.TestCase):
    def test_printer_selection_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "label_printer.json"
            _MODULE.save_printer_name(path, "Brother QL-500")
            self.assertEqual(_MODULE.load_printer_name(path), "Brother QL-500")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_missing_or_invalid_settings_use_system_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.json"
            self.assertEqual(_MODULE.load_printer_name(path), "")
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(_MODULE.load_printer_name(path), "")

    def test_installed_printers_are_sorted_and_deduplicated(self):
        fake = types.SimpleNamespace(
            PRINTER_ENUM_LOCAL=2,
            PRINTER_ENUM_CONNECTIONS=4,
            EnumPrinters=lambda *_args: [
                (None, "Zebra Label", None),
                {"pPrinterName": "Brother QL-500"},
                (None, "zebra label", None),
            ],
        )
        with patch.object(_MODULE.sys, "platform", "win32"), patch.dict(sys.modules, {"win32print": fake}):
            self.assertEqual(_MODULE.installed_printers(), ["Brother QL-500", "Zebra Label"])

    def test_non_windows_has_no_bpac_printer_discovery(self):
        with patch.object(_MODULE.sys, "platform", "linux"):
            self.assertEqual(_MODULE.installed_printers(), [])
            self.assertEqual(_MODULE.default_printer(), "")


if __name__ == "__main__":
    unittest.main()
