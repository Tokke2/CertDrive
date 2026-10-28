import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


_MAPPING_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "label_mappings.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_label_mappings", _MAPPING_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MAPPING_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MAPPING_MODULE
_SPEC.loader.exec_module(_MAPPING_MODULE)
load_label_mappings = _MAPPING_MODULE.load_label_mappings
save_label_mappings = _MAPPING_MODULE.save_label_mappings


class LabelMappingStoreTests(unittest.TestCase):
    def test_mapping_round_trip_persists_by_certificate_template(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "certificate_label_mappings.json"
            mappings = {"35kW": "motorcycle.lbx", "Passagerare": "passenger.lbx"}
            save_label_mappings(path, mappings)
            self.assertEqual(load_label_mappings(path), mappings)

    def test_invalid_paths_and_non_lbx_files_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            path.write_text(json.dumps({"templates": {
                "Safe": "good.lbx",
                "Traversal": "../outside.lbx",
                "Wrong type": "label.pdf",
            }}), encoding="utf-8")
            self.assertEqual(load_label_mappings(path), {"Safe": "good.lbx"})

    def test_missing_mapping_file_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_label_mappings(Path(directory) / "missing.json"), {})


if __name__ == "__main__":
    unittest.main()
