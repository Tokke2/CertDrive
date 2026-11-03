import csv
import importlib.util
import json
import logging
import sys
import tempfile
import unittest
from datetime import datetime
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

    def test_exception_details_extracts_type_and_full_traceback(self):
        try:
            raise ValueError("traceback detail marker")
        except ValueError:
            record = logging.LogRecord(
                "test.errors", logging.ERROR, __file__, 10, "failed", (), sys.exc_info()
            )
        exception_type, exception = LOGGING_CORE.exception_details(record)
        self.assertEqual(exception_type, "ValueError")
        self.assertIn("ValueError: traceback detail marker", exception)

    def test_exception_traceback_is_capped_for_queue_safety(self):
        record = logging.LogRecord(
            "test.errors", logging.ERROR, __file__, 10, "failed", (), None
        )
        record.exc_text = "x" * 40_000
        _exception_type, exception = LOGGING_CORE.exception_details(record)
        self.assertLess(len(exception), 32_100)
        self.assertIn("traceback truncated", exception)

    def test_exception_is_preserved_in_structured_and_text_exports(self):
        entry = LOGGING_CORE.LogEntry(
            timestamp=datetime(2026, 11, 3, 12, 0, 0),
            level="ERROR",
            category="Updater",
            message="Update failed",
            exception_type="ValueError",
            exception="ValueError: traceback detail marker\n  at updater.py:42",
            module_name="updater",
            filename="updater.py",
            process_id=123,
        )
        data = entry.to_dict()
        self.assertEqual(data["exception_type"], "ValueError")
        self.assertIn("traceback detail marker", data["exception"])
        self.assertEqual(data["module"], "updater")
        self.assertEqual(data["process"], 123)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            text_path = root / "logs.txt"
            json_path = root / "logs.json"
            csv_path = root / "logs.csv"
            html_path = root / "logs.html"
            markdown_path = root / "logs.md"
            LOGGING_CORE.export_text(text_path, [entry], "test", None)
            LOGGING_CORE.export_json(json_path, [entry], "test", None)
            LOGGING_CORE.export_csv(csv_path, [entry])
            LOGGING_CORE.export_html(html_path, [entry], "test", None)
            LOGGING_CORE.export_markdown(markdown_path, [entry], "test", None)
            self.assertIn("traceback detail marker", text_path.read_text(encoding="utf-8"))
            self.assertIn("traceback detail marker", json_path.read_text(encoding="utf-8"))
            self.assertIn("traceback detail marker", csv_path.read_text(encoding="utf-8"))
            self.assertIn("traceback detail marker", html_path.read_text(encoding="utf-8"))
            self.assertIn("traceback detail marker", markdown_path.read_text(encoding="utf-8"))
            with csv_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0][-2:], ["exception_type", "exception"])
            self.assertEqual(rows[1][-2], "ValueError")


if __name__ == "__main__":
    unittest.main()
