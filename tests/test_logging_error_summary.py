import csv
import gzip
import importlib.util
import json
import logging
import sys
import time
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
create_general_file_handler = LOGGING_CORE.create_general_file_handler


class ErrorSummaryLogTests(unittest.TestCase):
    def test_logging_writes_only_general_and_error_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            logger = logging.getLogger(f"two-file-logging-{id(root)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            general = create_general_file_handler(root / "app.log", "test-session")
            errors = create_error_file_handler(root / "errors.log", "test-session")
            logger.addHandler(general)
            logger.addHandler(errors)
            try:
                logger.info("ordinary information marker")
                logger.warning("ordinary warning marker")
                logger.warning(
                    "tagged failure warning",
                    extra={"_intygsdrive_error_event": True},
                )
                logger.error("failure marker")
                logger.handlers[0].flush()
                logger.handlers[1].flush()
            finally:
                logger.removeHandler(general)
                logger.removeHandler(errors)
                general.close()
                errors.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

            self.assertEqual(sorted(path.name for path in root.iterdir()), ["app.log", "errors.log"])
            general_text = (root / "app.log").read_text(encoding="utf-8")
            error_text = (root / "errors.log").read_text(encoding="utf-8")
            self.assertIn("ordinary information marker", general_text)
            self.assertIn("ordinary warning marker", general_text)
            self.assertIn("tagged failure warning", general_text)
            self.assertIn("failure marker", general_text)
            self.assertIn("tagged failure warning", error_text)
            self.assertIn("failure marker", error_text)
            self.assertNotIn("ordinary information marker", error_text)
            self.assertNotIn("ordinary warning marker", error_text)

    def test_general_log_rollover_compresses_the_previous_day_into_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            logger = logging.getLogger(f"daily-archive-{id(root)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            handler = create_general_file_handler(
                root / "app.log", session_id="test-session", archive_dir=archive
            )
            logger.addHandler(handler)
            try:
                logger.info("previous day archive marker")
                handler.rolloverAt = int(time.time()) - 1
                logger.info("new day marker")
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

            archives = list(archive.glob("app.log.*.gz"))
            self.assertEqual(len(archives), 1)
            with gzip.open(archives[0], "rt", encoding="utf-8") as stream:
                self.assertIn("previous day archive marker", stream.read())
            active = (root / "app.log").read_text(encoding="utf-8")
            self.assertIn("new day marker", active)
            self.assertNotIn("previous day archive marker", active)

    def test_active_error_is_persisted_then_marked_resolved_after_same_function_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            logger = logging.getLogger(f"error-ledger-{id(error_file)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            handler = create_error_file_handler(error_file, session_id="test-session")
            logger.addHandler(handler)
            try:
                logger.error("dependency unavailable")
                self.assertEqual(len(handler.active_errors), 1)
            finally:
                logger.removeHandler(handler)
                handler.close()

            # A new process/handler reloads the active state from errors.log.
            resumed = create_error_file_handler(error_file, session_id="test-session")
            self.assertEqual(len(resumed.active_errors), 1)
            logger.addHandler(resumed)
            try:
                logger.info("dependency operation not completed successfully")
                self.assertEqual(len(resumed.active_errors), 1)
                logger.info("dependency operation succeeded")
                self.assertEqual(len(resumed.active_errors), 0)
            finally:
                logger.removeHandler(resumed)
                resumed.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

            contents = error_file.read_text(encoding="utf-8")
            self.assertIn("[ERROR OPEN]", contents)
            self.assertIn("dependency unavailable", contents)
            self.assertIn("[ERROR RESOLVED]", contents)

    def test_failure_event_resolves_on_its_matching_success_event(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            handler = create_error_file_handler(error_file, session_id="test-session")
            logger = logging.getLogger(f"event-error-ledger-{id(error_file)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            logger.addHandler(handler)
            try:
                logger.warning(
                    "[EVENT] certificate.generation_failed",
                    extra={"_intygsdrive_error_event": True},
                )
                self.assertEqual(len(handler.active_errors), 1)
                logger.info(
                    "[EVENT] plugin.loaded",
                    extra={"event": "plugin.loaded"},
                )
                self.assertEqual(len(handler.active_errors), 1)
                logger.info(
                    "[EVENT] certificate.generated",
                    extra={"event": "certificate.generated"},
                )
                self.assertEqual(len(handler.active_errors), 0)
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

            contents = error_file.read_text(encoding="utf-8")
            self.assertIn("certificate.generation_failed", contents)
            self.assertIn("[ERROR RESOLVED]", contents)

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
