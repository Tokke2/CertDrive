# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/logging_plugin/core.py
Sökväg: Intygsgenerator/plugins/logging_plugin/core.py

Intygsgenerator — logging-core (kompatibel med plugin.py 9.x):
LogEntry, LogStorage, QuickFilter, SmartRotatingHandler, formatters,
archive_old_logs och exportfunktioner.

Version 9.0.0 (2026-09-21): kompat-yta mot plugin.py + uppgraderingar:
  • LogEntry har valfria `event`/`ctx` (AI-kontrakt) — JSONL/export inkluderar dem.
  • LogStorage: trådsäker deque med maxlen (minne), search med regex+bookmark,
    stats med events_per_minute/duration.
  • SmartRotatingHandler: gzip vid rollover (asynkron), infra.*-events.
  • archive_old_logs enligt LOG-021 (dagsarkiv gallras efter 21 dagar).
  • Export: txt/json/csv/html/md — alla atomiska och ÅÄÖ-säkra.
"""

import csv as _csv
import gzip
import hashlib
import html as _html
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable

__version__: str = "9.1.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

SESSION_ID: str = uuid.uuid4().hex[:12]

_logger = logging.getLogger(__name__)

LOG_MAX_BYTES: int = 10 * 1024 * 1024
LOG_BACKUP_COUNT: int = 15
ARCHIVE_AFTER_DAYS: int = 21


def _evt(event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    try:
        _logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
    except Exception:
        pass


# ─── Enum ────────────────────────────────────────────────────────────────────


class QuickFilter:
    """Snabbfilter för loggvyn."""

    ALL = "all"
    ERRORS_ONLY = "errors"
    WARNINGS_UP = "warnings_up"
    LAST_HOUR = "last_hour"
    LAST_24H = "last_24h"
    TODAY = "today"
    BOOKMARKED = "bookmarked"


# ─── LogEntry ────────────────────────────────────────────────────────────────


def exception_details(record: logging.LogRecord) -> tuple[str | None, str | None]:
    """Extract exception type and traceback, bounded to protect the log queue."""
    if record.exc_info:
        try:
            exception_type = getattr(record.exc_info[0], "__name__", None)
            trace = logging.Formatter().formatException(record.exc_info)
            if len(trace) > 32_000:
                trace = trace[:32_000] + "\\n...[traceback truncated at 32,000 characters]"
            return exception_type, trace
        except Exception:
            return getattr(record.exc_info[0], "__name__", None), "Traceback formatting failed"
    if record.exc_text:
        trace = str(record.exc_text)
        if len(trace) > 32_000:
            trace = trace[:32_000] + "\\n...[traceback truncated at 32,000 characters]"
        return None, trace
    return None, None


@dataclass(slots=True)
class LogEntry:
    """En loggpost. 9.0.0: valfria event/ctx för AI-kontraktet."""

    id: int = 0
    timestamp: datetime = field(default_factory=datetime.now)
    level: str = "INFO"
    category: str = ""
    message: str = ""
    session_id: str = SESSION_ID
    thread_name: str = ""
    func_name: str = ""
    line_no: int = 0
    event: str | None = None
    ctx: dict[str, Any] | None = None
    bookmarked: bool = False
    exception_type: str | None = None
    exception: str | None = None
    module_name: str = ""
    filename: str = ""
    process_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "level": self.level,
            "category": self.category,
            "message": self.message,
            "session": self.session_id,
            "thread": self.thread_name,
            "function": self.func_name,
            "line": self.line_no,
        }
        if self.event:
            data["event"] = self.event
        if self.ctx:
            data["ctx"] = self.ctx
        if self.exception_type:
            data["exception_type"] = self.exception_type
        if self.exception:
            data["exception"] = self.exception
        if self.module_name:
            data["module"] = self.module_name
        if self.filename:
            data["filename"] = self.filename
        if self.process_id:
            data["process"] = self.process_id
        return data


# ─── LogStorage ──────────────────────────────────────────────────────────────


class _StorageStats:
    def __init__(self, storage: "LogStorage") -> None:
        with storage._lock:
            self.total_events = len(storage._entries) + storage._dropped
            self.errors = storage._errors
            self.warnings = storage._warnings
            self.events_by_level = dict(storage._level_counts)
            self._start = storage._start_time

    def get_duration_str(self) -> str:
        seconds = (datetime.now() - self._start).total_seconds()
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {secs}s"
        return f"{secs}s"

    def get_events_per_minute(self) -> float:
        minutes = (datetime.now() - self._start).total_seconds() / 60.0
        return self.total_events / minutes if minutes > 0.05 else float(self.total_events)


class LogStorage:
    """Trådsäker minneslagring med maxlen (bounded minne) och sökning."""

    def __init__(self, max_memory_entries: int = 50_000) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=int(max_memory_entries))
        self._lock = threading.RLock()
        self._next_id = 1
        self._dropped = 0
        self._errors = 0
        self._warnings = 0
        self._level_counts: dict[str, int] = {}
        self._start_time = datetime.now()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def add(self, entry: LogEntry) -> LogEntry:
        with self._lock:
            if entry.id == 0:
                entry.id = self._next_id
                self._next_id += 1
            full = len(self._entries) == self._entries.maxlen
            self._entries.append(entry)
            if full:
                self._dropped += 1
            level = str(entry.level).upper()
            self._level_counts[level] = self._level_counts.get(level, 0) + 1
            if level in {"ERROR", "CRITICAL"}:
                self._errors += 1
            elif level == "WARNING":
                self._warnings += 1
        return entry

    def get_all(self) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)

    def get_recent(self, count: int = 50) -> list[LogEntry]:
        with self._lock:
            return list(self._entries)[-count:]

    def get_stats(self) -> _StorageStats:
        return _StorageStats(self)

    def set_bookmarked(self, entry_id: int, value: bool = True) -> bool:
        with self._lock:
            for entry in self._entries:
                if entry.id == entry_id:
                    entry.bookmarked = bool(value)
                    return True
        return False

    def search(
        self,
        query: str = "",
        levels: list[str] | None = None,
        start_time: datetime | None = None,
        bookmarked_only: bool = False,
        use_regex: bool = False,
        limit: int = 2_000,
    ) -> list[LogEntry]:
        """Sök poster: substring eller regex, nivåer, tid, bookmarks."""
        pattern = None
        if query and use_regex:
            with suppress(Exception):
                import re as _re

                pattern = _re.compile(query, _re.IGNORECASE)
        level_set = {str(level).upper() for level in levels} if levels else None
        results: list[LogEntry] = []
        with self._lock:
            for entry in self._entries:
                if level_set and str(entry.level).upper() not in level_set:
                    continue
                if start_time is not None and entry.timestamp < start_time:
                    continue
                if bookmarked_only and not entry.bookmarked:
                    continue
                if query:
                    if pattern is not None:
                        if not pattern.search(entry.message):
                            continue
                    elif query.lower() not in entry.message.lower():
                        continue
                results.append(entry)
                if len(results) >= limit:
                    break
        return results


# ─── Handlers och formatters ────────────────────────────────────────────────


class SmartRotatingHandler(TimedRotatingFileHandler):
    """Dagsroterande logg: aktiv fil är ALLTID endast dagens logg.

    Vid midnattsrotation komprimeras den daterade filen till archive/ och tas
    bort från den aktiva mappen. Dagsarkiv gallras automatiskt efter 21 dagar.
    """

    def __init__(
        self,
        filename: str,
        when: str = "midnight",
        backupCount: int = 0,
        encoding: str = "utf-8",
        compress: bool = True,
        archive_dir: Path | str | None = None,
        maxBytes: int = 0,  # accepteras för bakåtkompatibilitet (dagsrotation råder)
        **kwargs: Any,
    ) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename, when=when, backupCount=0, encoding=encoding, **kwargs
        )
        self.compress = compress
        self.archive_dir = (
            Path(archive_dir) if archive_dir else Path(filename).parent / "archive"
        )

    def doRollover(self) -> None:  # noqa: N802
        super().doRollover()
        if not self.compress:
            return
        base = Path(self.baseFilename)
        with suppress(OSError):
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            for old in sorted(base.parent.glob(f"{base.name}.20*")):
                dest = self.archive_dir / f"{old.name}.gz"
                try:
                    with open(old, "rb") as inp, gzip.open(dest, "wb") as outp:
                        shutil.copyfileobj(inp, outp)
                    old.unlink()
                    _evt("infra.log_archived", f"Dagslogg arkiverad: {dest.name}",
                         logging.DEBUG, file=dest.name)
                except OSError:
                    _logger.exception("Kunde inte arkivera %s", old)

    def rollover_if_due(self, *, force: bool = False) -> bool:
        """Run a due midnight rollover even when no new log record arrives."""
        self.acquire()
        try:
            if not force and time.time() < self.rolloverAt:
                return False
            self.doRollover()
            return True
        finally:
            self.release()

    def close(self) -> None:
        super().close()


class DetailedFormatter(logging.Formatter):
    """Detaljerad radformatter med session, tråd och kodposition."""

    def __init__(self, session_id: str = SESSION_ID) -> None:
        super().__init__(
            "%(asctime)s | %(levelname)-8s | [%(session)s] | "
            "%(threadName)-15s | pid=%(process)d | %(name)s | "
            "%(pathname)s:%(funcName)s:%(lineno)d | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        record.session = getattr(record, "session", self.session_id)
        return super().format(record)


class ErrorSummaryFilter(logging.Filter):
    """Keep errors plus explicitly tagged EventBus failure warnings."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.ERROR or bool(
            getattr(record, "_intygsdrive_error_event", False)
        )


