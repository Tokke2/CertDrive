# -*- coding: utf-8 -*-
from __future__ import annotations

# core/infrastructure.py
# Intygsgenerator — Centrala infrastrukturkomponenter:
# plattformsdetektering, loggning, atomisk filskrivning,
# skärmhantering via PySide6, hjälpfunktioner och dekoratorer.
#
# Version: 9.2.0 (uppgraderad från 9.1.0, 2026-09-21)
# © Rickard Längkvist 2025–2026
#
# logging_plugin äger all sessionsinformation och startloggning. Denna modul
# tillhandahåller endast återanvändbara infrastrukturkomponenter.
#
# Uppgradering 9.1.0 → 9.2.0 ("188888888% bättre", endast denna fil)
# ================================================================
# 1. JSONLFormatter inkluderar nu `event` och `ctx` (appens AI-kontrakt) —
#    NDJSON blir fullt AI-analyserbar även via infrastruktur-loggern.
# 2. AtomicFileWriter: borttagen Windows-unlink-race — os.replace är atomärt
#    på alla plattformar; skrivfel loggas infra.atomic_write_failed.
# 3. infra.*-events: log_rollover, log_compressed, display_detected (ms+källa),
#    atomic_write_failed — allt synligt i centralloggen.
# 4. Delad `scale_px()` (fontmetrik/DPI) för hela appen (§17/§30).
# 5. All övrig funktionalitet oförändrad (MUST-005).

import gzip
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum, auto
from functools import lru_cache, wraps
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterator

__version__: str = "9.2.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

APP_NAME: str = "Intygsgenerator"
APP_DISPLAY_NAME: str = "Intygs Generator"
VERSION: str = __version__

FOLDER_ICONS: str = "Icon"
FOLDER_TEMPLATES: str = "Mallar"
FOLDER_OUTPUT: str = "Out"
FOLDER_CONFIG: str = "js"
FOLDER_PLUGINS: str = "plugins"
FOLDER_LOGS: str = "logs"
FOLDER_BACKUP: str = "Backup"
FOLDER_SENT: str = "Skickade"

DEFAULT_WINDOW_WIDTH: int = 1100
DEFAULT_WINDOW_HEIGHT: int = 750
MIN_WINDOW_WIDTH: int = 800
MIN_WINDOW_HEIGHT: int = 600

LOG_MAX_BYTES: int = 10 * 1024 * 1024
LOG_BACKUP_COUNT: int = 15
LOG_FORMAT: str = (
    "%(asctime)s | %(levelname)-8s | [%(session)s] | "
    "%(threadName)-15s | %(name)s | %(message)s"
)
LOG_DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"
LOG_CONSOLE_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(message)s"

SESSION_ID: str = uuid.uuid4().hex[:12]
PathLike = str | Path

_logger = logging.getLogger(__name__)


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


# ─── Plattforms-enum ────────────────────────────────────────────────────────


class PlatformType(Enum):
    """Operativsystem-enum."""

    WINDOWS = auto()
    MACOS = auto()
    LINUX = auto()
    UNKNOWN = auto()

    @classmethod
    def current(cls) -> PlatformType:
        platform_key = sys.platform.lower()
        if platform_key in {"win32", "cygwin"}:
            return cls.WINDOWS
        if platform_key == "darwin":
            return cls.MACOS
        if platform_key.startswith("linux"):
            return cls.LINUX
        return cls.UNKNOWN

    @property
    def is_windows(self) -> bool:
        return self == PlatformType.WINDOWS

    @property
    def is_macos(self) -> bool:
        return self == PlatformType.MACOS

    @property
    def is_linux(self) -> bool:
        return self == PlatformType.LINUX

    @property
    def is_unix(self) -> bool:
        return self in (PlatformType.MACOS, PlatformType.LINUX)

    @property
    def display_name(self) -> str:
        names = {
            PlatformType.WINDOWS: "Windows",
            PlatformType.MACOS: "macOS",
            PlatformType.LINUX: "Linux",
            PlatformType.UNKNOWN: "Unknown",
        }
        return names.get(self, "Unknown")

    @property
    def name_display(self) -> str:
        return self.display_name

    @property
    def emoji(self) -> str:
        emojis = {
            PlatformType.WINDOWS: "🪟",
            PlatformType.MACOS: "🍎",
            PlatformType.LINUX: "🐧",
            PlatformType.UNKNOWN: "❓",
        }
        return emojis.get(self, "❓")

    @property
    def icon_extension(self) -> str:
        extensions = {
            PlatformType.WINDOWS: ".ico",
            PlatformType.MACOS: ".icns",
            PlatformType.LINUX: ".png",
        }
        return extensions.get(self, ".png")

    @property
    def default_font(self) -> str:
        fonts = {
            PlatformType.WINDOWS: "Segoe UI",
            PlatformType.MACOS: "SF Pro Text",
            PlatformType.LINUX: "Ubuntu",
        }
        return fonts.get(self, "Helvetica")

    @property
    def monospace_font(self) -> str:
        fonts = {
            PlatformType.WINDOWS: "Consolas",
            PlatformType.MACOS: "SF Mono",
            PlatformType.LINUX: "Ubuntu Mono",
        }
        return fonts.get(self, "Courier")


Platform = PlatformType
CURRENT_PLATFORM: PlatformType = PlatformType.current()


# ─── Loggnivåer och åtgärds-enum ────────────────────────────────────────────


class LogLevel(IntEnum):
    """Loggnivåer med utökad TRACE-nivå."""

    TRACE = 5
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    @classmethod
    def from_string(cls, level: str) -> LogLevel:
        mapping: dict[str, LogLevel] = {
            "trace": cls.TRACE,
            "debug": cls.DEBUG,
            "info": cls.INFO,
            "warning": cls.WARNING,
            "warn": cls.WARNING,
            "error": cls.ERROR,
            "critical": cls.CRITICAL,
            "fatal": cls.CRITICAL,
        }
        return mapping.get(str(level).lower(), cls.INFO)

    @property
    def color_code(self) -> str:
        colors = {
            LogLevel.TRACE: "\033[90m",
            LogLevel.DEBUG: "\033[36m",
            LogLevel.INFO: "\033[32m",
            LogLevel.WARNING: "\033[33m",
            LogLevel.ERROR: "\033[31m",
            LogLevel.CRITICAL: "\033[35;1m",
        }
        return colors.get(self, "")

    @property
    def emoji(self) -> str:
        emojis = {
            LogLevel.TRACE: "🔬",
            LogLevel.DEBUG: "🔍",
            LogLevel.INFO: "ℹ️",
            LogLevel.WARNING: "⚠️",
            LogLevel.ERROR: "❌",
            LogLevel.CRITICAL: "🚨",
        }
        return emojis.get(self, "📝")


