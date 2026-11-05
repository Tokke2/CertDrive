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

    def test_larger_text_scales_column_breakpoints(self):
        count = _MODULE.field_column_count
        self.assertEqual(count(935, font_height=32), 1)
        self.assertEqual(count(936, font_height=32), 2)
        self.assertEqual(count(1943, font_height=32), 2)
        self.assertEqual(count(1944, font_height=32), 3)

    def test_small_text_uses_a_conservative_minimum_breakpoint(self):
        count = _MODULE.field_column_count
        self.assertEqual(count(441, font_height=8), 1)
        self.assertEqual(count(442, font_height=8), 2)

    def test_common_field_names_are_grouped_into_readable_sections(self):
        group = _MODULE.field_group
        self.assertEqual(group("REGNR"), "Fordonsuppgifter")
        self.assertEqual(group("Fordonets årsmodell"), "Fordonsuppgifter")
        self.assertEqual(group("MODEL_YEAR"), "Fordonsuppgifter")
        self.assertEqual(group("HANDELSBETECKNING"), "Fordonsuppgifter")
        self.assertEqual(group("TYPE_APPROVAL", "Typgodkännande"), "Intyg och kontroll")
        self.assertEqual(group("Intygsdatum"), "Intyg och kontroll")
        self.assertEqual(group("Kundkommentar"), "Övriga uppgifter")

    def test_form_sections_use_vehicle_certificate_other_order(self):
        self.assertEqual(
            _MODULE.FIELD_GROUP_ORDER,
            ("Fordonsuppgifter", "Intyg och kontroll", "Övriga uppgifter"),
        )


if __name__ == "__main__":
    unittest.main()
