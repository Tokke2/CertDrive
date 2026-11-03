import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


_LABELS_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "labels.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_labels_calibration_test", _LABELS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_LABELS = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LABELS
_SPEC.loader.exec_module(_LABELS)


class _FakeDocument:
    def __init__(self, text_count=3):
        self.text_count = text_count
        self.texts = []
        self.printer = ""
        self.printed = False
        self.ErrorCode = 0

    def Open(self, _path):
        return True

    def SetPrinter(self, name, _show_dialog):
        self.printer = name

    def GetTextCount(self):
        return self.text_count

    def SetText(self, index, text):
        self.texts.append((index, text))
        return True

    def StartPrint(self, _job_name, _options):
        return True

    def PrintOut(self, copies, _options):
        self.printed = copies == 1
        return True

    def EndPrint(self):
        return True

    def Close(self):
        return True


class LabelCalibrationTests(unittest.TestCase):
    def test_calibration_pattern_is_numbered_and_contains_no_input_data(self):
        self.assertEqual(_LABELS.calibration_text(0), "01|0123456789|")
        self.assertEqual(_LABELS.calibration_text(7), "08|0123456789|")

    def test_prints_calibration_pattern_on_selected_printer_without_customer_values(self):
        document = _FakeDocument(text_count=3)
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = lambda: None
        pythoncom.CoUninitialize = lambda: None
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda _progid: document
        win32com = types.ModuleType("win32com")
        win32com.__path__ = []
        win32com.client = client
        modules = {
            "pythoncom": pythoncom,
            "win32com": win32com,
            "win32com.client": client,
        }

        with tempfile.TemporaryDirectory() as directory:
            label_path = Path(directory) / "test.lbx"
            label_path.write_bytes(b"fake lbx")
            with mock.patch.object(_LABELS.sys, "platform", "win32"), mock.patch.dict(
                sys.modules, modules
            ):
                result = _LABELS.print_label(
                    label_path,
                    copies=9,
                    values={"NAME": "Sensitive customer value"},
                    printer_name="Selected Brother printer",
                    calibration=True,
                )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.copies, 1)
        self.assertEqual(document.printer, "Selected Brother printer")
        self.assertTrue(document.printed)
        self.assertEqual(
            document.texts,
            [
                (0, "01|0123456789|"),
                (1, "02|0123456789|"),
                (2, "03|0123456789|"),
            ],
        )
        self.assertNotIn("Sensitive customer value", repr(document.texts))
        self.assertEqual(result.filled, ["text_1", "text_2", "text_3"])

    def test_template_without_editable_text_is_not_sent_to_printer(self):
        document = _FakeDocument(text_count=0)
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = lambda: None
        pythoncom.CoUninitialize = lambda: None
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda _progid: document
        win32com = types.ModuleType("win32com")
        win32com.__path__ = []
        win32com.client = client

        with tempfile.TemporaryDirectory() as directory:
            label_path = Path(directory) / "no-text.lbx"
            label_path.touch()
            with mock.patch.object(_LABELS.sys, "platform", "win32"), mock.patch.dict(
                sys.modules,
                {"pythoncom": pythoncom, "win32com": win32com, "win32com.client": client},
            ):
                with self.assertLogs("Intyg.labels", level="ERROR"):
                    result = _LABELS.print_label(label_path, calibration=True)

        self.assertFalse(result.ok)
        self.assertIn("inga redigerbara textfält", result.error)
        self.assertFalse(document.printed)


if __name__ == "__main__":
    unittest.main()
