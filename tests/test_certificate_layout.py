import importlib.util
import sys
import unittest
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "layout.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_certificate_layout", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


class CertificateResponsiveLayoutTests(unittest.TestCase):
    def test_column_breakpoints_use_logical_width_boundaries(self):
        count = _MODULE.field_column_count
        self.assertEqual(count(0), 1)
        self.assertEqual(count(519), 1)
        self.assertEqual(count(520), 2)
        self.assertEqual(count(1079), 2)
        self.assertEqual(count(1080), 3)

    def test_breakpoints_are_monotonic_at_common_dpi_window_sizes(self):
        widths = [480, 640, 800, 1024, 1280, 1600]
        columns = [_MODULE.field_column_count(width) for width in widths]
        self.assertEqual(columns, sorted(columns))
        self.assertTrue(all(1 <= value <= 3 for value in columns))

    def test_negative_width_falls_back_to_one_column(self):
        self.assertEqual(_MODULE.field_column_count(-50), 1)


if __name__ == "__main__":
    unittest.main()
