import csv
import gzip
import importlib.util
import json
import logging
import os
import sys
import time
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path


CORE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "logging_plugin" / "core.py"
SPEC = importlib.util.spec_from_file_location("intygsdrive_logging_core_test", CORE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load logging core for tests")
LOGGING_CORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = LOGGING_CORE
SPEC.loader.exec_module(LOGGING_CORE)
archive_old_logs = LOGGING_CORE.archive_old_logs
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
                self.assertFalse(handler.rollover_if_due())
                handler.rolloverAt = int(time.time()) - 1
                self.assertTrue(handler.rollover_if_due())
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

    def test_daily_archives_are_pruned_after_the_retention_period(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "archive"
            archive.mkdir()
            expired = archive / "app.log.2024-01-01.gz"
            retained = archive / "app.log.2099-01-01.gz"
            expired.write_bytes(b"old archive")
            retained.write_bytes(b"new archive")
            old_timestamp = time.time() - 30 * 24 * 60 * 60
            os.utime(expired, (old_timestamp, old_timestamp))

            archive_old_logs(temporary, older_than_days=21)

            self.assertFalse(expired.exists())
            self.assertTrue(retained.exists())

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

    def test_explicit_recovery_key_links_failure_and_success_across_functions(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            handler = create_error_file_handler(error_file, session_id="test-session")
            logger = logging.getLogger(f"recovery-key-{id(error_file)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            logger.addHandler(handler)
            try:
                logger.error("generation failed", extra={"recovery_key": "certificate.generation"})
                self.assertEqual(len(handler.active_errors), 1)
                logger.info("another operation succeeded", extra={"recovery_key": "label.print"})
                self.assertEqual(len(handler.active_errors), 1)
                logger.info("generation recovered", extra={"recovery_key": "certificate.generation"})
                self.assertEqual(len(handler.active_errors), 0)
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

    def test_partial_batch_completion_does_not_recover_batch_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            handler = create_error_file_handler(error_file, session_id="test-session")
            logger = logging.getLogger(f"batch-recovery-{id(error_file)}")
            old_level, old_propagate = logger.level, logger.propagate
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
            logger.addHandler(handler)
            try:
                logger.error("batch failed", extra={"recovery_key": "certificates.batch"})
                logger.info(
                    "batch completed with failures",
                    extra={"event": "certificates.batch.completed"},
                )
                self.assertEqual(len(handler.active_errors), 1)
                logger.info(
                    "batch completed successfully",
                    extra={
                        "event": "certificates.batch.completed",
                        "recovery_key": "certificates.batch",
                    },
                )
                self.assertEqual(len(handler.active_errors), 0)
            finally:
                logger.removeHandler(handler)
                handler.close()
                logger.setLevel(old_level)
                logger.propagate = old_propagate

    def test_resolved_error_history_expires_but_active_errors_do_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            error_file = Path(temporary) / "errors.log"
            resolved_logger = logging.getLogger(f"resolved-history-{id(error_file)}")
            active_logger = logging.getLogger(f"active-history-{id(error_file)}")
            previous = [(logger, logger.level, logger.propagate) for logger in (resolved_logger, active_logger)]
            handler = create_error_file_handler(error_file, session_id="test-session")
            for logger in (resolved_logger, active_logger):
                logger.setLevel(logging.DEBUG)
                logger.propagate = False
                logger.addHandler(handler)
            try:
                resolved_logger.error("resolved issue marker")
                resolved_logger.info("operation succeeded")
                active_logger.error("active issue marker")
            finally:
                for logger in (resolved_logger, active_logger):
                    logger.removeHandler(handler)
                handler.close()
                for logger, old_level, old_propagate in previous:
                    logger.setLevel(old_level)
                    logger.propagate = old_propagate

            old_date = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
            lines = error_file.read_text(encoding="utf-8").splitlines()
            aged_lines = []
            for line in lines:
                if line.startswith("[ERROR OPEN] "):
                    data = json.loads(line[len("[ERROR OPEN] "):])
                    data["opened"] = old_date
                    data["last_seen"] = old_date
                    line = "[ERROR OPEN] " + json.dumps(data, ensure_ascii=False, sort_keys=True)
                elif line.startswith("[ERROR RESOLVED] "):
                    data = json.loads(line[len("[ERROR RESOLVED] "):])
                    data["at"] = old_date
                    line = "[ERROR RESOLVED] " + json.dumps(data, ensure_ascii=False, sort_keys=True)
                aged_lines.append(line)
            error_file.write_text("\n".join(aged_lines) + "\n", encoding="utf-8")

            resumed = create_error_file_handler(error_file, session_id="test-session")
            try:
                active = resumed.get_active_errors()
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]["message"], "active issue marker")
                contents = error_file.read_text(encoding="utf-8")
                self.assertNotIn("resolved issue marker", contents)
                self.assertIn("active issue marker", contents)
            finally:
                resumed.close()

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
