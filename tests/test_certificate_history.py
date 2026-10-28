import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


_HISTORY_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "history.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_history", _HISTORY_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_HISTORY_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _HISTORY_MODULE
_SPEC.loader.exec_module(_HISTORY_MODULE)
find_duplicate_certificates = _HISTORY_MODULE.find_duplicate_certificates
normalize_registration = _HISTORY_MODULE.normalize_registration
registration_from_values = _HISTORY_MODULE.registration_from_values


class CertificateHistoryDuplicateTests(unittest.TestCase):
    def test_registration_normalization_ignores_case_spaces_and_punctuation(self):
        self.assertEqual(normalize_registration(" abc- 123 "), "ABC123")

    def test_matches_same_template_and_registration_case_insensitively(self):
        records = [
            {"template": "35kW", "reg": "ABC 123", "id": 19},
            {"template": "Passagerare", "reg": "ABC123", "id": 20},
            {"template": "35kW", "reg": "XYZ 999", "id": 21},
        ]
        matches = find_duplicate_certificates(records, "35KW", "abc-123")
        self.assertEqual([record["id"] for record in matches], [19])

    def test_reads_registration_field_aliases_from_dicts_and_records(self):
        records = [
            {"category": "Personbil", "values": {"Registreringsnummer": "DEF456"}},
            SimpleNamespace(template="Personbil", regnr="DEF-456"),
        ]
        self.assertEqual(len(find_duplicate_certificates(records, "Personbil", "DEF 456")), 2)
        self.assertEqual(registration_from_values({"Reg nr": "DEF 456"}), "DEF456")

    def test_empty_registration_never_matches(self):
        self.assertEqual(find_duplicate_certificates(
            [{"template": "35kW", "reg": ""}], "35kW", ""
        ), [])


if __name__ == "__main__":
    unittest.main()
