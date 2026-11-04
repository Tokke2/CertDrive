import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_PIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "counter_security.py"
_SPEC = importlib.util.spec_from_file_location("counter_security_test", _PIN_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_PIN_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _PIN_MODULE
_SPEC.loader.exec_module(_PIN_MODULE)
CounterPinStore = _PIN_MODULE.CounterPinStore


class CounterPinStoreTests(unittest.TestCase):
    def test_pin_is_salted_hashed_and_verifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "counter-pin.json"
            store = CounterPinStore(path)
            store.set_pin("482916")

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("482916", path.read_text(encoding="utf-8"))
            self.assertNotEqual(saved["salt"], saved["digest"])
            self.assertTrue(store.verify("482916"))
            self.assertFalse(store.verify("482917"))

    def test_pin_requires_four_to_twelve_ascii_digits(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = CounterPinStore(Path(temporary) / "counter-pin.json")
            for invalid in ("", "123", "1234567890123", "12ab", "１２３４"):
                with self.subTest(pin=invalid), self.assertRaises(ValueError):
                    store.set_pin(invalid)

    def test_corrupt_configured_pin_file_does_not_allow_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "counter-pin.json"
            path.write_text("not json", encoding="utf-8")
            store = CounterPinStore(path)
            self.assertTrue(store.configured)
            self.assertFalse(store.verify("1234"))


if __name__ == "__main__":
    unittest.main()