class CloseAction(Enum):
    """Åtgärd vid fönsterstängning."""

    MINIMIZE_TO_TRAY = auto()
    CLOSE_TO_TRAY = auto()
    EXIT_APPLICATION = auto()
    ASK_USER = auto()

    @property
    def description(self) -> str:
        descriptions = {
            CloseAction.MINIMIZE_TO_TRAY: "Minimera till systemfältet",
            CloseAction.CLOSE_TO_TRAY: "Dölj till systemfältet",
            CloseAction.EXIT_APPLICATION: "Avsluta programmet",
            CloseAction.ASK_USER: "Fråga varje gång",
        }
        return descriptions.get(self, "Okänd")


class StartupMode(Enum):
    """Startläge för applikationen."""

    NORMAL = auto()
    MINIMIZED = auto()
    TRAY_ONLY = auto()

    @property
    def description(self) -> str:
        descriptions = {
            StartupMode.NORMAL: "Visa fönster",
            StartupMode.MINIMIZED: "Minimerad",
            StartupMode.TRAY_ONLY: "Endast systemfält",
        }
        return descriptions.get(self, "Okänd")


class FileOperation(Enum):
    """Filoperationstyp."""

    CREATE = auto()
    UPDATE = auto()
    DELETE = auto()
    BACKUP = auto()
    MOVE = auto()
    COPY = auto()


# ─── Dataklasser ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Systeminformation."""

    platform: PlatformType
    platform_name: str
    platform_release: str
    platform_version: str
    architecture: str
    python_version: str
    app_version: str
    is_frozen: bool
    is_admin: bool
    app_path: Path
    session_id: str
    cpu_count: int = field(default_factory=lambda: os.cpu_count() or 1)
    hostname: str = field(default_factory=platform.node)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform.name,
            "platform_name": self.platform_name,
            "platform_release": self.platform_release,
            "platform_version": self.platform_version,
            "architecture": self.architecture,
            "python_version": self.python_version,
            "app_version": self.app_version,
            "is_frozen": self.is_frozen,
            "is_admin": self.is_admin,
            "app_path": str(self.app_path),
            "session_id": self.session_id,
            "cpu_count": self.cpu_count,
            "hostname": self.hostname,
        }

    def __str__(self) -> str:
        return f"{self.platform_name} {self.platform_release} ({self.architecture})"


@dataclass(slots=True)
class DisplayInfo:
    """Skärminformation för dynamisk fönsterstorlek via PySide6."""

    width: int
    height: int
    dpi: int = 96
    scaling: float = 1.0
    work_area: tuple[int, int, int, int] | None = None

    @property
    def is_hidpi(self) -> bool:
        return self.dpi > 96 or self.scaling > 1.0

    @property
    def size_category(self) -> str:
        if self.width < 1366:
            return "small"
        if self.width < 1920:
            return "medium"
        if self.width < 2560:
            return "large"
        return "xlarge"

    @property
    def scale_factor(self) -> float:
        return self.scaling

    @property
    def recommended_window_size(self) -> tuple[int, int]:
        base_width = self.work_area[2] if self.work_area else self.width
        base_height = self.work_area[3] if self.work_area else self.height
        width = min(int(base_width * 0.70), 1400)
        height = min(int(base_height * 0.75), 900)

        if self.scaling > 1.0:
            width = int(width * min(self.scaling, 1.5))
            height = int(height * min(self.scaling, 1.5))

        width = min(width, int(base_width * 0.90))
        height = min(height, int(base_height * 0.90))
        return max(width, MIN_WINDOW_WIDTH), max(height, MIN_WINDOW_HEIGHT)

    @property
    def min_window_size(self) -> tuple[int, int]:
        scale = min(self.scaling, 1.0)
        return int(MIN_WINDOW_WIDTH * scale), int(MIN_WINDOW_HEIGHT * scale)

    @property
    def center_position(self) -> tuple[int, int]:
        width, height = self.recommended_window_size
        if self.work_area:
            base_x, base_y, base_width, base_height = self.work_area
            return (
                base_x + (base_width - width) // 2,
                base_y + (base_height - height) // 2,
            )
        return (self.width - width) // 2, (self.height - height) // 2

    def calculate_font_size(self, base_size: int = 10) -> int:
        if self.scaling > 1.5:
            return int(base_size * 1.3)
        if self.scaling > 1.25:
            return int(base_size * 1.15)
        if self.is_hidpi:
            return int(base_size * 1.1)
        return base_size


@dataclass(slots=True)
class LogStats:
    """Statistik för infrastruktur-logger."""

    total_logs: int = 0
    error_count: int = 0
    warning_count: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    level_counts: dict[str, int] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def uptime_formatted(self) -> str:
        return format_duration(self.uptime_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_logs": self.total_logs,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "uptime_formatted": self.uptime_formatted,
            "level_counts": dict(self.level_counts),
            "start_time": self.start_time.isoformat(),
        }


# ─── Atomisk filskrivning ───────────────────────────────────────────────────


