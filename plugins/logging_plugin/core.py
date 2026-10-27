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
  • archive_old_logs enligt LOG-021 (archive/ rensas aldrig).
  • Export: txt/json/csv/html/md — alla atomiska och ÅÄÖ-säkra.
"""

import csv as _csv
import gzip
import html as _html
import json
import logging
import shutil
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

    9.1.0 (ny loggpolicy): vid midnattsrotation (och vid uppstart om appen
    missade midnatten) gzip-komprimeras den daterade filen till archive/ och
    tas bort — kvar i loggmappen finns bara dagens logg. Arkivet rensas sedan
    av archive_old_logs() efter 3 veckor (LOG-021 reviderad 2026-09-22).
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

    def close(self) -> None:
        super().close()


class DetailedFormatter(logging.Formatter):
    """Detaljerad radformatter med session, tråd och kodposition."""

    def __init__(self, session_id: str = SESSION_ID) -> None:
        super().__init__(
            "%(asctime)s | %(levelname)-8s | [%(session)s] | "
            "%(threadName)-15s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
            "%Y-%m-%d %H:%M:%S",
        )
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        record.session = getattr(record, "session", self.session_id)
        return super().format(record)


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
        f"Intygsgenerator loggexport {datetime.now().isoformat(timespec='seconds')}",
        f"Session: {session_id} │ Poster: {len(entries)}",
        "=" * 72,
    ]
    for entry in entries:
        lines.append(
            f"{entry.timestamp:%Y-%m-%d %H:%M:%S} | {entry.level:<8} | "
            f"{entry.category} | {entry.message}"
        )
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
        writer.writerow(["timestamp", "level", "category", "message", "thread", "event"])
        for entry in entries:
            writer.writerow([
                entry.timestamp.isoformat(), entry.level, entry.category,
                entry.message, entry.thread_name, entry.event or "",
            ])
    return True


def export_html(path: Path | str, entries: list[LogEntry], session_id: str, stats: Any) -> bool:
    colors = {"ERROR": "#c62828", "CRITICAL": "#7d1172", "WARNING": "#e65100",
              "INFO": "#1a1a1a", "DEBUG": "#666666"}
    rows = []
    for entry in entries:
        color = colors.get(str(entry.level).upper(), "#1a1a1a")
        rows.append(
            f"<tr><td>{entry.timestamp:%H:%M:%S}</td>"
            f"<td style='color:{color}'>{_html.escape(entry.level)}</td>"
            f"<td>{_html.escape(entry.category)}</td>"
            f"<td>{_html.escape(entry.message)}</td></tr>"
        )
    content = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Loggexport</title></head><body>"
        f"<h1>Loggexport {session_id}</h1><table border='1' cellpadding='4'>"
        + "".join(rows) + "</table></body></html>"
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
        lines.append(f"- `{entry.timestamp:%H:%M:%S}` **{entry.level}** {entry.category}{event}: {entry.message}")
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
    "ColorFormatter",
    "archive_old_logs",
    "export_text",
    "export_json",
    "export_csv",
    "export_html",
    "export_markdown",
]
