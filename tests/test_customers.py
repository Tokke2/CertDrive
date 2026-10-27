import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


# Load the pure storage module directly so its unit tests can run on older
# Python interpreters even though importing the full plugins package requires 3.12+.
_CUSTOMERS_PATH = Path(__file__).resolve().parents[1] / "plugins" / "certificates" / "customers.py"
_SPEC = importlib.util.spec_from_file_location("certdrive_customers", _CUSTOMERS_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_CUSTOMERS_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _CUSTOMERS_MODULE
_SPEC.loader.exec_module(_CUSTOMERS_MODULE)
Customer = _CUSTOMERS_MODULE.Customer
load_customers = _CUSTOMERS_MODULE.load_customers
save_customers = _CUSTOMERS_MODULE.save_customers


class CustomerStoreTests(unittest.TestCase):
    def test_round_trip_is_sorted_and_keeps_all_customer_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config" / "customers.json"
            zed = Customer.create("Zed Service", "1 Main St", "12345", "Uppsala", "0700", "z@example.test")
            anna = Customer.create("Åsa Motor", "2 Parkvägen", "111 22", "Stockholm")

            save_customers(path, [zed, anna])
            loaded = load_customers(path)

            self.assertEqual([customer.name for customer in loaded], ["Zed Service", "Åsa Motor"])
            self.assertEqual(loaded[0].address, "1 Main St")
            self.assertEqual(loaded[0].postal_code, "12345")
            self.assertEqual(loaded[0].city, "Uppsala")
            self.assertEqual(loaded[0].phone, "0700")
            self.assertEqual(loaded[0].email, "z@example.test")
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_load_missing_file_and_legacy_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "customers.json"
            self.assertEqual(load_customers(path), [])
            path.write_text(json.dumps([{"name": "Legacy customer"}]), encoding="utf-8")
            loaded = load_customers(path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].name, "Legacy customer")
            self.assertTrue(loaded[0].customer_id)

    def test_label_values_expose_common_swedish_and_english_object_names(self):
        customer = Customer.create("Nord AB", "Hamngatan 4", "111 20", "Stockholm")
        values = customer.label_values()
        self.assertEqual(values["KUNDNAMN"], "Nord AB")
        self.assertEqual(values["ADRESS"], "Hamngatan 4")
        self.assertEqual(values["POSTNR"], "111 20")
        self.assertEqual(values["CITY"], "Stockholm")

    def test_customer_name_is_required_and_fields_are_trimmed(self):
        with self.assertRaises(ValueError):
            Customer.create("   ")
        customer = Customer.create("  Nord AB  ", "  Hamngatan 4  ")
        self.assertEqual(customer.name, "Nord AB")
        self.assertEqual(customer.address, "Hamngatan 4")


if __name__ == "__main__":
    unittest.main()