class AtomicFileWriter:
    """Atomisk filskrivning med backup-stöd."""

    def __init__(
        self,
        path: PathLike,
        mode: str = "w",
        encoding: str = "utf-8",
        backup: bool = True,
        sync: bool = True,
    ) -> None:
        self.path = Path(path)
        self.mode = mode
        self.encoding = encoding if "b" not in mode else ""
        self.backup = backup
        self.sync = sync
        self._temp_path: Path | None = None
        self._file: Any = None

    def __enter__(self) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f".{self.path.stem}_",
            dir=self.path.parent,
        )
        self._temp_path = Path(tmp)
        os.close(fd)

        if self.encoding:
            self._file = open(
                self._temp_path,
                self.mode,
                encoding=self.encoding,
                newline="",
            )
        else:
            self._file = open(self._temp_path, self.mode)
        return self._file

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if self._file:
            try:
                self._file.flush()
                if self.sync and hasattr(os, "fsync"):
                    os.fsync(self._file.fileno())
            finally:
                self._file.close()

        if exc_type is not None:
            self._cleanup_temp()
            return False

        if self._temp_path and self._temp_path.exists():
            try:
                if self.backup and self.path.exists():
                    backup_path = self.path.with_suffix(f"{self.path.suffix}.bak")
                    with suppress(OSError):
                        shutil.copy2(self.path, backup_path)

                # 9.2.0: os.replace är atomärt på alla plattformar — ingen
                # unlink-race på Windows (filen existerar alltid eller aldrig).
                self._temp_path.replace(self.path)
            except Exception as exc:
                _logger.error(
                    "Atomic write failed: %s (%s)", self.path, exc,
                    extra={"event": "infra.atomic_write_failed",
                           "ctx": {"path": str(self.path), "error": str(exc)}},
                )
                self._cleanup_temp()
                raise

        return False

    def _cleanup_temp(self) -> None:
        if self._temp_path and self._temp_path.exists():
            with suppress(OSError):
                self._temp_path.unlink()

    @classmethod
    def write_json(
        cls,
        path: PathLike,
        data: Any,
        indent: int = 2,
        **kwargs: Any,
    ) -> bool:
        try:
            with cls(path, **kwargs) as handle:
                json.dump(
                    data,
                    handle,
                    indent=indent,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
            return True
        except Exception:
            _logger.exception("Atomic JSON write failed: %s", path)
            return False

    @classmethod
    def write_text(cls, path: PathLike, content: str, **kwargs: Any) -> bool:
        try:
            with cls(path, **kwargs) as handle:
                handle.write(content)
            return True
        except Exception:
            _logger.exception("Atomic text write failed: %s", path)
            return False

    @classmethod
    def write_bytes(cls, path: PathLike, content: bytes, **kwargs: Any) -> bool:
        kwargs.pop("encoding", None)
        try:
            with cls(path, mode="wb", encoding="", **kwargs) as handle:
                handle.write(content)
            return True
        except Exception:
            _logger.exception("Atomic bytes write failed: %s", path)
            return False


# ─── Logg-komponenter ───────────────────────────────────────────────────────


class SessionContextFilter(logging.Filter):
    """Filter som lägger till session-ID till alla loggar."""

    def __init__(self, session_id: str = SESSION_ID) -> None:
        super().__init__()
        self.session_id = session_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.session = self.session_id
        return True


class CompressedRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler med gzip-komprimering vid rollover.

    9.4.0: komprimeringen ar SYNKRON (enkelhet/§26). Tidigare asynkron trad gav
    opalitliga testresultat och en trad som kunde lopa vidare efter stangning.
    Rollover sker sallan (storlek/dag) sa kostnaden ar forsumbar.
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = LOG_MAX_BYTES,
        backupCount: int = LOG_BACKUP_COUNT,
        encoding: str = "utf-8",
        compress: bool = True,
        **kwargs: Any,
    ) -> None:
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(
            filename,
            maxBytes=maxBytes,
            backupCount=backupCount,
            encoding=encoding,
            **kwargs,
        )
        self.compress = compress

    def _compress_file(self, filepath: str) -> bool:
        """Komprimera en roterad loggfil till .gz. Returnerar True vid lyckad komprimering."""
        source = Path(filepath)
        if not source.exists() or source.stat().st_size <= 0:
            return False
        gzip_path = Path(f"{filepath}.gz")
        try:
            with open(source, "rb") as input_file:
                with gzip.open(gzip_path, "wb", compresslevel=9) as output_file:
                    shutil.copyfileobj(input_file, output_file)
            source.unlink()
            _logger.debug(
                "Logg komprimerad: %s", gzip_path,
                extra={"event": "infra.log_compressed",
                       "ctx": {"file": str(gzip_path)}},
            )
            return True
        except Exception:
            _logger.exception("Log compression failed: %s", source)
            with suppress(OSError):
                gzip_path.unlink()
            return False

    def doRollover(self) -> None:  # noqa: N802
        if self.stream:
            self.stream.close()
            self.stream = None

        for index in range(self.backupCount - 1, 0, -1):
            for extension in ("", ".gz"):
                source = f"{self.baseFilename}.{index}{extension}"
                destination = f"{self.baseFilename}.{index + 1}{extension}"
                if os.path.exists(source):
                    if os.path.exists(destination):
                        os.remove(destination)
                    os.rename(source, destination)
                    break

        first_backup = f"{self.baseFilename}.1"
        for existing in (first_backup, f"{first_backup}.gz"):
            if os.path.exists(existing):
                os.remove(existing)

        if os.path.exists(self.baseFilename):
            os.rename(self.baseFilename, first_backup)
            if self.compress:
                self._compress_file(first_backup)
            _logger.debug(
                "Logg roterad: %s", self.baseFilename,
                extra={"event": "infra.log_rollover",
                       "ctx": {"file": self.baseFilename, "compress": self.compress}},
            )

        if not self.delay:
            self.stream = self._open()

    def close(self) -> None:
        super().close()


class ColoredFormatter(logging.Formatter):
    """Formatter med ANSI-färger för konsol-output."""

    COLORS: ClassVar[dict[str, str]] = {
        "TRACE": "\033[90m",
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35;1m",
        "RESET": "\033[0m",
    }
    EMOJIS: ClassVar[dict[str, str]] = {
        "TRACE": "🔬",
        "DEBUG": "🔍",
        "INFO": "ℹ️ ",
        "WARNING": "⚠️ ",
        "ERROR": "❌",
        "CRITICAL": "🚨",
    }

    _colors_enabled: ClassVar[bool] = False
    _colors_checked: ClassVar[bool] = False

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        use_emoji: bool = True,
        use_colors: bool = True,
    ) -> None:
        super().__init__(fmt or LOG_CONSOLE_FORMAT, datefmt or "%H:%M:%S")
        self.use_emoji = use_emoji
        self.use_colors = use_colors

        if not ColoredFormatter._colors_checked:
            ColoredFormatter._colors_checked = True
            ColoredFormatter._colors_enabled = self._enable_colors()

    @staticmethod
    def _enable_colors() -> bool:
        if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
            return False

        if CURRENT_PLATFORM.is_windows:
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetStdHandle(-11)
                mode = ctypes.c_ulong()
                kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
            except Exception:
                return os.environ.get("TERM") is not None

        return True

    @classmethod
    def supports_color(cls) -> bool:
        if not cls._colors_checked:
            cls._colors_checked = True
            cls._colors_enabled = cls._enable_colors()
        return cls._colors_enabled

    def format(self, record: logging.LogRecord) -> str:
        copied = logging.makeLogRecord(record.__dict__)

        if self.use_emoji:
            emoji = self.EMOJIS.get(copied.levelname, "")
            if emoji:
                copied.msg = f"{emoji} {copied.msg}"

        message = super().format(copied)

        if self.use_colors and ColoredFormatter._colors_enabled:
            color = self.COLORS.get(copied.levelname, "")
            if color:
                return f"{color}{message}{self.COLORS['RESET']}"

        return message


class JSONLFormatter(logging.Formatter):
    """Formatter för JSON Lines.

    9.2.0: inkluderar appens AI-kontraktfält `event` och `ctx` så att NDJSON
    från infrastruktur-loggern är lika analyserbar som logging_pluginets.
    """

    def __init__(self, session_id: str = SESSION_ID) -> None:
        super().__init__()
        self.session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session": self.session_id,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread": record.threadName,
            "process": record.process,
        }

        event = getattr(record, "event", None)
        if event:
            entry["event"] = event
        ctx = getattr(record, "ctx", None)
        if ctx:
            entry["ctx"] = ctx

        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_data"):
            entry["data"] = record.extra_data

        return json.dumps(entry, ensure_ascii=False, default=str)


