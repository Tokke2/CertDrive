import ast
import contextlib
import json
import logging
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SERVICES_PATH = ROOT / "plugins" / "certificates" / "services.py"


def load_counter_manager():
    tree = ast.parse(SERVICES_PATH.read_text(encoding="utf-8"))
    counter_class = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "CounterManager"
    )
    module = ast.Module(body=[counter_class], type_ignores=[])
    namespace = {
        "Any": Any,
        "Path": Path,
        "threading": threading,
        "logging": logging,
        "json": json,
        "contextlib": contextlib,
        "tempfile": tempfile,
        "_logger": logging.getLogger("counter-manager-test"),
        "FOLDER_CONFIG": "js",
    }
    exec(compile(ast.fix_missing_locations(module), str(SERVICES_PATH), "exec"), namespace)
    return namespace["CounterManager"]


CounterManager = load_counter_manager()


class CounterManagerNextNumberTests(unittest.TestCase):
    def test_set_next_number_means_next_increment_returns_exact_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = CounterManager(config_path=Path(temporary) / "counters.json")
            self.assertEqual(manager.set_next("Testmall", 42, start=1), 42)
            self.assertEqual(manager.get_current("Testmall"), 41)
            self.assertEqual(manager.get_next("Testmall"), 42)

    def test_next_number_respects_template_start_and_rejects_lower_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = CounterManager(config_path=Path(temporary) / "counters.json")
            with self.assertRaises(ValueError):
                manager.set_next("Testmall", 9, start=10)
            self.assertEqual(manager.set_next("Testmall", 10, start=10), 10)
            self.assertEqual(manager.get_next("Testmall", start=10), 10)


if __name__ == "__main__":
    unittest.main()
