import importlib.util
import sys
import unittest
from pathlib import Path


_TRACER_PATH = Path(__file__).resolve().parents[1] / "plugins" / "logging_plugin" / "tracer.py"
_SPEC = importlib.util.spec_from_file_location("_certdrive_logging_tracer_test", _TRACER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("Could not load logging tracer module for tests")
_TRACER_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _TRACER_MODULE
_SPEC.loader.exec_module(_TRACER_MODULE)
FileSystemTracer = _TRACER_MODULE.FileSystemTracer


class FileSystemTracerNoiseTests(unittest.TestCase):
    def setUp(self):
        self.tracer = FileSystemTracer(lambda _operation: None)

    def test_update_staging_paths_are_filtered_for_windows_and_posix(self):
        self.assertTrue(
            self.tracer._is_noise(
                r"D:\Program Files\IntygsDrive\js\.update-staging\abc\files\main.pyw"
            )
        )
        self.assertTrue(
            self.tracer._is_noise(
                "/opt/intygsdrive/js/.update-staging/abc/files/main.pyw"
            )
        )

    def test_normal_application_paths_are_not_filtered(self):
        self.assertFalse(self.tracer._is_noise("D:/IntygsDrive/core/updater.py"))


if __name__ == "__main__":
    unittest.main()