class AdvancedLogger:
    """Avancerad logger-fabrik utan startrubriker."""

    _instance: ClassVar[AdvancedLogger | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _loggers: ClassVar[dict[str, logging.Logger]] = {}
    _initialized: ClassVar[bool] = False

    TRACE_LEVEL: ClassVar[int] = 5

    def __init__(
        self,
        log_dir: Path,
        name: str = APP_NAME,
        level: int = logging.DEBUG,
        enable_console: bool = True,
        enable_json: bool = True,
        enable_daily: bool = True,
        compress: bool = True,
    ) -> None:
        if not AdvancedLogger._initialized:
            logging.addLevelName(self.TRACE_LEVEL, "TRACE")
            AdvancedLogger._initialized = True

        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self._level = level

        self._logger = logging.getLogger(name)
        self._logger.setLevel(self.TRACE_LEVEL)
        self._logger.propagate = False

        for handler in self._logger.handlers[:]:
            with suppress(Exception):
                handler.close()
            self._logger.removeHandler(handler)

        self._session_filter = SessionContextFilter(SESSION_ID)
        self._stats = LogStats()
        self._stats_lock = threading.Lock()
        self._handlers: list[logging.Handler] = []

        self._setup_handlers(enable_console, enable_json, enable_daily, compress)
        AdvancedLogger._loggers[name] = self._logger

    def _setup_handlers(
        self,
        enable_console: bool,
        enable_json: bool,
        enable_daily: bool,
        compress: bool,
    ) -> None:
        def add_handler(
            handler: logging.Handler,
            level: int,
            formatter: logging.Formatter,
        ) -> None:
            handler.setLevel(level)
            handler.setFormatter(formatter)
            handler.addFilter(self._session_filter)
            self._logger.addHandler(handler)
            self._handlers.append(handler)

        try:
            add_handler(
                CompressedRotatingFileHandler(
                    str(self.log_dir / f"{self.name.lower()}.log"),
                    maxBytes=LOG_MAX_BYTES,
                    backupCount=LOG_BACKUP_COUNT,
                    compress=compress,
                ),
                self.TRACE_LEVEL,
                logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT),
            )
        except Exception as exc:
            print(f"[INFRA] main log handler: {exc}", file=sys.stderr)

        if enable_daily:
            try:
                daily = TimedRotatingFileHandler(
                    str(self.log_dir / f"{self.name.lower()}_daily.log"),
                    when="midnight",
                    backupCount=30,
                    encoding="utf-8",
                )
                daily.suffix = "%Y-%m-%d"
                add_handler(
                    daily,
                    self.TRACE_LEVEL,
                    logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT),
                )
            except Exception as exc:
                print(f"[INFRA] daily log handler: {exc}", file=sys.stderr)

        if enable_console:
            try:
                add_handler(
                    logging.StreamHandler(sys.stdout),
                    logging.INFO,
                    ColoredFormatter(),
                )
            except Exception as exc:
                print(f"[INFRA] console handler: {exc}", file=sys.stderr)

        if enable_json:
            try:
                add_handler(
                    CompressedRotatingFileHandler(
                        str(self.log_dir / f"{self.name.lower()}.jsonl"),
                        maxBytes=LOG_MAX_BYTES,
                        backupCount=5,
                        compress=compress,
                    ),
                    self.TRACE_LEVEL,
                    JSONLFormatter(),
                )
            except Exception as exc:
                print(f"[INFRA] JSON handler: {exc}", file=sys.stderr)

    @classmethod
    def setup(
        cls,
        log_dir: Path,
        name: str = APP_NAME,
        **kwargs: Any,
    ) -> logging.Logger:
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(log_dir, name, **kwargs)
            return cls._instance._logger

    @classmethod
    def get_instance(cls) -> AdvancedLogger | None:
        return cls._instance

    @classmethod
    def get_logger(cls, name: str | None = None) -> logging.Logger:
        with cls._lock:
            if name in cls._loggers:
                return cls._loggers[name]

            if cls._instance:
                if name:
                    child = cls._instance._logger.getChild(name)
                    cls._loggers[name] = child
                    return child
                return cls._instance._logger

        logger = logging.getLogger(name or APP_NAME)
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(LOG_CONSOLE_FORMAT, "%H:%M:%S"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

        with cls._lock:
            cls._loggers[name or APP_NAME] = logger
        return logger

    @classmethod
    def get_log_dir(cls) -> Path | None:
        return cls._instance.log_dir if cls._instance else None

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    @property
    def level(self) -> int:
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        self._level = value
        self._logger.setLevel(value)

    def _update_stats(self, level: int) -> None:
        with self._stats_lock:
            self._stats.total_logs += 1
            level_name = logging.getLevelName(level)
            self._stats.level_counts[level_name] = (
                self._stats.level_counts.get(level_name, 0) + 1
            )
            if level >= logging.ERROR:
                self._stats.error_count += 1
            elif level == logging.WARNING:
                self._stats.warning_count += 1

    def trace(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._update_stats(self.TRACE_LEVEL)
        self._logger.log(self.TRACE_LEVEL, msg, *args, **kwargs)

    def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._update_stats(logging.DEBUG)
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._update_stats(logging.INFO)
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._update_stats(logging.WARNING)
        self._logger.warning(msg, *args, **kwargs)

    def error(
        self,
        msg: Any,
        *args: Any,
        exc_info: bool = False,
        **kwargs: Any,
    ) -> None:
        self._update_stats(logging.ERROR)
        self._logger.error(msg, *args, exc_info=exc_info, **kwargs)

    def critical(
        self,
        msg: Any,
        *args: Any,
        exc_info: bool = True,
        **kwargs: Any,
    ) -> None:
        self._update_stats(logging.CRITICAL)
        self._logger.critical(msg, *args, exc_info=exc_info, **kwargs)

    def exception(self, msg: Any, *args: Any, **kwargs: Any) -> None:
        self.error(msg, *args, exc_info=True, **kwargs)

    def log(self, level: int, msg: Any, *args: Any, **kwargs: Any) -> None:
        self._update_stats(level)
        self._logger.log(level, msg, *args, **kwargs)

    @contextmanager
    def timer(
        self,
        operation: str,
        warn_threshold: float = 1.0,
    ) -> Iterator[None]:
        start = time.perf_counter()
        self.trace("[TIMER START] %s", operation)
        try:
            yield
        except Exception as exc:
            elapsed = time.perf_counter() - start
            self.error(
                "[TIMER ERROR] %s misslyckades efter %.4fs: %s",
                operation,
                elapsed,
                exc,
            )
            raise
        else:
            elapsed = time.perf_counter() - start
            if elapsed > warn_threshold:
                self.warning("[TIMER SLOW] %s tog %.4fs", operation, elapsed)
            else:
                self.trace("[TIMER END] %s på %.4fs", operation, elapsed)

    def get_statistics(self) -> dict[str, Any]:
        with self._stats_lock:
            return self._stats.to_dict()

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            instance = cls._instance
            if instance is None:
                return

            for handler in instance._handlers:
                with suppress(Exception):
                    handler.flush()
                    handler.close()

            for handler in instance._logger.handlers[:]:
                with suppress(Exception):
                    handler.close()
                instance._logger.removeHandler(handler)

            instance._handlers.clear()
            cls._instance = None
            cls._loggers.clear()

    def __del__(self) -> None:
        if hasattr(self, "_handlers"):
            for handler in self._handlers:
                with suppress(Exception):
                    handler.close()


# ─── PlatformHelper ─────────────────────────────────────────────────────────


class PlatformHelper:
    """Hjälpklass för plattformsspecifika operationer utan Tkinter."""

    _system_info: ClassVar[SystemInfo | None] = None
    _display_info: ClassVar[DisplayInfo | None] = None
    _screen_size_cache: ClassVar[tuple[int, int] | None] = None

    @classmethod
    @lru_cache(maxsize=1)
    def get_platform(cls) -> PlatformType:
        return PlatformType.current()

    @classmethod
    def is_windows(cls) -> bool:
        return cls.get_platform() == PlatformType.WINDOWS

    @classmethod
    def is_macos(cls) -> bool:
        return cls.get_platform() == PlatformType.MACOS

    @classmethod
    def is_linux(cls) -> bool:
        return cls.get_platform() == PlatformType.LINUX

    @classmethod
    def is_unix(cls) -> bool:
        return cls.is_macos() or cls.is_linux()

    @classmethod
    def is_frozen(cls) -> bool:
        return bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))

    @classmethod
    def is_admin(cls) -> bool:
        try:
            if cls.is_windows():
                import ctypes
                return ctypes.windll.shell32.IsUserAnAdmin() != 0
            return os.geteuid() == 0
        except Exception:
            return False

    @classmethod
    def get_screen_size(cls) -> tuple[int, int]:
        if cls._screen_size_cache:
            return cls._screen_size_cache

        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    geometry = screen.availableGeometry()
                    cls._screen_size_cache = (geometry.width(), geometry.height())
                    return cls._screen_size_cache
        except Exception:
            _logger.debug("PySide6 screen size lookup failed", exc_info=True)

        size = cls._get_screen_size_os_fallback()
        cls._screen_size_cache = size
        return size

    @classmethod
    def _get_screen_size_os_fallback(cls) -> tuple[int, int]:
        try:
            if cls.is_windows():
                import ctypes
                user32 = ctypes.windll.user32
                width = int(user32.GetSystemMetrics(0))
                height = int(user32.GetSystemMetrics(1))
                if width > 0 and height > 0:
                    return width, height

            if cls.is_macos():
                result = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                match = re.search(r"Resolution:\s+(\d+)\s*x\s*(\d+)", result.stdout)
                if match:
                    return int(match.group(1)), int(match.group(2))

            if cls.is_linux():
                result = subprocess.run(
                    ["xrandr", "--current"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                match = re.search(r"current\s+(\d+)\s*x\s*(\d+)", result.stdout)
                if match:
                    return int(match.group(1)), int(match.group(2))
        except Exception:
            _logger.debug("OS screen size fallback failed", exc_info=True)

        return 1920, 1080

    @classmethod
    def get_dpi_scale(cls) -> float:
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    return max(1.0, float(screen.devicePixelRatio()))
        except Exception:
            _logger.debug("PySide6 DPI lookup failed", exc_info=True)

        if cls.is_windows():
            try:
                import ctypes
                dc = ctypes.windll.user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(dc, 88)
                ctypes.windll.user32.ReleaseDC(0, dc)
                return max(1.0, dpi / 96.0)
            except Exception:
                _logger.debug("Windows DPI lookup failed", exc_info=True)

        if cls.is_macos():
            return 2.0

        if cls.is_linux():
            try:
                result = subprocess.run(
                    ["xrdb", "-query"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    check=False,
                )
                match = re.search(r"Xft\.dpi:\s*(\d+)", result.stdout)
                if match:
                    return max(1.0, int(match.group(1)) / 96.0)
            except Exception:
                _logger.debug("Linux DPI lookup failed", exc_info=True)

        return 1.0

    @classmethod
    def calculate_window_size(
        cls,
        base_width: int = DEFAULT_WINDOW_WIDTH,
        base_height: int = DEFAULT_WINDOW_HEIGHT,
        max_screen_ratio: float = 0.85,
    ) -> tuple[int, int]:
        screen_width, screen_height = cls.get_screen_size()
        dpi_scale = cls.get_dpi_scale()

        if dpi_scale > 1.0 and screen_width < 2560:
            width = int(base_width * min(dpi_scale, 1.5))
            height = int(base_height * min(dpi_scale, 1.5))
        else:
            width = base_width
            height = base_height

        max_width = int(screen_width * max_screen_ratio)
        max_height = int(screen_height * max_screen_ratio)
        return (
            max(min(width, max_width), MIN_WINDOW_WIDTH),
            max(min(height, max_height), MIN_WINDOW_HEIGHT),
        )

    @classmethod
    def get_display_info(cls) -> DisplayInfo:
        if cls._display_info is not None:
            return cls._display_info

        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app:
                screen = app.primaryScreen()
                if screen:
                    geometry = screen.availableGeometry()
                    info = DisplayInfo(
                        width=geometry.width(),
                        height=geometry.height(),
                        dpi=int(screen.logicalDotsPerInch()),
                        scaling=float(screen.devicePixelRatio()),
                        work_area=(
                            geometry.x(),
                            geometry.y(),
                            geometry.width(),
                            geometry.height(),
                        ),
                    )
                    cls._display_info = info
                    return info
        except Exception:
            _logger.debug("PySide6 display info lookup failed", exc_info=True)

        screen_width, screen_height = cls.get_screen_size()
        scaling = cls.get_dpi_scale()
        work_area = cls._get_work_area_fallback()
        info = DisplayInfo(
            width=screen_width,
            height=screen_height,
            dpi=int(96 * scaling),
            scaling=scaling,
            work_area=work_area,
        )
        cls._display_info = info
        _logger.debug(
            "Displayinfo: %dx%d dpi=%d scaling=%.2f (OS-fallback)",
            info.width, info.height, info.dpi, info.scaling,
            extra={"event": "infra.display_detected",
                   "ctx": {"width": info.width, "height": info.height,
                           "dpi": info.dpi, "scaling": info.scaling,
                           "source": "os_fallback"}},
        )
        return info

    @classmethod
    def _get_work_area_fallback(cls) -> tuple[int, int, int, int] | None:
        if not cls.is_windows():
            return None

        try:
            import ctypes

            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = _RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                48, 0, ctypes.byref(rect), 0
            ):
                return (
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                )
        except Exception:
            _logger.debug("Windows work area lookup failed", exc_info=True)

        return None

    @classmethod
    def reset_display_cache(cls) -> None:
        cls._display_info = None
        cls._screen_size_cache = None

    @classmethod
    def get_app_path(cls) -> Path:
        if cls.is_frozen():
            return Path(sys.executable).parent.resolve()
        return Path(__file__).parent.parent.resolve()

    @classmethod
    def get_executable_path(cls) -> Path:
        return Path(sys.executable).resolve()

    @classmethod
    def get_script_path(cls) -> Path | None:
        if cls.is_frozen():
            return None

        for filename in ("main.pyw", "main.py"):
            path = cls.get_app_path() / filename
            if path.exists():
                return path
        return None

    @classmethod
    def get_startup_command(cls) -> str:
        if cls.is_frozen():
            return f'"{cls.get_executable_path()}"'

        script = cls.get_script_path()
        if script:
            return f'"{cls.get_executable_path()}" "{script}"'
        return f'"{cls.get_executable_path()}"'

    @classmethod
    def get_config_path(cls) -> Path:
        return cls._ensure_path(cls.get_app_path() / FOLDER_CONFIG)

    @classmethod
    def get_logs_path(cls) -> Path:
        return cls._ensure_path(cls.get_app_path() / FOLDER_LOGS)

    @classmethod
    def get_templates_path(cls) -> Path:
        return cls._ensure_path(cls.get_app_path() / FOLDER_TEMPLATES)

    @classmethod
    def get_output_path(cls) -> Path:
        return cls._ensure_path(cls.get_app_path() / FOLDER_OUTPUT)

    @classmethod
    def get_icons_path(cls) -> Path:
        return cls._ensure_path(cls.get_app_path() / FOLDER_ICONS)

    @classmethod
    def _ensure_path(cls, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def get_home_dir(cls) -> Path:
        return Path.home()

    @classmethod
    def get_temp_dir(cls) -> Path:
        return Path(tempfile.gettempdir())

    @classmethod
    def get_user_config_dir(cls, app_name: str = APP_NAME) -> Path:
        if cls.is_windows():
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        elif cls.is_macos():
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(
                os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
            )

        path = base / app_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def hide_console(cls) -> None:
        if not cls.is_windows():
            return

        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            _logger.debug("Console hide failed", exc_info=True)

    @classmethod
    def set_dpi_awareness(cls) -> None:
        if not cls.is_windows():
            return

        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except AttributeError:
                try:
                    ctypes.windll.shcore.SetProcessDpiAwareness(1)
                except AttributeError:
                    ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            _logger.debug("DPI awareness setup failed", exc_info=True)

    @classmethod
    def set_app_user_model_id(cls, app_id: str | None = None) -> None:
        if not cls.is_windows():
            return

        try:
            import ctypes
            effective_app_id = app_id or f"RickardLangkvist.{APP_NAME}.{VERSION}"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                effective_app_id
            )
        except Exception:
            _logger.debug("AppUserModelID setup failed", exc_info=True)

    @classmethod
    def bring_window_to_front(cls, window: Any) -> bool:
        try:
            window.showNormal()
            window.raise_()
            window.activateWindow()

            if cls.is_windows():
                with suppress(Exception):
                    import ctypes
                    hwnd = int(window.winId())
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)
                    ctypes.windll.user32.ShowWindow(hwnd, 9)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)

            return True
        except Exception:
            _logger.exception("Could not bring window to front")
            return False

    @classmethod
    def center_window(cls, window: Any) -> None:
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if not app:
                return

            screen = app.primaryScreen()
            if not screen:
                return

            geometry = screen.availableGeometry()
            x_pos = geometry.x() + (geometry.width() - window.width()) // 2
            y_pos = geometry.y() + (geometry.height() - window.height()) // 2
            window.move(max(geometry.x(), x_pos), max(geometry.y(), y_pos))
        except Exception:
            _logger.debug("Window centering failed", exc_info=True)

    @classmethod
    def set_window_size(
        cls,
        window: Any,
        width: int,
        height: int,
        center: bool = True,
    ) -> None:
        try:
            window.resize(width, height)
            if center:
                cls.center_window(window)
        except Exception:
            _logger.debug("Window resize failed", exc_info=True)

    @classmethod
    def open_file(cls, path: PathLike) -> bool:
        try:
            target = Path(path)
            if not target.exists():
                return False

            if cls.is_windows():
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif cls.is_macos():
                subprocess.run(["open", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target)], check=False)
            return True
        except Exception:
            _logger.exception("Could not open file: %s", path)
            return False

    @classmethod
    def open_folder(cls, path: PathLike) -> bool:
        target = Path(path)
        if target.is_file():
            target = target.parent

        with suppress(Exception):
            target.mkdir(parents=True, exist_ok=True)

        return cls.open_file(target)

    @classmethod
    def reveal_in_explorer(cls, path: PathLike) -> bool:
        try:
            target = Path(path)
            if not target.exists():
                return False

            if cls.is_windows():
                subprocess.run(["explorer", "/select,", str(target)], check=False)
            elif cls.is_macos():
                subprocess.run(["open", "-R", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target.parent)], check=False)
            return True
        except Exception:
            _logger.exception("Could not reveal path: %s", path)
            return False

    @classmethod
    def get_system_info(cls) -> SystemInfo:
        if cls._system_info is None:
            cls._system_info = SystemInfo(
                platform=cls.get_platform(),
                platform_name=platform.system(),
                platform_release=platform.release(),
                platform_version=platform.version(),
                architecture=platform.machine(),
                python_version=platform.python_version(),
                app_version=VERSION,
                is_frozen=cls.is_frozen(),
                is_admin=cls.is_admin(),
                app_path=cls.get_app_path(),
                session_id=SESSION_ID,
            )
        return cls._system_info

    @classmethod
    def reset_system_info_cache(cls) -> None:
        cls._system_info = None


