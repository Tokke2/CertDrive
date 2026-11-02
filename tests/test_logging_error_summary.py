import importlib.util
import logging
import sys
import tempfile
import unittest
from pathlib import Path


CORE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "logging_plugin" / "core.py"
SPEC = importlib.util.spec_from_file_location("intygsdrive_logging_core_test", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load logging core for tests")
LOGGING_CORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOGGING_CORE
SPEC.loader.exec_module(LOGGING_CORE)
create_error_file_handler = LOGGING_CORE.create_error_file_handler


class ErrorSummaryLogTests(unittest.TestCase):
    def test_collects_errors_and_tagged_failure_events_but_not_plain_warnings(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            handler = create_error_file_handler(error_file, session_id="test-session")
            root = logging.getLogger()
            logger = logging.getLogger("other.plugin.worker")
            old_level = logger.level
            old_propagate = logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = True
            root.addHandler(handler)
            try:
                logger.warning("warning is not an error")
                logger.warning(
                    "[EVENT] certificate.generation_failed",
                    extra={"_intygsdrive_error_event": True},
                )
                logger.error("certificate conversion failed")
                try:
                    raise RuntimeError("converter traceback marker")
                except RuntimeError:
                    logger.exception("background worker failed")
                handler.flush()
            finally:
                root.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

            contents = error_file.read_text(encoding="utf-8")
            self.assertIn("certificate conversion failed", contents)
            self.assertIn("certificate.generation_failed", contents)
            self.assertIn("background worker failed", contents)
            self.assertIn("RuntimeError: converter traceback marker", contents)
            self.assertIn("other.plugin.worker", contents)
            self.assertNotIn("warning is not an error", contents)


if __name__ == "__main__":
    unittest.main()
