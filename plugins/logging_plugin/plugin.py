# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/logging_plugin/plugin.py
Sökväg: Intygsgenerator/plugins/logging_plugin/plugin.py
Version: 8.9.0 (uppgraderad från 8.8.1, 2026-09-18)

Intygsgenerator — central logging-controller. Skriver all loggning till app.log
och speglar fel till errors.log; behåller crash hooks, EventBus och Logger UI.

Logglayout: exakt två loggerfiler — app.log innehåller alla nivåer och errors.log
innehåller fel och uttryckligen markerade felhändelser. Loggar roteras eller arkiveras
inte till extrafiler. Tidigare JSONL-, crash- och roterade loggar migreras in i de två
filerna vid uppstart; gamla poster bevaras.
"""

import atexit
import contextlib
import gzip
import json
import logging
import os
import queue
import re
import signal
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Final

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QVBoxLayout, QWidget

from .core import (
    COPYRIGHT,
    SESSION_ID as CORE_SESSION_ID,
    ColorFormatter,
    create_error_file_handler,
    create_general_file_handler,
    exception_details,
    LogEntry,
    LogStorage,
    QuickFilter,
    export_csv,
    export_html,
    export_json,
    export_markdown,
    export_text,
)
from .tracer import FileOperation, FileSystemTracer
from .view import LoggerView

if TYPE_CHECKING:
    from core import AppContext

__version__: str = "9.2.0"
__all__: list[str] = ["Plugin"]

PLUGIN_NAME: str = "Logger"
PLUGIN_PRIORITY: int = 1

_TRACKED_EVENTS: Final[tuple[str, ...]] = (
    "certificate.before_generate", "certificate.generated", "certificate.generation_failed",
    "certificate.template_selected", "certificate.placeholder_updated", "certificates.imported",
    "certificates.batch.started", "certificates.batch.completed", "templates.scanned",
    "refresh_templates", "template.created", "template.deleted", "template.updated",
    "statistics.certificate_generated", "statistics.updated", "statistics.response",
    "settings.changed", "settings.theme_changed", "tray.status_update", "tray.minimize",
    "tray.restore", "plugin.loaded", "plugin.unloaded", "plugin.failed",
    "plugin_manager.ready", "app.startup", "app.ready", "app.closing", "app.shutdown",
    "notification", "error.occurred",
)

_logger: logging.Logger = logging.getLogger(__name__)

_HEARTBEAT_MS: Final[int] = 300_000  # 5 minuter; INTYGG_LOG_HEARTBEAT=0 stänger av


class _LoggerSignals(QObject):
    entries_updated = Signal()
    stats_updated = Signal()
    alert_raised = Signal(str)


class _InterceptorHandler(logging.Handler):
    """Översätter logging.LogRecord till LogEntry och köar den icke-blockerande.

    v8.9.0: droppade poster (kö full) räknas i ``dropped`` i stället för att
    försvinna tyst — syns i statistik och AI-rapport.
    """

    def __init__(self, destination: "queue.Queue[LogEntry]", session_id: str) -> None:
        super().__init__(level=logging.DEBUG)
        self._destination = destination
        self._session_id = session_id
        self.dropped: int = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 9.0.0: event/ctx följer med (AI-kontraktet) så att strukturerad
            # Loggning från hela appen blir analyserbar i de två loggfilerna och vyn.
            exception_type, exception = exception_details(record)
            entry = LogEntry(
                id=0,
                timestamp=datetime.fromtimestamp(record.created),
                level=record.levelname,
                category=record.name.rsplit(".", 1)[-1][:32],
                message=record.getMessage(),
                session_id=self._session_id,
                thread_name=record.threadName or "",
                func_name=record.funcName or "",
                line_no=record.lineno,
                event=getattr(record, "event", None),
                ctx=getattr(record, "ctx", None),
                exception_type=exception_type,
                exception=exception,
                module_name=record.module or "",
                filename=record.filename or "",
                process_id=record.process or 0,
            )
            self._destination.put_nowait(entry)
        except queue.Full:
            self.dropped += 1
            return
        except Exception:
            _logger.debug("[LOGGING] Interceptor misslyckades", exc_info=True)


class Plugin:
    """IPlugin-implementation och enda ägare av applikationens loggning."""

    name: str = PLUGIN_NAME
    version: str = __version__
    description: str = "Loggning till app.log och errors.log med sökning, filsystemsspårning och export"
    priority: int = PLUGIN_PRIORITY

    MAX_MEMORY_ENTRIES: Final[int] = 50_000
    MAX_QUEUE_SIZE: Final[int] = 10_000
    UI_UPDATE_INTERVAL_MS: Final[int] = 250
    FS_FLUSH_INTERVAL_MS: Final[int] = 250
    CONSECUTIVE_ERROR_THRESHOLD: Final[int] = 5

    def __init__(self) -> None:
        self._context: "AppContext | Any | None" = None
        self._event_bus: Any | None = None
        self._unsubscribers: list[Callable[[], None]] = []
        self._root_logger: logging.Logger | None = None
        self._python_root_logger: logging.Logger | None = None
        self._previous_python_root_level: int | None = None
        self._general_handler: logging.Handler | None = None
        self._error_handler: logging.Handler | None = None
        self._interceptor: _InterceptorHandler | None = None
        self._previous_exception_hook: Any | None = None
        self._previous_thread_exception_hook: Any | None = None
        self._hooks_installed = False
        self._loaded = False
        self._running = False
        self._stats_lock = threading.RLock()
        self._alerts_lock = threading.RLock()

        self.session_id = CORE_SESSION_ID
        self.session_start = datetime.now()
        self.log_dir: Path | None = None
        self.archive_dir: Path | None = None
        self.current_log_file: Path | None = None
        self.json_log_file: Path | None = None
        self.crash_log_file: Path | None = None
        self.error_log_file: Path | None = None

        self._storage = LogStorage(max_memory_entries=self.MAX_MEMORY_ENTRIES)
        self._queue: "queue.Queue[LogEntry]" = queue.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._signals = _LoggerSignals()
        self._tracer: FileSystemTracer | None = None
        self._view: LoggerView | None = None
        self._fs_timer: QTimer | None = None
        self._heartbeat_timer: QTimer | None = None

        self._alerts: deque[dict[str, Any]] = deque(maxlen=200)
        self._alert_counter = 0
        self._consecutive_errors = 0
        self._certificates_generated = 0
        self._events_by_level: dict[str, int] = {}
        self._errors = 0
        self._warnings = 0
        self._live_mode = True
        self._detailed_mode = False
        # Visibility into previously silent losses.
        self._step_failures = 0
        self._fs_operations = 0
        self._heartbeats = 0

    # ── IPlugin-livscykel ─────────────────────────────────────────────────

    def on_load(self, context: Any) -> None:
        if self._loaded:
            return
        self._context = context
        self._event_bus = getattr(context, "event_bus", None)
        steps: tuple[tuple[str, Callable[[], None]], ...] = (
            ("sökvägar", self._setup_paths),
            ("migrera äldre loggar", self._migrate_legacy_logs),
            ("logging", self._setup_logging),
            ("filsystem-spårning", self._setup_tracer),
            ("bakgrundsköer", self._start_background_threads),
            ("crashhanterare", self._install_crash_handlers),
            ("EventBus", self._subscribe_events),
        )
        for name, action in steps:
            started = time.monotonic()
            try:
                action()
            except Exception:
                self._step_failures += 1
                print(f"[LOGGER] on_load/{name} misslyckades", file=sys.stderr)
                _logger.exception("[LOGGING] on_load/%s misslyckades", name)
                continue
            self._safe_log(
                "debug",
                f"[STEP] on_load/{name} klart ({(time.monotonic() - started) * 1000:.1f} ms)",
            )
        if self._root_logger is not None:
            setter = getattr(context, "set_logger", None)
            if callable(setter):
                try:
                    setter(self._root_logger)
                except Exception:
                    _logger.exception("[LOGGING] Kunde inte registrera applogger")
        self._loaded = True
        self._log_session_start()
        self._safe_log(
            "info",
            f"[LOGGER] v{self.version} laddad (prio={self.priority}) — "
            f"stegfel={self._step_failures}",
        )
        self._emit(
            "logging.session_started",
            {
                "session_id": self.session_id,
                "timestamp": self.session_start.isoformat(timespec="seconds"),
                "log_file": str(self.current_log_file or ""),
                "errors_file": str(self.error_log_file or ""),
                "version": self.version,
            },
        )

    def setup(self, context: Any) -> None:
        self.on_load(context)

    def on_unload(self) -> None:
        self._loaded = False
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                _logger.exception("[LOGGING] Kunde inte avregistrera EventBus-prenumeration")
        self._unsubscribers.clear()
        if self._tracer is not None:
            try:
                self._tracer.uninstall()
            except Exception:
                _logger.exception("[LOGGING] Kunde inte avinstallera filsystem-tracer")
        self._running = False
        self._log_session_end()
        self._drain_queue()
        self._restore_crash_handlers()
        self._close_handlers()
        if self._fs_timer is not None:
            with contextlib.suppress(Exception):
                self._fs_timer.stop()
        if self._heartbeat_timer is not None:
            with contextlib.suppress(Exception):
                self._heartbeat_timer.stop()
        self._context = None
        self._event_bus = None

    def on_close(self) -> None:
        self.on_unload()

    def get_tab(self) -> QWidget | None:
        if self._view is not None:
            return self._view
        try:
            self._view = LoggerView(self.session_id, COPYRIGHT)
            self._view.refresh_requested.connect(self._refresh_view)
            self._view.clear_requested.connect(self._clear_display)
            self._view.open_folder_requested.connect(self._open_log_folder)
            self._view.export_requested.connect(self._export)
            self._view.show_alerts_requested.connect(self._show_alerts)
            self._view.search_changed.connect(self._refresh_view)
            self._view.filters_changed.connect(self._refresh_view)
            self._view.live_changed.connect(self._set_live_mode)
            self._view.detailed_changed.connect(self._set_detailed_mode)
            self._view.acknowledge_all_requested.connect(self._acknowledge_all)
            self._view.alert_activated.connect(self._activate_alert)
            # 9.2.0: bokmärken persisteras i LogStorage (guarded för äldre vy).
            bookmark_sig = getattr(self._view, "bookmark_requested", None)
            if bookmark_sig is not None:
                bookmark_sig.connect(self._on_bookmark_requested)
            self._signals.entries_updated.connect(self._refresh_view)
            self._signals.stats_updated.connect(self._update_view_stats)
            self._signals.alert_raised.connect(self._on_alert_raised)
            self._fs_timer = QTimer(self._view)
            self._fs_timer.setInterval(self.FS_FLUSH_INTERVAL_MS)
            self._fs_timer.timeout.connect(self._view.flush_fs_pending)
            self._fs_timer.start()
            self._start_heartbeat()
            self._refresh_view()
            self._update_view_stats()
            return self._view
        except Exception as exc:
            _logger.exception("[LOGGING] Kunde inte bygga Logger UI")
            fallback = QWidget()
            layout = QVBoxLayout(fallback)
            layout.addWidget(QLabel(f"⚠️ Logger UI kunde inte laddas:\n{exc}", fallback))
            return fallback

    # ── Setup ─────────────────────────────────────────────────────────────

    def _setup_paths(self) -> None:
        getter = getattr(self._context, "get_path", None) if self._context is not None else None
        if callable(getter):
            try:
                self.log_dir = Path(getter("logs"))
            except Exception:
                _logger.exception("[LOGGING] Kunde inte hämta logs-sökväg")
        if self.log_dir is None:
            self.log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir = self.log_dir / "archive"  # legacy archive path; no new archive files
        self.current_log_file = self.log_dir / "app.log"
        self.json_log_file = None  # JSONL events are stored in app.log/UI memory, not a third file
        self.crash_log_file = None  # crashes are routed to both app.log and errors.log
        self.error_log_file = self.log_dir / "errors.log"

    def _migrate_legacy_logs(self) -> None:
        """Fold old JSONL/crash/rotated logs into the two current files safely."""
        if self.log_dir is None or self.current_log_file is None or self.error_log_file is None:
            return
        sources: set[Path] = set()
        for pattern in ("session_*.jsonl", "crash.log", "app.log.*", "errors.log.*"):
            sources.update(path for path in self.log_dir.glob(pattern) if path.is_file())
        if self.archive_dir is not None and self.archive_dir.is_dir():
            sources.update(
                path for path in self.archive_dir.rglob("*")
                if path.is_file() and (path.suffix in {".gz", ".log", ".jsonl"})
            )

        def read_text(path: Path) -> str:
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rb") as stream:
                return stream.read().decode("utf-8", errors="replace")

        def error_excerpt(path: Path, text: str) -> str:
            name = path.name.casefold()
            if "crash.log" in name or "errors.log" in name:
                return text
            if name.endswith((".jsonl", ".jsonl.gz")):
                kept: list[str] = []
                for line in text.splitlines():
                    try:
                        record = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    level = str(record.get("level", "")).upper()
                    event = str(record.get("event", "")).casefold()
                    message = str(record.get("message", "")).casefold()
                    if level in {"ERROR", "CRITICAL"} or any(
                        token in event or token in message
                        for token in ("error", "failed", "crash", "exception")
                    ):
                        kept.append(line)
                return "\n".join(kept)
            kept = []
            capturing = False
            for line in text.splitlines():
                is_record_start = bool(re.match(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", line))
                is_error_start = bool(re.search(r"\|\s*(ERROR|CRITICAL)\s*\|", line))
                is_tagged_warning = bool(re.search(
                    r"\|\s*WARNING\s*\|.*(?:error|failed|crash|exception|misslyck|fel)",
                    line,
                    re.IGNORECASE,
                ))
                if is_error_start or is_tagged_warning or "[CRASH]" in line:
                    capturing = True
                elif is_record_start:
                    capturing = False
                if capturing:
                    kept.append(line)
            return "\n".join(kept)

        for source in sorted(sources, key=lambda item: str(item).casefold()):
            try:
                text = read_text(source)
                relative = source.relative_to(self.log_dir).as_posix()
                marker = f"[LEGACY LOG IMPORT] {relative}"
                current_general = self.current_log_file.read_text(encoding="utf-8", errors="replace") \
                    if self.current_log_file.is_file() else ""
                if marker not in current_general:
                    with self.current_log_file.open("a", encoding="utf-8", newline="\n") as stream:
                        stream.write(f"\n{marker}\n{text}\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                errors = error_excerpt(source, text)
                if errors:
                    current_errors = self.error_log_file.read_text(encoding="utf-8", errors="replace") \
                        if self.error_log_file.is_file() else ""
                    if marker not in current_errors:
                        with self.error_log_file.open("a", encoding="utf-8", newline="\n") as stream:
                            stream.write(f"\n{marker}\n{errors}\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                source.unlink()
            except OSError:
                _logger.exception("[LOGGING] Kunde inte migrera äldre loggfil %s", source.name)

        # app.log itself is the general destination and must stay in place; copy
        # its historical errors to the error file once without removing history.
        try:
            if self.current_log_file.is_file():
                current_text = self.current_log_file.read_text(encoding="utf-8", errors="replace")
                errors = error_excerpt(self.current_log_file, current_text)
                marker = "[LEGACY LOG ERRORS IMPORT] app.log"
                if errors:
                    current_errors = self.error_log_file.read_text(encoding="utf-8", errors="replace") \
                        if self.error_log_file.is_file() else ""
                    if errors not in current_errors and marker not in current_errors:
                        with self.error_log_file.open("a", encoding="utf-8", newline="\n") as stream:
                            stream.write(f"\n{marker}\n{errors}\n")
                            stream.flush()
                            os.fsync(stream.fileno())
        except OSError:
            _logger.exception("[LOGGING] Kunde inte kopiera historiska fel från app.log")

        if self.archive_dir is not None and self.archive_dir.is_dir():
            for directory in sorted(self.archive_dir.rglob("*"), reverse=True):
                if directory.is_dir():
                    with contextlib.suppress(OSError):
                        directory.rmdir()
            with contextlib.suppress(OSError):
                self.archive_dir.rmdir()

    def _setup_logging(self) -> None:
        logger = logging.getLogger("Intygsgenerator")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        python_root = logging.getLogger()
        self._python_root_logger = python_root
        self._previous_python_root_level = python_root.level
        python_root.setLevel(logging.DEBUG)

        for existing in list(python_root.handlers):
            if getattr(existing, "_intygsdrive_general_handler", False) or getattr(
                existing, "_intygsdrive_error_handler", False
            ):
                python_root.removeHandler(existing)
                with contextlib.suppress(Exception):
                    existing.close()
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()

        if getattr(sys.stderr, "isatty", lambda: False)():
            console = logging.StreamHandler(sys.stderr)
            console.setLevel(logging.INFO)
            console.setFormatter(ColorFormatter(use_colors=True))
            logger.addHandler(console)

        handlers: list[logging.Handler] = []
        try:
            if self.current_log_file is not None:
                self._general_handler = create_general_file_handler(
                    self.current_log_file, self.session_id
                )
                handlers.append(self._general_handler)
            if self.error_log_file is not None:
                self._error_handler = create_error_file_handler(
                    self.error_log_file, self.session_id
                )
                handlers.append(self._error_handler)
            for handler in handlers:
                logger.addHandler(handler)
                python_root.addHandler(handler)
        except Exception:
            for handler in handlers:
                with contextlib.suppress(Exception):
                    logger.removeHandler(handler)
                    python_root.removeHandler(handler)
                    handler.close()
            self._general_handler = None
            self._error_handler = None
            logger.exception("[LOGGING] Kunde inte aktivera app.log och errors.log")

        self._interceptor = _InterceptorHandler(self._queue, self.session_id)
        self._interceptor.setLevel(logging.DEBUG)
        logger.addHandler(self._interceptor)
        self._root_logger = logger

    def _setup_tracer(self) -> None:
        self._tracer = FileSystemTracer(self._on_file_operation)
        self._tracer.install()
        self._safe_log("info", "[LOGGER] Filsystem-spårning aktiverad")

    def _start_background_threads(self) -> None:
        self._running = True
        threading.Thread(target=self._process_queue, name="LogProcessor", daemon=True).start()

    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        for event_name in _TRACKED_EVENTS:
            try:
                unsubscribe = self._event_bus.subscribe(
                    event_name,
                    lambda data=None, tracked=event_name: self._on_event(tracked, data),
                )
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
            except Exception:
                _logger.exception("[LOGGING] Kunde inte prenumerera på %s", event_name)

    # ── Queue och statistik ──────────────────────────────────────────────

    def _process_queue(self) -> None:
        batch: list[LogEntry] = []
        last_ui = datetime.now().timestamp()
        while self._running or not self._queue.empty():
            try:
                try:
                    batch.append(self._queue.get(timeout=0.1))
                except queue.Empty:
                    pass
                while len(batch) < 500:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                if batch:
                    for entry in batch:
                        self._storage.add(entry)
                        self._update_stats(entry)
                        self._check_alerts(entry)
                    batch.clear()
                now = datetime.now().timestamp()
                if (now - last_ui) * 1000 >= self.UI_UPDATE_INTERVAL_MS:
                    if self._live_mode:
                        self._signals.entries_updated.emit()
                    self._signals.stats_updated.emit()
                    last_ui = now
            except Exception:
                _logger.exception("[LOGGING] Queue-processor misslyckades")

    def _drain_queue(self) -> None:
        while True:
            try:
                entry = self._queue.get_nowait()
            except queue.Empty:
                return
            self._storage.add(entry)

    def _update_stats(self, entry: LogEntry) -> None:
        with self._stats_lock:
            self._events_by_level[entry.level] = self._events_by_level.get(entry.level, 0) + 1
            if entry.level in {"ERROR", "CRITICAL"}:
                self._errors += 1
            elif entry.level == "WARNING":
                self._warnings += 1

    def _check_alerts(self, entry: LogEntry) -> None:
        if entry.level in {"ERROR", "CRITICAL"}:
            self._consecutive_errors += 1
            if entry.level == "CRITICAL":
                self._create_alert("CRITICAL", "Kritiskt fel", entry.message)
            elif self._consecutive_errors >= self.CONSECUTIVE_ERROR_THRESHOLD:
                self._create_alert("WARNING", f"{self._consecutive_errors} fel i rad", entry.message[:160])
        else:
            self._consecutive_errors = 0

    def _create_alert(self, level: str, title: str, message: str) -> None:
        with self._alerts_lock:
            self._alert_counter += 1
            self._alerts.append({
                "id": self._alert_counter,
                "timestamp": datetime.now(),
                "level": level,
                "title": title,
                "message": message,
                "acknowledged": False,
            })
        self._signals.alert_raised.emit(title)

    # ── Heartbeat (v8.9.0) ────────────────────────────────────────────────

    def _start_heartbeat(self) -> None:
        """Periodisk [HEARTBEAT]-logg: bevisar liv och ger AI:n en tidsserie."""
        if os.environ.get("INTYGG_LOG_HEARTBEAT", "1") == "0":
            self._safe_log("debug", "[HEARTBEAT] avstängd via INTYGG_LOG_HEARTBEAT=0")
            return
        if self._view is None:
            return
        self._heartbeat_timer = QTimer(self._view)
        self._heartbeat_timer.setInterval(_HEARTBEAT_MS)
        self._heartbeat_timer.timeout.connect(self._beat)
        self._heartbeat_timer.start()
        self._safe_log("debug", "[HEARTBEAT] aktiv var 5:e minut")

    def _beat(self) -> None:
        self._heartbeats += 1
        dropped = self._interceptor.dropped if self._interceptor else 0
        self._safe_log(
            "info",
            f"[HEARTBEAT] levande: events={self._storage.count} fel={self._errors} "
            f"var={self._warnings} intyg={self._certificates_generated} "
            f"fs={self._fs_operations} dropp={dropped}",
        )

    # ── EventBus och FS ───────────────────────────────────────────────────

    def _on_file_operation(self, operation: FileOperation) -> None:
        self._fs_operations += 1
        self._safe_log(
            "debug",
            f"[FS] {operation.operation:<8} {operation.plugin or '?':>12} "
            f"{operation.caller:<20} {operation.path}"
            + (f" — {operation.detail}" if operation.detail else ""),
        )
        if self._view is not None:
            self._view.fs_view.add_operation(operation)

    def _on_event(self, event_name: str, data: Any = None) -> None:
        parts: list[str] = []
        if isinstance(data, dict):
            for key in ("template", "number", "count", "filename", "error", "key", "title", "message", "source"):
                if data.get(key) is not None:
                    value = str(data[key])
                    parts.append(f"{key}={value[:60]!r}")
        message = f"[EVENT] {event_name}" + (f"\n  ↳ {', '.join(parts)}" if parts else "")
        if any(token in event_name.casefold() for token in ("error", "failed", "crash")):
            self._safe_log(
                "warning", message, extra={"_intygsdrive_error_event": True}
            )
            self._emit("logging.error_logged", {"event": event_name, "timestamp": datetime.now().isoformat(timespec="seconds")})
        elif any(token in event_name for token in ("generated", "completed", "started", "imported")):
            self._safe_log("info", message)
        else:
            self._safe_log("debug", message)
        if event_name == "certificate.generated":
            with self._stats_lock:
                self._certificates_generated += 1

    def _emit(self, event_name: str, data: dict[str, Any]) -> None:
        emitter = getattr(self._context, "emit_event", None) if self._context is not None else None
        if callable(emitter):
            with contextlib.suppress(Exception):
                emitter(event_name, data)
                return
        emitter = getattr(self._event_bus, "emit", None) if self._event_bus is not None else None
        if callable(emitter):
            with contextlib.suppress(Exception):
                emitter(event_name, data)

    # ── Crashhantering ────────────────────────────────────────────────────

    def _install_crash_handlers(self) -> None:
        if self._hooks_installed:
            return
        self._previous_exception_hook = sys.excepthook
        self._previous_thread_exception_hook = threading.excepthook
        sys.excepthook = self._on_unhandled_exception
        threading.excepthook = self._on_thread_exception
        atexit.register(self._on_exit)
        self._hooks_installed = True
        with contextlib.suppress(Exception):
            signal.signal(signal.SIGTERM, self._on_signal)
            signal.signal(signal.SIGINT, self._on_signal)

    def _restore_crash_handlers(self) -> None:
        if not self._hooks_installed:
            return
        if self._previous_exception_hook is not None:
            sys.excepthook = self._previous_exception_hook
        if self._previous_thread_exception_hook is not None:
            threading.excepthook = self._previous_thread_exception_hook
        with contextlib.suppress(Exception):
            atexit.unregister(self._on_exit)
        self._hooks_installed = False

    def _on_exit(self) -> None:
        # FileHandlers write synchronously; stopping the in-memory UI queue is enough.
        self._running = False

    def _on_unhandled_exception(self, exc_type: type[BaseException], exc_value: BaseException, exc_traceback: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            if self._previous_exception_hook:
                self._previous_exception_hook(exc_type, exc_value, exc_traceback)
            return
        trace = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        self._safe_log("critical", f"[CRASH] {exc_type.__name__}:\n{trace}")
        self._emit("logging.crash_detected", {"type": exc_type.__name__, "message": str(exc_value), "timestamp": datetime.now().isoformat(timespec="seconds")})

    def _on_thread_exception(self, args: "threading.ExceptHookArgs") -> None:
        if args.exc_type is None or issubclass(args.exc_type, SystemExit):
            return
        trace = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        self._safe_log("error", f"[CRASH] Tråd {args.thread}: {args.exc_type.__name__}\n{trace}")

    def _on_signal(self, signum: int, _frame: Any) -> None:
        with contextlib.suppress(Exception):
            self._safe_log("info", f"[LOGGER] Signal: {signal.Signals(signum).name}")

    # ── Session och UI ────────────────────────────────────────────────────

    def _log_session_start(self) -> None:
        self._safe_log("info", "\n".join(("=" * 60, "[START] SESSION START", f"Session ID: {self.session_id}", f"Plattform: {sys.platform}", f"Python: {sys.version.split()[0]}", "=" * 60)))

    def _log_session_end(self) -> None:
        duration = str(datetime.now() - self.session_start).split(".")[0]
        stats = self.get_statistics()
        self._safe_log("info", "\n".join(("=" * 60, "[END] SESSION END", f"Varaktighet: {duration}", f"Events: {stats['total_events']}  Fel: {stats['errors']}  Varningar: {stats['warnings']}", f"Intyg: {stats['certificates_generated']}", "=" * 60)))

    @Slot()
    def _refresh_view(self) -> None:
        if self._view is None:
            return
        filters = self._view.filters()
        levels: list[str] | None = None
        level = str(filters["level"])
        quick = filters["quick_filter"]
        if level != "ALL":
            levels = [level]
        elif quick is QuickFilter.ERRORS_ONLY:
            levels = ["ERROR", "CRITICAL"]
        elif quick is QuickFilter.WARNINGS_UP:
            levels = ["WARNING", "ERROR", "CRITICAL"]
        now = datetime.now()
        start_time: datetime | None = None
        if quick is QuickFilter.LAST_HOUR:
            start_time = now - timedelta(hours=1)
        elif quick is QuickFilter.LAST_24H:
            start_time = now - timedelta(hours=24)
        elif quick is QuickFilter.TODAY:
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
        entries = self._storage.search(
            query=str(filters["query"]), levels=levels, start_time=start_time,
            bookmarked_only=quick is QuickFilter.BOOKMARKED,
            use_regex=bool(filters["use_regex"]), limit=2_000,
        )
        self._view.set_entries(entries)
        self._update_view_stats()
        self._update_view_alerts()
        self._view.set_status(f"Visar {len(entries):,}/{self._storage.count:,} │ Fel: {self._errors} │ Var: {self._warnings} │ Intyg: {self._certificates_generated}")

    @Slot()
    def _update_view_stats(self) -> None:
        if self._view is None:
            return
        storage_stats = self._storage.get_stats()
        with self._stats_lock:
            lines = [
                "📊 SESSION STATISTIK", "═" * 34, "",
                f"Start:    {self.session_start:%Y-%m-%d %H:%M:%S}",
                f"Tid:      {storage_stats.get_duration_str()}",
                f"Events:   {storage_stats.total_events:,}",
                f"Intyg:    {self._certificates_generated}",
                f"Ev/min:   {storage_stats.get_events_per_minute():.1f}", "", "NIVÅER", "─" * 34,
            ]
            for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
                lines.append(f"{level:<10} {storage_stats.events_by_level.get(level, 0):>6}")
            lines.extend(["", f"Fel:      {self._errors}", f"Var.:     {self._warnings}", f"Lagrat:   {self._storage.count:,}"])
        self._view.set_stats_text("\n".join(lines))

    def _update_view_alerts(self) -> None:
        if self._view is None:
            return
        with self._alerts_lock:
            alerts = list(self._alerts)
            unacknowledged = sum(not bool(alert["acknowledged"]) for alert in alerts)
        self._view.set_alerts(alerts)
        self._view.set_alert_count(unacknowledged)

    def _set_live_mode(self, enabled: bool) -> None:
        self._live_mode = enabled
        self._safe_log("debug", f"[UI] live-läge: {enabled}")
        if enabled:
            self._refresh_view()

    def _set_detailed_mode(self, enabled: bool) -> None:
        self._detailed_mode = enabled
        self._safe_log("debug", f"[UI] detaljläge: {enabled}")
        self._refresh_view()

    def _clear_display(self) -> None:
        self._safe_log("debug", "[UI] display rensad")
        if self._view is not None:
            self._view.log_display.clear()

    def _on_alert_raised(self, _title: str) -> None:
        self._update_view_alerts()

    def _activate_alert(self, alert_id: int) -> None:
        with self._alerts_lock:
            alert = next((item for item in self._alerts if item["id"] == alert_id), None)
            if alert is not None:
                alert["acknowledged"] = True
        if alert is not None:
            self._safe_log("debug", f"[ALERT] bekräftad #{alert_id} ({alert['title']})")
            QMessageBox.information(self._view, str(alert["title"]), f"Tid: {alert['timestamp']:%H:%M:%S}\nNivå: {alert['level']}\n\n{alert['message']}")
        self._update_view_alerts()

    def _on_bookmark_requested(self, entry_id: int, value: bool) -> None:
        """9.2.0: persistera bokmärke i LogStorage och uppdatera vyn."""
        try:
            self._storage.set_bookmarked(int(entry_id), bool(value))
            self._refresh_view()
        except Exception:
            self._safe_log("debug", "[LOGGING] bokmärke misslyckades", exc_info=True)

    def _acknowledge_all(self) -> None:
        with self._alerts_lock:
            for alert in self._alerts:
                alert["acknowledged"] = True
        self._safe_log("debug", "[ALERT] alla bekräftade")
        self._update_view_alerts()

    def _open_log_folder(self) -> None:
        if self.log_dir is None:
            return
        self._safe_log("debug", f"[UI] öppnar loggmapp: {self.log_dir}")
        try:
            if sys.platform == "win32":
                startfile = getattr(os, "startfile", None)
                if callable(startfile):
                    startfile(str(self.log_dir))
                    return
                raise RuntimeError("Windows startfile är inte tillgänglig")
            command = ["open", str(self.log_dir)] if sys.platform == "darwin" else ["xdg-open", str(self.log_dir)]
            import subprocess
            subprocess.Popen(command, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as exc:
            _logger.exception("[LOGGING] Kunde inte öppna loggmapp")
            QMessageBox.warning(self._view, "Kunde inte öppna loggmapp", str(exc))

    def _show_alerts(self) -> None:
        with self._alerts_lock:
            total = len(self._alerts)
            unacknowledged = sum(not bool(alert["acknowledged"]) for alert in self._alerts)
        self._safe_log("debug", f"[UI] visar alerts: {total} totalt, {unacknowledged} obekräftade")
        QMessageBox.information(self._view, "Alerts", f"Totalt: {total}\nObekräftade: {unacknowledged}")

    def _export(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self._view,
            "Exportera logg",
            f"logg_{self.session_id[:8]}_{datetime.now():%Y%m%d_%H%M%S}",
            "Text (*.txt);;JSON (*.json);;HTML (*.html);;CSV (*.csv);;Markdown (*.md)",
        )
        if not selected:
            self._safe_log("debug", "[UI] export avbruten")
            return
        try:
            entries = self._storage.get_all()
            stats = self._storage.get_stats()
            extension = Path(selected).suffix.casefold()
            exporters: dict[str, Callable[[], None]] = {
                ".json": lambda: export_json(selected, entries, self.session_id, stats),
                ".html": lambda: export_html(selected, entries, self.session_id, stats),
                ".csv": lambda: export_csv(selected, entries),
                ".md": lambda: export_markdown(selected, entries, self.session_id, stats),
            }
            exporters.get(extension, lambda: export_text(selected, entries, self.session_id, stats))()
            self._safe_log("info", f"[UI] exporterade {len(entries)} poster → {selected} ({extension or '.txt'})")
            QMessageBox.information(self._view, "Export klar", f"✅ {len(entries)} poster exporterade")
        except Exception as exc:
            _logger.exception("[LOGGING] Export misslyckades")
            QMessageBox.critical(self._view, "Exportfel", str(exc))

    # ── AI-rapport (v8.9.0, 10000% felsökning) ────────────────────────────

    def ai_report(self, target: str | Path | None = None, extra: str = "") -> Path | None:
        """Skriver en AI-anpassad felsökningsrapport (Markdown) och returnerar sökvägen.

        Samlar hela sessionens tillstånd i EN fil: statistik, nivåer, alerts,
        fs-sammanfattning, droppar/skrivfel/stegfel och de senaste posterna —
        redo att klistras in i en AI för analys.
        """
        if self.log_dir is None:
            return None
        path = Path(target) if target else self.log_dir / "ai_report.md"
        stats = self.get_statistics()
        dropped = self._interceptor.dropped if self._interceptor else 0
        lines: list[str] = [
            "# IntygsDrive — AI-felsökningsrapport (Logger v8.9.0)",
            "",
            f"Genererad: {datetime.now().isoformat(timespec='seconds')}",
            f"Session: `{self.session_id}` (start {stats['session_start']}, varaktighet {stats['duration_str']})",
            "",
            "## Miljö",
            f"- Python: {sys.version.split()[0]} · Plattform: {sys.platform}",
            f"- Loggkatalog: `{self.log_dir}` · arkiv: {stats['archived_files']} fil(er)",
            f"- Aktiv logg: `{stats['log_file']}`",
            f"- Samlad fellogg: `{stats['errors_file']}`",
            "",
            "## Statistik",
            f"- Events: {stats['total_events']} ({stats['events_per_minute']}/min) · lagrade: {stats['storage_count']}",
            f"- Fel: {stats['errors']} · Varningar: {stats['warnings']} · Intyg: {stats['certificates_generated']}",
            "- Nivåer: " + ", ".join(f"{k}={v}" for k, v in sorted(stats['by_level'].items())),
            f"- Alerts: {stats['alerts']} ({stats['unacknowledged_alerts']} obekräftade)",
            "",
            "## Hälsa / tysta förluster",
            f"- Interceptor-droppar (kö full): {dropped}",
            "- Loggfilslayout: app.log innehåller allt; errors.log innehåller endast fel.",
            f"- on_load-stegfel: {self._step_failures}",
            f"- Heartbeats: {self._heartbeats} · fs-operationer: {self._fs_operations}",
            "",
            "## Filsystemsspårning",
            "```json",
            json.dumps(stats.get("fs_operations", {}), ensure_ascii=False, default=str, indent=2),
            "```",
            "",
            f"## Senaste {min(30, len(self._storage.get_recent(30)))} poster",
            "```json",
            json.dumps(self.get_recent_entries(30), ensure_ascii=False, default=str, indent=2),
            "```",
        ]
        with self._alerts_lock:
            if self._alerts:
                lines += ["", "## Alerts"]
                for alert in list(self._alerts)[-10:]:
                    stamp = alert["timestamp"]
                    lines.append(
                        f"- [{getattr(stamp, 'isoformat', lambda: str(stamp))()}] "
                        f"{alert['level']} {alert['title']}: {alert['message']} "
                        f"(bekräftad={alert['acknowledged']})"
                    )
        if extra:
            lines += ["", "## Extra kontext", extra]
        lines += ["", "---", "_Stabila rubriker och JSON-block: maskinläsbar för AI._"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            self._safe_log("info", f"[AI] rapport skriven: {path}")
            return path
        except OSError:
            _logger.exception("[LOGGING] Kunde inte skriva AI-rapport")
            return None

    def diagnose(self) -> dict[str, Any]:
        """Returnerar pluginets kompletta tillstånd som dict (för AI/utvecklare)."""
        return self.get_statistics() | {
            "version": self.version,
            "interceptor_dropped": self._interceptor.dropped if self._interceptor else 0,
            "step_failures": self._step_failures,
            "heartbeats": self._heartbeats,
            "fs_operations_total": self._fs_operations,
            "running": self._running,
            "loaded": self._loaded,
            "live_mode": self._live_mode,
            "detailed_mode": self._detailed_mode,
        }

    # ── Publikt API ───────────────────────────────────────────────────────

    def get_statistics(self) -> dict[str, Any]:
        storage_stats = self._storage.get_stats()
        with self._alerts_lock:
            alerts = len(self._alerts)
            unacknowledged = sum(not bool(alert["acknowledged"]) for alert in self._alerts)
        fs_summary = self._view.fs_view.get_summary() if self._view is not None else {}
        archived_files = 0
        if self.archive_dir is not None and self.archive_dir.is_dir():
            with contextlib.suppress(OSError):
                archived_files = sum(1 for path in self.archive_dir.iterdir() if path.is_file())
        return {
            "total_events": storage_stats.total_events,
            "errors": storage_stats.errors,
            "warnings": storage_stats.warnings,
            "certificates_generated": self._certificates_generated,
            "events_per_minute": round(storage_stats.get_events_per_minute(), 2),
            "by_level": dict(storage_stats.events_by_level),
            "alerts": alerts,
            "unacknowledged_alerts": unacknowledged,
            "session_id": self.session_id,
            "session_start": self.session_start.isoformat(timespec="seconds"),
            "duration_str": storage_stats.get_duration_str(),
            "storage_count": self._storage.count,
            "log_file": str(self.current_log_file or ""),
            "errors_file": str(self.error_log_file or ""),
            "log_dir": str(self.log_dir or ""),
            "archive_dir": str(self.archive_dir or ""),
            "archived_files": archived_files,
            "fs_operations": fs_summary,
        }

    def get_recent_entries(self, count: int = 50) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self._storage.get_recent(count)]

    def log(self, level: str, message: str, *args: Any) -> None:
        text = message % args if args else message
        self._safe_log(level.casefold(), text)

    def _safe_log(
        self, level: str, message: str, *, extra: dict[str, Any] | None = None
    ) -> None:
        logger = self._root_logger
        if logger is not None:
            method = getattr(logger, level, logger.info)
            try:
                method(message, extra=extra) if extra else method(message)
                return
            except Exception:
                _logger.debug("[LOGGING] Rootloggning misslyckades", exc_info=True)
        try:
            print(f"[{level.upper()}] {message}", file=sys.stderr)
        except Exception:
            return

    def _close_handlers(self) -> None:
        root = self._root_logger
        python_root = self._python_root_logger
        owned = {
            handler for handler in
            (self._general_handler, self._error_handler, self._interceptor)
            if handler is not None
        }
        for handler in owned:
            if root is not None:
                root.removeHandler(handler)
            if python_root is not None:
                python_root.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.flush()
                handler.close()
        if root is not None:
            for handler in list(root.handlers):
                root.removeHandler(handler)
                with contextlib.suppress(Exception):
                    handler.flush()
                    handler.close()
        if python_root is not None and self._previous_python_root_level is not None:
            python_root.setLevel(self._previous_python_root_level)
        self._general_handler = None
        self._error_handler = None
        self._interceptor = None
        self._previous_python_root_level = None
        self._root_logger = None
        self._python_root_logger = None