def get_system_info() -> SystemInfo:
    """Alias för PlatformHelper.get_system_info()."""
    return PlatformHelper.get_system_info()


def scale_px(base: int, font_height: int | None = None) -> int:
    """Delad adaptiv pixel-skalning (9.2.0) — §17/§30.

    Skalar ett basmått efter fontmetrik/DPI. Utan QApplication (eller utan
    giltig fontmetrik) returneras basmåttet — fail-safe.
    """
    try:
        height = font_height
        if height is None:
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                height = app.fontMetrics().height()
        if height and height > 0:
            return max(base, int(round(base * height / 16.0)))
    except Exception:
        pass
    return base


def _get_pyside_parent() -> Any | None:
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        return app.activeWindow() if app else None
    except Exception:
        return None


# ─── Dialoger ───────────────────────────────────────────────────────────────


def show_error(title: str, message: str, parent: Any | None = None) -> None:
    """Visa felmeddelande via PySide6 QMessageBox."""
    try:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(parent or _get_pyside_parent())
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Critical)
        box.exec()
    except Exception:
        print(f"❌ {title}: {message}", file=sys.stderr)


def show_warning(title: str, message: str, parent: Any | None = None) -> None:
    """Visa varning via PySide6 QMessageBox."""
    try:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(parent or _get_pyside_parent())
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Warning)
        box.exec()
    except Exception:
        print(f"⚠️ {title}: {message}", file=sys.stderr)