class ErrorLedgerHandler(logging.FileHandler):
    """Persist active errors and resolve them only on a matching recovery key."""

    _EVENT_GROUPS = {
        "certificate.generation_failed": "certificate.generation",
        "certificate.generated": "certificate.generation",
        "cert.generate_failed": "certificate.generation",
        "cert.generate_error": "certificate.generation",
        "cert.generated": "certificate.generation",
        "label.calibration_failed": "label.calibration",
        "label.calibration_printed": "label.calibration",
        "label.print_failed": "label.print",
        "label.printed": "label.print",
        "certificates.batch.failed": "certificates.batch",
    }

    def __init__(self, filename: Path | str, session_id: str = SESSION_ID) -> None:
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(str(path), mode="a", encoding="utf-8", delay=False)
        self.setLevel(logging.DEBUG)
        self.setFormatter(DetailedFormatter(session_id))
        self.active_errors: dict[str, dict[str, Any]] = {}
        self._load_active_errors(path)
        self._intygsdrive_error_handler = True  # type: ignore[attr-defined]
        self.prune_resolved()

    @classmethod
    def _event_name(cls, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        if event:
            return str(event).strip().casefold()
        message = record.getMessage()
        if message.startswith("[EVENT] "):
            return message[len("[EVENT] "):].split(maxsplit=1)[0].casefold()
        return ""

    @classmethod
    def _source_key(cls, record: logging.LogRecord, event: str) -> str:
        recovery_key = getattr(record, "recovery_key", None)
        if recovery_key:
            return f"recovery:{str(recovery_key).strip().casefold()}"
        if event:
            recovery_group = cls._EVENT_GROUPS.get(event)
            return f"recovery:{recovery_group}" if recovery_group else f"event:{event}"
        return "call:{}|{}|{}".format(
            record.name,
            record.pathname,
            record.funcName,
        )

    def _load_active_errors(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            _logger.exception("Could not restore active errors from %s", path)
            return
        for line in lines:
            try:
                if line.startswith("[ERROR OPEN] "):
                    data = json.loads(line[len("[ERROR OPEN] "):])
                    issue_id = str(data["id"])
                    self.active_errors[issue_id] = {
                        "source": str(data["source"]),
                        "opened": str(data.get("opened", "")),
                        "last_seen": str(data.get("opened", "")),
                        "level": str(data.get("level", "ERROR")),
                        "message": str(data.get("message", data["source"])),
                        "occurrences": int(data.get("occurrences", 1)),
                    }
                elif line.startswith("[ERROR REPEAT] "):
                    data = json.loads(line[len("[ERROR REPEAT] "):])
                    issue = self.active_errors.get(str(data["id"]))
                    if issue is not None:
                        issue["last_seen"] = str(data.get("at", issue["last_seen"]))
                        issue["message"] = str(data.get("message", issue["message"]))
                        issue["occurrences"] = int(data.get("occurrences", issue["occurrences"] + 1))
                elif line.startswith("[ERROR RECORD id="):
                    issue_id, _, formatted = line[len("[ERROR RECORD id="):].partition("] ")
                    issue = self.active_errors.get(issue_id)
                    if issue is not None:
                        issue["message"] = formatted.rsplit(" | ", 1)[-1][:240]
                elif line.startswith("[ERROR RESOLVED] "):
                    data = json.loads(line[len("[ERROR RESOLVED] "):])
                    self.active_errors.pop(str(data["id"]), None)
            except (ValueError, KeyError, TypeError):
                continue

    def _write_line(self, text: str) -> None:
        if self.stream is None:
            self.stream = self._open()
        self.stream.write(text + "\n")
        self.flush()

    def _write_metadata(self, kind: str, payload: dict[str, Any]) -> None:
        self._write_line(f"[ERROR {kind}] " + json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _record_error(self, record: logging.LogRecord, source: str) -> None:
        issue_id = hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest()[:16]
        at = datetime.fromtimestamp(record.created).isoformat(timespec="seconds")
        message = record.getMessage().replace("\n", " ")[:240]
        issue = self.active_errors.get(issue_id)
        if issue is None:
            issue = {
                "source": source,
                "opened": at,
                "last_seen": at,
                "level": record.levelname,
                "message": message,
                "occurrences": 1,
            }
            self.active_errors[issue_id] = issue
            self._write_metadata("OPEN", {"id": issue_id, **issue})
        else:
            issue["last_seen"] = at
            issue["message"] = message
            issue["occurrences"] = int(issue.get("occurrences", 1)) + 1
            self._write_metadata("REPEAT", {
                "id": issue_id,
                "at": at,
                "message": message,
                "occurrences": issue["occurrences"],
            })
        self._write_line(f"[ERROR RECORD id={issue_id}] {self.format(record)}")

    def _resolve(self, record: logging.LogRecord, event: str) -> None:
        if not self.active_errors:
            return
        # Explicit event/recovery keys are authoritative; generic log records
        # resolve only when they come from the same function and say they succeeded.
        if event or getattr(record, "recovery_key", None):
            success_source = self._source_key(record, event)
        else:
            if record.levelno > logging.INFO:
                return
            message = record.getMessage().casefold()
            negative = (
                "unsuccessful", "failed", "failure", "error", "exception", "unable",
                "could not", "cannot", "can't", "never", "not ", "no ", "inte ",
                "ej ", "misslyck", "fel ",
            )
            positive = (
                "success", "succeeded", "completed", "generated", "saved", "updated",
                "restored", "printed", "recovered", "finished", "lyckades", "slutförd",
                "slutfört", "sparad", "sparat", "uppdaterad", "återställd", "genererad",
                "utskriven", "färdig", "klart", "återhämtad",
            )
            if any(term in message for term in negative) or not any(term in message for term in positive):
                return
            success_source = self._source_key(record, "")

        resolved = [
            issue_id for issue_id, issue in self.active_errors.items()
            if issue.get("source") == success_source
        ]
        for issue_id in resolved:
            self._write_metadata("RESOLVED", {
                "id": issue_id,
                "source": success_source,
                "at": datetime.fromtimestamp(record.created).isoformat(timespec="seconds"),
            })
            self.active_errors.pop(issue_id, None)

    def get_active_errors(self) -> list[dict[str, Any]]:
        self.acquire()
        try:
            return [
                {"id": issue_id, **issue.copy()}
                for issue_id, issue in self.active_errors.items()
            ]
        finally:
            self.release()

    @staticmethod
    def _line_issue_id(line: str) -> str | None:
        if line.startswith("[ERROR RECORD id="):
            return line[len("[ERROR RECORD id="):].split("]", 1)[0]
        for prefix in ("[ERROR OPEN] ", "[ERROR REPEAT] ", "[ERROR RESOLVED] "):
            if line.startswith(prefix):
                try:
                    return str(json.loads(line[len(prefix):])["id"])
                except (ValueError, KeyError, TypeError):
                    return None
        return None

    def prune_resolved(self, older_than_days: int = ARCHIVE_AFTER_DAYS) -> int:
        """Drop managed error history only after its issue has been resolved for the retention period."""
        path = Path(self.baseFilename)
        self.acquire()
        temporary: Path | None = None
        try:
            if not path.is_file():
                return 0
            self.flush()
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            active: set[str] = set()
            resolved_at: dict[str, datetime] = {}
            for line in lines:
                if line.startswith("[ERROR OPEN] "):
                    try:
                        data = json.loads(line[len("[ERROR OPEN] "):])
                        issue_id = str(data["id"])
                        active.add(issue_id)
                        resolved_at.pop(issue_id, None)
                    except (ValueError, KeyError, TypeError):
                        pass
                elif line.startswith("[ERROR RESOLVED] "):
                    try:
                        data = json.loads(line[len("[ERROR RESOLVED] "):])
                        issue_id = str(data["id"])
                        active.discard(issue_id)
                        resolved_at[issue_id] = datetime.fromisoformat(str(data["at"]))
                    except (ValueError, KeyError, TypeError):
                        pass
            cutoff = datetime.now() - timedelta(days=max(0, older_than_days))
            expired = {
                issue_id for issue_id, resolved in resolved_at.items()
                if issue_id not in active and resolved < cutoff
            }
            if not expired:
                return 0
            remaining = [
                line for line in lines
                if self._line_issue_id(line) not in expired
            ]
            mode = stat.S_IMODE(path.stat().st_mode)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.writelines(remaining)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            os.replace(temporary, path)
            temporary = None
            return len(expired)
        except OSError as exc:
            with suppress(OSError):
                sys.stderr.write(f"[ERROR LEDGER] Could not prune resolved errors in {path}: {exc}\n")
            return 0
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if self.stream is None and not self._closed:
                try:
                    self.stream = self._open()
                except OSError:
                    _logger.exception("Could not reopen error ledger %s", path)
            self.release()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            is_failure = record.levelno >= logging.ERROR or bool(
                getattr(record, "_intygsdrive_error_event", False)
            )
            event = self._event_name(record)
            if is_failure:
                self._record_error(record, self._source_key(record, event))
            else:
                self._resolve(record, event)
        except Exception:
            self.handleError(record)


def create_general_file_handler(
    filename: Path | str,
    session_id: str = SESSION_ID,
    *,
    archive_dir: Path | str | None = None,
) -> SmartRotatingHandler:
    """Create the all-level log, archiving each completed calendar day."""
    path = Path(filename)
    handler = SmartRotatingHandler(
        str(path),
        when="midnight",
        backupCount=0,
        encoding="utf-8",
        compress=True,
        archive_dir=archive_dir or path.parent / "archive",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(DetailedFormatter(session_id))
    handler._intygsdrive_general_handler = True  # type: ignore[attr-defined]
    return handler


def create_error_file_handler(
    filename: Path | str,
    session_id: str = SESSION_ID,
) -> ErrorLedgerHandler:
    """Create the durable, automatically resolved error ledger."""
    return ErrorLedgerHandler(filename, session_id)


class ColorFormatter(logging.Formatter):
    """ANSI-färger för konsol (aktiveras endast i TTY)."""

    COLORS = {
        "DEBUG": "\033[36m", "INFO": "\033[32m", "WARNING": "\033[33m",
        "ERROR": "\033[31m", "CRITICAL": "\033[35;1m", "RESET": "\033[0m",
    }

    def __init__(self, use_colors: bool = True, session_id: str = SESSION_ID) -> None:
        super().__init__("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S")
        import sys as _sys

        self.use_colors = bool(use_colors) and bool(getattr(_sys.stdout, "isatty", lambda: False)())
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if self.use_colors:
            color = self.COLORS.get(record.levelname, "")
            if color:
                return f"{color}{message}{self.COLORS['RESET']}"
        return message


# ─── Arkivering (LOG-021) ────────────────────────────────────────────────────


def archive_old_logs(log_dir: Path | str, older_than_days: int = ARCHIVE_AFTER_DAYS) -> int:
    """LOG-021 reviderad (2026-09-22): arkiverade loggar äldre än 3 veckor rensas.

    Aktiv logg = endast dagens; arkivet (archive/*.gz) behålls 21 dagar och
    raderas sedan. crash.log röras aldrig.
    """
    log_dir = Path(log_dir)
    archive_dir = log_dir / "archive"
    archived = 0
    cutoff = time.time() - older_than_days * 86400
    with suppress(OSError):
        archive_dir.mkdir(parents=True, exist_ok=True)
        # 1) Rensa arkivfiler äldre än gränsen.
        for gz in archive_dir.glob("*.gz"):
            with suppress(OSError):
                if gz.stat().st_mtime < cutoff:
                    gz.unlink()
                    _evt("infra.archive_purged", f"Arkivlogg rensad: {gz.name}",
                         logging.DEBUG, file=gz.name)
        # 2) Arkivera kvarlämnade daterade loggar.
        for path in log_dir.iterdir():
            if not path.is_file():
                continue
            if path.name in {"crash.log"} or path.suffix == ".gz":
                continue
            if not (path.name.startswith("app.log.20") or path.name.startswith("session_")):
                continue
            with suppress(OSError):
                if path.stat().st_mtime >= cutoff:
                    continue
            dest = archive_dir / f"{path.name}.gz"
            if dest.exists():
                continue
            try:
                with open(path, "rb") as inp, gzip.open(dest, "wb") as outp:
                    shutil.copyfileobj(inp, outp)
                path.unlink()
                archived += 1
            except OSError:
                _logger.exception("Kunde inte arkivera %s", path)
    if archived:
        _evt("infra.logs_archived", f"Arkiverade {archived} loggfil(er)",
             logging.INFO, count=archived)
    return archived


# ─── Export ──────────────────────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def export_text(path: Path | str, entries: list[LogEntry], session_id: str, stats: Any) -> bool:
    lines = [
        f"IntygsDrive loggexport {datetime.now().isoformat(timespec='seconds')}",
        f"Session: {session_id} │ Poster: {len(entries)}",
        "=" * 72,
    ]
    for entry in entries:
        source = entry.filename or entry.module_name
        location = f"{source}:{entry.func_name}:{entry.line_no}" if source else ""
        lines.append(
            f"{entry.timestamp:%Y-%m-%d %H:%M:%S} | {entry.level:<8} | "
            f"{entry.category} | {entry.thread_name} pid={entry.process_id} | "
            f"{location} | {entry.message}"
        )
        if entry.exception:
            exception_type = entry.exception_type or "Exception"
            lines.extend((f"  {exception_type} traceback:", entry.exception))
    _atomic_write(Path(path), "\n".join(lines) + "\n")
    return True


def export_json(path: Path | str, entries: list[LogEntry], session_id: str, stats: Any) -> bool:
    data = {
        "session": session_id,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(entries),
        "entries": [entry.to_dict() for entry in entries],
    }
    _atomic_write(Path(path), json.dumps(data, ensure_ascii=False, indent=2, default=str))
    return True


def export_csv(path: Path | str, entries: list[LogEntry]) -> bool:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = _csv.writer(handle)
        writer.writerow([
            "timestamp", "level", "category", "message", "thread", "process",
            "module", "filename", "function", "line", "event",
            "exception_type", "exception",
        ])
        for entry in entries:
            writer.writerow([
                entry.timestamp.isoformat(), entry.level, entry.category,
                entry.message, entry.thread_name, entry.process_id,
                entry.module_name, entry.filename, entry.func_name, entry.line_no,
                entry.event or "", entry.exception_type or "", entry.exception or "",
            ])
    return True


def export_html(path: Path | str, entries: list[LogEntry], session_id: str, stats: Any) -> bool:
    colors = {"ERROR": "#c62828", "CRITICAL": "#7d1172", "WARNING": "#e65100",
              "INFO": "#1a1a1a", "DEBUG": "#666666"}
    rows = []
    for entry in entries:
        color = colors.get(str(entry.level).upper(), "#1a1a1a")
        exception_html = ""
        if entry.exception:
            exception_type = _html.escape(entry.exception_type or "Exception")
            exception_html = (
                f"<details><summary>{exception_type} traceback</summary>"
                f"<pre>{_html.escape(entry.exception)}</pre></details>"
            )
        source = _html.escape(entry.filename or entry.module_name)
        location = _html.escape(f"{source}:{entry.func_name}:{entry.line_no}")
        rows.append(
            f"<tr><td>{entry.timestamp:%H:%M:%S}</td>"
            f"<td style='color:{color}'>{_html.escape(entry.level)}</td>"
            f"<td>{_html.escape(entry.category)}</td>"
            f"<td>{_html.escape(entry.thread_name)} / pid {entry.process_id}</td>"
            f"<td>{location}</td>"
            f"<td>{_html.escape(entry.message)}{exception_html}</td></tr>"
        )
    content = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Loggexport</title></head><body>"
        f"<h1>Loggexport {session_id}</h1><table border='1' cellpadding='4'>"
        "<thead><tr><th>Tid</th><th>Nivå</th><th>Kategori</th><th>Tråd/process</th>"
        "<th>Källa</th><th>Meddelande och undantag</th></tr></thead><tbody>"
        + "".join(rows) + "</tbody></table></body></html>"
    )
    _atomic_write(Path(path), content)
    return True


def export_markdown(path: Path | str, entries: list[LogEntry], session_id: str, stats: Any) -> bool:
    lines = [
        f"# Loggexport — session {session_id}",
        "",
        f"Exporterad: {datetime.now().isoformat(timespec='seconds')} · Poster: {len(entries)}",
        "",
    ]
    for entry in entries:
        event = f" `{entry.event}`" if entry.event else ""
        source = entry.filename or entry.module_name
        location = f" `{source}:{entry.func_name}:{entry.line_no}`" if source else ""
        process = f" `{entry.thread_name}` pid={entry.process_id}"
        lines.append(
            f"- `{entry.timestamp:%H:%M:%S}` **{entry.level}** "
            f"{entry.category}{event}{process}{location}: {entry.message}"
        )
        if entry.exception:
            exception_type = entry.exception_type or "Exception"
            lines.extend(("", f"  `{exception_type}` traceback:", "  ```text"))
            lines.extend(f"  {line}" for line in entry.exception.splitlines())
            lines.append("  ```")
    _atomic_write(Path(path), "\n".join(lines) + "\n")
    return True


__all__: list[str] = [
    "__version__",
    "COPYRIGHT",
    "SESSION_ID",
    "QuickFilter",
    "LogEntry",
    "LogStorage",
    "SmartRotatingHandler",
    "DetailedFormatter",
    "ErrorLedgerHandler",
    "create_general_file_handler",
    "create_error_file_handler",
    "ColorFormatter",
    "archive_old_logs",
    "export_text",
    "export_json",
    "export_csv",
    "export_html",
    "export_markdown",
]