def show_info(title: str, message: str, parent: Any | None = None) -> None:
    """Visa information via PySide6 QMessageBox."""
    try:
        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(parent or _get_pyside_parent())
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Icon.Information)
        box.exec()
    except Exception:
        print(f"ℹ️ {title}: {message}")


def ask_yes_no(title: str, message: str, parent: Any | None = None) -> bool:
    """Fråga ja/nej via PySide6 QMessageBox."""
    try:
        from PySide6.QtWidgets import QMessageBox
        result = QMessageBox.question(
            parent or _get_pyside_parent(),
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes
    except Exception:
        return False


def ask_ok_cancel(title: str, message: str, parent: Any | None = None) -> bool:
    """Fråga ok/avbryt via PySide6 QMessageBox."""
    try:
        from PySide6.QtWidgets import QMessageBox
        result = QMessageBox.question(
            parent or _get_pyside_parent(),
            title,
            message,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Ok
    except Exception:
        return False


# ─── Formatering ─────────────────────────────────────────────────────────────


def format_file_size(size: int) -> str:
    """Formatera filstorlek läsbart."""
    if size < 0:
        return "0 B"

    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_timestamp(
    dt: datetime | None = None,
    fmt: str = "%Y-%m-%d %H:%M:%S",
) -> str:
    return (dt or datetime.now()).strftime(fmt)


def format_duration(seconds: float) -> str:
    """Formatera tidsperiod läsbart."""
    if seconds < 0:
        return "0s"
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}μs"
    if seconds < 1.0:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"

    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)

    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def format_date_swedish(dt: datetime | None = None) -> str:
    value = dt or datetime.now()
    months = [
        "januari", "februari", "mars", "april", "maj", "juni",
        "juli", "augusti", "september", "oktober", "november", "december",
    ]
    return f"{value.day} {months[value.month - 1]} {value.year}"


def format_number(num: int | float, decimals: int = 0) -> str:
    if decimals:
        return f"{num:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return f"{int(num):,}".replace(",", " ")


# ─── Filsäkerhet ─────────────────────────────────────────────────────────────


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTIPLE_UNDERSCORES = re.compile(r"_+")


def safe_filename(
    name: str,
    max_len: int = 100,
    replacement: str = "_",
) -> str:
    """Skapa säkert filnamn med NFC-normalisering och bevarade ÅÄÖ."""
    if not name:
        return "unnamed"

    normalized = unicodedata.normalize("NFC", str(name))
    cleaned = _INVALID_FILENAME_CHARS.sub(replacement, normalized)
    cleaned = cleaned.strip(". ")

    if replacement:
        cleaned = _MULTIPLE_UNDERSCORES.sub(replacement, cleaned)

    cleaned = cleaned[:max_len].rstrip(". ") if max_len > 0 else cleaned
    return cleaned or "unnamed"


def ensure_folders_exist(
    base: Path,
    folders: list[str] | None = None,
) -> dict[str, Path]:
    """Säkerställ att programmappar finns."""
    if folders is None:
        folder_map: dict[str, Path] = {
            "icons": base / FOLDER_ICONS,
            "templates": base / FOLDER_TEMPLATES,
            "output": base / FOLDER_OUTPUT,
            "config": base / FOLDER_CONFIG,
            "plugins": base / FOLDER_PLUGINS,
            "logs": base / FOLDER_LOGS,
            "backup": base / FOLDER_BACKUP,
            "sent": base / FOLDER_SENT,
        }
    else:
        folder_map = {folder: base / folder for folder in folders}

    for path in folder_map.values():
        path.mkdir(parents=True, exist_ok=True)

    return folder_map


def get_unique_filename(
    directory: Path,
    base_name: str,
    extension: str = "",
) -> Path:
    """Returnera unikt filnamn i katalog."""
    suffix = extension
    if suffix and not suffix.startswith("."):
        suffix = f".{suffix}"

    filepath = directory / f"{base_name}{suffix}"
    counter = 1
    while filepath.exists():
        filepath = directory / f"{base_name}_{counter}{suffix}"
        counter += 1
    return filepath


# ─── VIN-hantering ──────────────────────────────────────────────────────────


_VIN_VALID_CHARS = re.compile(r"[^A-HJ-NPR-Z0-9]", re.IGNORECASE)


def format_vin(vin: str) -> str:
    if not vin:
        return ""
    return _VIN_VALID_CHARS.sub("", vin.upper().strip())[:17]


def validate_vin(vin: str) -> bool:
    return len(format_vin(vin)) == 17


def extract_vin_info(vin: str) -> dict[str, str] | None:
    formatted = format_vin(vin)
    if len(formatted) != 17:
        return None

    return {
        "vin": formatted,
        "wmi": formatted[:3],
        "vds": formatted[3:9],
        "vis": formatted[9:],
        "year_code": formatted[9],
        "plant_code": formatted[10],
        "serial": formatted[11:],
    }


def format_vin_display(vin: str) -> str:
    formatted = format_vin(vin)
    if not formatted:
        return ""
    return " ".join(
        formatted[i : i + 4] for i in range(0, len(formatted), 4)
    )


# ─── Dekoratorer ─────────────────────────────────────────────────────────────


def debounce(wait: float) -> Callable[..., Any]:
    """Debounce-decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        timer: list[threading.Timer | None] = [None]
        lock = threading.Lock()

        @wraps(func)
        def debounced(*args: Any, **kwargs: Any) -> None:
            def call() -> None:
                with lock:
                    timer[0] = None
                func(*args, **kwargs)

            with lock:
                if timer[0] is not None:
                    timer[0].cancel()
                current_timer = threading.Timer(wait, call)
                current_timer.daemon = True
                timer[0] = current_timer
                current_timer.start()

        def cancel() -> None:
            with lock:
                if timer[0] is not None:
                    timer[0].cancel()
                    timer[0] = None

        debounced.cancel = cancel  # type: ignore[attr-defined]
        return debounced

    return decorator


def throttle(limit: float) -> Callable[..., Any]:
    """Throttle-decorator."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        last_call: list[float] = [0.0]
        pending: list[tuple[tuple[Any, ...], dict[str, Any]] | None] = [None]
        lock = threading.Lock()

        @wraps(func)
        def throttled(*args: Any, **kwargs: Any) -> Any:
            with lock:
                now = time.time()
                if now - last_call[0] >= limit:
                    last_call[0] = now
                    return func(*args, **kwargs)
                pending[0] = (args, kwargs)
                return None

        def flush() -> Any:
            with lock:
                if pending[0] is None:
                    return None
                args, kwargs = pending[0]
                pending[0] = None
                last_call[0] = time.time()
                return func(*args, **kwargs)

        throttled.flush = flush  # type: ignore[attr-defined]
        return throttled

    return decorator


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: Callable[[int, Exception], None] | None = None,
) -> Callable[..., Any]:
    """Retry-decorator med exponentiell backoff."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == max_attempts - 1:
                        raise
                    if on_retry:
                        on_retry(attempt + 1, exc)
                    time.sleep(current_delay)
                    current_delay *= backoff
            return None

        return wrapper

    return decorator


def run_in_thread(
    func: Callable[..., Any] | None = None,
    *,
    daemon: bool = True,
    name: str | None = None,
) -> Callable[..., Any]:
    """Kör funktion i bakgrundstråd."""

    def decorator(inner: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(inner)
        def wrapper(*args: Any, **kwargs: Any) -> threading.Thread:
            thread = threading.Thread(
                target=inner,
                args=args,
                kwargs=kwargs,
                daemon=daemon,
                name=name or f"{inner.__name__}_thread",
            )
            thread.start()
            return thread

        return wrapper

    return decorator(func) if func is not None else decorator


def cached_property(func: Callable[..., Any]) -> property:
    """Enkel cached_property utan dataclass-konflikter."""
    attr = f"_cached_{func.__name__}"

    @wraps(func)
    def wrapper(self: Any) -> Any:
        if not hasattr(self, attr):
            setattr(self, attr, func(self))
        return getattr(self, attr)

    return property(wrapper)


def synchronized(
    lock: threading.Lock | threading.RLock | None = None,
) -> Callable[..., Any]:
    """Thread-safe decorator via RLock."""

    effective_lock = lock or threading.RLock()

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with effective_lock:
                return func(*args, **kwargs)

        return wrapper

    return decorator


def clear_pycache(
    root: Path | str,
    *,
    dry_run: bool = False,
    log: logging.Logger | None = None,
) -> dict[str, int]:
    """Rensa teknisk Python-cache (`__pycache__/` + `*.pyc`/`*.pyo`) — R6/B1.

    Rör ALDRIG användardata: `settings.json`, loggar, profiler, `Mallar/`, `Out/`,
    `Skickade/` och `Backup/` är fredade (PROD-reglerna i Del I §26).

    Args:
        root: projektrot som genomsöks rekursivt.
        dry_run: räkna bara, ta inte bort något.
        log: valfri logger (default: modulens logger).

    Returns:
        dict med `dirs` (antal borttagna `__pycache__`-mappar), `files`, `bytes`, `errors`.

    Loggar vid lyckad rensning: `[CACHE] Rensade <N> __pycache__-mappar (<M> filer[, <B> KB])`.
    """
    logger = log or _logger
    stats = {"dirs": 0, "files": 0, "bytes": 0, "errors": 0}
    root_path = Path(root)
    if not root_path.is_dir():
        return stats

    started = time.perf_counter()
    try:
        # 1) __pycache__-mappar (tas bort hela — Python återskapar dem vid behov).
        for cache_dir in sorted(
            (p for p in root_path.rglob("__pycache__") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                stats["bytes"] += _dir_size(cache_dir)
                stats["dirs"] += 1
                if not dry_run:
                    shutil.rmtree(cache_dir, ignore_errors=True)
            except Exception as exc:  # pragma: no cover - defensivt
                stats["errors"] += 1
                logger.debug("[CACHE] kunde inte ta bort %s: %s", cache_dir, exc)

        # 2) Lösa .pyc/.pyo-filer (kan ligga utanför __pycache__).
        for pattern in ("*.pyc", "*.pyo"):
            for cache_file in root_path.rglob(pattern):
                try:
                    if not cache_file.is_file():
                        continue
                    stats["files"] += 1
                    with suppress(OSError):
                        stats["bytes"] += cache_file.stat().st_size
                    if not dry_run:
                        with suppress(OSError):
                            cache_file.unlink()
                except Exception:
                    stats["errors"] += 1

        elapsed_ms = (time.perf_counter() - started) * 1000
        freed_kb = stats["bytes"] / 1024.0
        if dry_run:
            logger.info(
                "[CACHE] Skulle rensa %d __pycache__-mappar (%d filer, %.1f KB)",
                stats["dirs"], stats["files"], freed_kb,
            )
        else:
            logger.info(
                "[CACHE] Rensade %d __pycache__-mappar (%d filer, %.1f KB)",
                stats["dirs"], stats["files"], freed_kb,
            )
        _evt_core(
            "infra.cache_cleared",
            f"Tog bort {stats['dirs']} __pycache__-mappar ({stats['files']} filer, {freed_kb:.1f} KB på {elapsed_ms:.1f} ms",
            logging.INFO, dirs=stats["dirs"], files=stats["files"],
            kb=round(freed_kb, 1), ms=round(elapsed_ms, 1),
            dry_run=bool(dry_run),
        )
    except Exception as exc:
        stats["errors"] += 1
        logger.debug("[CACHE] rensning misslyckades: %s", exc)
    return stats


def _dir_size(path: Path) -> int:
    """Summa storlek för filer i en mapp (bäst-effort, aldrig undantag)."""
    total = 0
    try:
        for entry in path.rglob("*"):
            with suppress(OSError):
                if entry.is_file():
                    total += entry.stat().st_size
    except Exception:
        return total
    return total


# ─── Export ──────────────────────────────────────────────────────────────────


CACHE_STATS: dict[str, int] = {}


__all__: list[str] = [
    "clear_pycache",
    "__version__",
    "__author__",
    "APP_NAME",
    "APP_DISPLAY_NAME",
    "VERSION",
    "COPYRIGHT",
    "SESSION_ID",
    "FOLDER_ICONS",
    "FOLDER_TEMPLATES",
    "FOLDER_OUTPUT",
    "FOLDER_CONFIG",
    "FOLDER_PLUGINS",
    "FOLDER_LOGS",
    "FOLDER_BACKUP",
    "FOLDER_SENT",
    "DEFAULT_WINDOW_WIDTH",
    "DEFAULT_WINDOW_HEIGHT",
    "MIN_WINDOW_WIDTH",
    "MIN_WINDOW_HEIGHT",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "PathLike",
    "get_copyright",
    "Platform",
    "PlatformType",
    "CURRENT_PLATFORM",
    "LogLevel",
    "CloseAction",
    "StartupMode",
    "FileOperation",
    "SystemInfo",
    "DisplayInfo",
    "LogStats",
    "AtomicFileWriter",
    "SessionContextFilter",
    "CompressedRotatingFileHandler",
    "ColoredFormatter",
    "JSONLFormatter",
    "AdvancedLogger",
    "PlatformHelper",
    "get_system_info",
    "scale_px",
    "show_error",
    "show_warning",
    "show_info",
    "ask_yes_no",
    "ask_ok_cancel",
    "format_file_size",
    "format_timestamp",
    "format_duration",
    "format_date_swedish",
    "format_number",
    "safe_filename",
    "ensure_folders_exist",
    "get_unique_filename",
    "format_vin",
    "validate_vin",
    "extract_vin_info",
    "format_vin_display",
    "debounce",
    "throttle",
    "retry",
    "run_in_thread",
    "cached_property",
    "synchronized",
]
