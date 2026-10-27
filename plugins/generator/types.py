from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Fil:       plugins/generator/types.py
Projekt:   Intygsgenerator
Version:   5.0.2
Ansvar:    Kompatibilitetsstub — re-exporterar typer, enums och
           hjälpfunktioner från certificates engine.
           Bakåtkompatibla enums, exceptions och decorators bevarade.

Beroenden: Python 3.12+, plugins/certificates/engine.py

© Rickard Längkvist 2025–{year}
"""

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from enum import Enum, auto
from functools import wraps
from pathlib import Path
from typing import Any, Final, TypeVar

_logger = logging.getLogger(__name__)
T = TypeVar("T")

__version__: Final[str] = "5.0.2"
__author__: Final[str] = "Rickard Längkvist"

_START_YEAR: Final[int] = 2025
_CURRENT_YEAR: Final[int] = datetime.now().year
COPYRIGHT: Final[str] = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

IS_WINDOWS: Final[bool] = sys.platform == "win32"
IS_MACOS: Final[bool] = sys.platform == "darwin"
IS_LINUX: Final[bool] = sys.platform.startswith("linux")
IS_UNIX: Final[bool] = IS_MACOS or IS_LINUX
IS_POSIX: Final[bool] = os.name == "posix"
IS_FROZEN: Final[bool] = bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))

CURRENT_OS: Final[str] = (
    "Windows"
    if IS_WINDOWS
    else "macOS"
    if IS_MACOS
    else "Linux"
    if IS_LINUX
    else "Unknown"
)

PYTHON_VERSION: Final[tuple[int, int, int]] = (
    sys.version_info.major,
    sys.version_info.minor,
    sys.version_info.micro,
)
PYTHON_VERSION_STR: Final[str] = (
    f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
)

PathLike = str | Path
ProgressCallback = Callable[[str, float], None]
ErrorCallback = Callable[[Exception], None]

CONFIG_FILENAME: Final[str] = "generator_settings.json"
COUNTERS_FILENAME: Final[str] = "certificates_counters.json"
SETTINGS_FILENAME: Final[str] = "settings.json"
STATISTICS_FILENAME: Final[str] = "statistics.json"

FOLDER_OUTPUT: Final[str] = "Out"
FOLDER_TEMPLATES: Final[str] = "Mallar"
FOLDER_CONFIG: Final[str] = "js"
FOLDER_TEMP: Final[str] = ".temp"
FOLDER_BACKUP: Final[str] = "Backup"
FOLDER_LOGS: Final[str] = "logs"
FOLDER_ICON: Final[str] = "Icon"
FOLDER_SENT: Final[str] = "Skickade"

VIN_LENGTH: Final[int] = 17

CONVERSION_TIMEOUT_DEFAULT: Final[int] = 120
CONVERSION_TIMEOUT_MIN: Final[int] = 30
CONVERSION_TIMEOUT_MAX: Final[int] = 600
RETRY_COUNT_DEFAULT: Final[int] = 3
RETRY_DELAY_DEFAULT: Final[float] = 1.0
RETRY_BACKOFF_FACTOR: Final[float] = 2.0

MAX_TEMPLATE_SIZE_MB: Final[int] = 50
MAX_FILENAME_LENGTH: Final[int] = 200
MAX_PATH_LENGTH: Final[int] = 260 if IS_WINDOWS else 4096

PDF_DPI_DEFAULT: Final[int] = 300
PDF_DPI_MIN: Final[int] = 72
PDF_DPI_MAX: Final[int] = 600

_FORBIDDEN_CHARS: Final[str] = '<>:"/\\|?*\x00'
_INVALID_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

PLACEHOLDER_PATTERN_PRIMARY: Final[re.Pattern[str]] = re.compile(
    r"::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::",
    re.IGNORECASE | re.UNICODE,
)

PLACEHOLDER_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    PLACEHOLDER_PATTERN_PRIMARY,
)

ENGINE_AVAILABLE: bool = False
_SafeFilenameFn = Callable[[str, int], str]
_engine_safe_filename_ref: _SafeFilenameFn | None = None


_AUTO_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "intyg",
        "nummer",
        "number",
        "nr",
        "löpnummer",
        "id",
        "datum",
        "date",
        "idag",
        "today",
        "år_nu",
        "currentyear",
        "månad",
        "month",
        "dag",
        "day",
        "tid",
        "time",
        "timestamp",
        "tidstämpel",
    }
)


class _AutoFieldSet:
    """Case-insensitiv auto-fältuppslagning."""

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item.lower() in _AUTO_FIELD_NAMES

    def __iter__(self) -> Iterator[str]:
        yield from _AUTO_FIELD_NAMES

    def __len__(self) -> int:
        return len(_AUTO_FIELD_NAMES)


AUTO_FIELDS: Any = _AutoFieldSet()

FIELD_LABELS: dict[str, str] = {
    "vin": "Chassinummer (VIN)",
    "chassi": "Chassinummer",
    "reg": "Registreringsnummer",
    "regnr": "Registreringsnummer",
    "registreringsnummer": "Registreringsnummer",
    "år": "Årsmodell",
    "year": "Årsmodell",
    "årsmodell": "Årsmodell",
    "märke": "Fordonsmärke",
    "marke": "Fordonsmärke",
    "modell": "Fordonsmodell",
    "namn": "Namn",
    "ägare": "Ägare",
    "agare": "Ägare",
    "adress": "Adress",
    "telefon": "Telefon",
    "email": "E-post",
    "kommentar": "Kommentar",
}

DEFAULT_VALUES: dict[str, str] = {
    "land": "Sverige",
    "country": "Sverige",
}


class ConversionMethod(Enum):
    """PDF-konverteringsmetoder (fallback)."""

    NONE = "Ingen"
    LIBREOFFICE = "LibreOffice"
    DOCX2PDF = "docx2pdf"
    COMTYPES = "Microsoft Word"
    WORD = "Microsoft Word"
    UNOCONV = "unoconv"
    ABIWORD = "AbiWord"

    @property
    def display_name(self) -> str:
        return self.value

    @property
    def is_available(self) -> bool:
        return self != ConversionMethod.NONE


class ValidationResult(Enum):
    """Valideringsresultat (fallback)."""

    VALID = auto()
    WARNING = auto()
    ERROR = auto()

    @property
    def is_valid(self) -> bool:
        return self != ValidationResult.ERROR

    @property
    def is_error(self) -> bool:
        return self == ValidationResult.ERROR

    @property
    def is_warning(self) -> bool:
        return self == ValidationResult.WARNING


class ScanStatus(Enum):
    """Skanningsstatus (fallback)."""

    IDLE = auto()
    SCANNING = auto()
    COMPLETE = auto()
    ERROR = auto()


class VINValidator:
    """VIN-validering (fallback)."""

    STANDARD_LENGTH: Final[int] = 17
    INVALID_CHARS: Final[frozenset[str]] = frozenset("IOQioq")

    @classmethod
    def format_vin(cls, vin: str) -> str:
        return str(vin).strip().upper().replace(" ", "").replace("-", "")[:17]

    @classmethod
    def is_valid(cls, vin: str) -> bool:
        cleaned = cls.format_vin(vin)
        return len(cleaned) == cls.STANDARD_LENGTH and not set(cleaned) & cls.INVALID_CHARS


class RegValidator:
    """Registreringsnummer-validering (fallback)."""

    @classmethod
    def format_reg(cls, reg: str) -> str:
        return str(reg).strip().upper().replace(" ", "").replace("-", "")


class PlatformHelper:
    """Plattformshjälpare (fallback)."""

    @staticmethod
    def open_file(path: Any) -> bool:
        try:
            target = Path(path)
            if not target.exists():
                return False
            if IS_WINDOWS:
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif IS_MACOS:
                subprocess.run(["open", str(target)], check=False)
            else:
                subprocess.run(["xdg-open", str(target)], check=False)
            return True
        except Exception:
            _logger.exception("[Generator/types] Kunde inte öppna fil: %s", path)
            return False

    @staticmethod
    def open_folder(path: Any) -> bool:
        target = Path(path)
        if target.is_file():
            target = target.parent
        with suppress(Exception):
            target.mkdir(parents=True, exist_ok=True)
        return PlatformHelper.open_file(target)


try:
    from plugins.certificates.engine import (
        AUTO_FIELDS,
        DEFAULT_VALUES,
        FIELD_LABELS,
        ConversionMethod,
        PlatformHelper,
        RegValidator,
        ScanStatus,
        ValidationResult,
        VINValidator,
        safe_filename as _safe_filename_engine,
    )

    _engine_safe_filename_ref = _safe_filename_engine
    ENGINE_AVAILABLE = True
    _logger.debug("[Generator/types] certificates engine laddad OK")
except Exception as engine_exc:
    ENGINE_AVAILABLE = False
    _logger.debug(
        "[Generator/types] certificates engine ej tillgänglig: %s",
        engine_exc,
        exc_info=True,
    )


def safe_filename(name: str, max_length: int = MAX_FILENAME_LENGTH) -> str:
    """
    Returnera filsystemssäkert filnamn med ÅÄÖ bevarat.

    Delegerar till engine om tillgänglig. Fallback använder NFC-normalisering.
    """
    if ENGINE_AVAILABLE and _engine_safe_filename_ref is not None:
        with suppress(Exception):
            return _engine_safe_filename_ref(name, max_length)

    if not name or not str(name).strip():
        return "untitled"

    normalized = unicodedata.normalize("NFC", str(name))
    cleaned = _INVALID_FILENAME_RE.sub("_", normalized)
    cleaned = re.sub(r"_+", "_", cleaned).strip(". ").strip()

    if max_length > 0:
        cleaned = cleaned[:max_length].rstrip(". ")

    return cleaned or "untitled"


class GenerationStatus(Enum):
    """Status för dokumentgenerering."""

    PENDING = "pending"
    PREPARING = "preparing"
    IN_PROGRESS = "in_progress"
    REPLACING = "replacing"
    CONVERTING = "converting"
    MERGING = "merging"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @property
    def is_terminal(self) -> bool:
        return self in {
            GenerationStatus.COMPLETED,
            GenerationStatus.FAILED,
            GenerationStatus.CANCELLED,
            GenerationStatus.TIMEOUT,
        }

    @property
    def is_success(self) -> bool:
        return self == GenerationStatus.COMPLETED

    @property
    def is_error(self) -> bool:
        return self in {GenerationStatus.FAILED, GenerationStatus.TIMEOUT}


class AutoFieldType(Enum):
    """Typer av automatiska fält."""

    NUMBER = "number"
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    YEAR = "year"
    MONTH = "month"
    WEEK = "week"
    COMPANY = "company"


class GeneratorError(Exception):
    """Basundantag för generator."""

    def __init__(
        self,
        message: str,
        details: str | None = None,
        cause: Exception | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
            "cause": str(self.cause) if self.cause else None,
        }


class ConversionError(GeneratorError):
    """Fel vid PDF-konvertering."""


class TemplateError(GeneratorError):
    """Fel relaterat till dokumentmallar."""


class PlaceholderError(GeneratorError):
    """Fel vid placeholder-hantering."""


class MergeError(GeneratorError):
    """Fel vid PDF-sammanslagning."""


class CounterError(GeneratorError):
    """Fel vid räknarhantering."""


class ValidationError(GeneratorError):
    """Valideringsfel."""


class VINValidationError(ValidationError):
    """VIN-valideringsfel."""


class GeneratorTimeoutError(GeneratorError):
    """Timeout-fel."""


class ConfigurationError(GeneratorError):
    """Konfigurationsfel."""


def is_auto_field(field_name: str) -> bool:
    """
    Kontrollera om ett fält är automatiskt.

    OBS: 'år' och 'year' är ALDRIG automatiska eftersom de avser årsmodell.
    """
    if not field_name:
        return False
    return field_name in AUTO_FIELDS


def get_field_label(field_name: str, default: str | None = None) -> str:
    """Returnera displayetikett för fält."""
    if hasattr(FIELD_LABELS, "get"):
        label = FIELD_LABELS.get(field_name)
        if label is None:
            label = FIELD_LABELS.get(field_name.lower())
        if label is not None:
            return str(label)
    return default if default is not None else field_name


def normalize_field_name(name: str) -> str:
    """Normalisera fältnamn för jämförelse."""
    return str(name).upper().strip().replace(" ", "")


def get_timestamp() -> str:
    """Returnera ISO 8601-tidsstämpel."""
    return datetime.now().isoformat()


def get_date_string(fmt: str = "%Y-%m-%d") -> str:
    """Returnera datum som sträng."""
    return datetime.now().strftime(fmt)


def get_time_string(fmt: str = "%H:%M:%S") -> str:
    """Returnera tid som sträng."""
    return datetime.now().strftime(fmt)


def format_file_size(size_bytes: int | float, precision: int = 1) -> str:
    """Formatera filstorlek till läsbar sträng."""
    if size_bytes < 0:
        return "0 B"

    value = float(size_bytes)
    if value < 1024.0:
        return f"{int(value)} B"

    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024.0 or unit == "TB":
            return f"{value:.{precision}f} {unit}"

    value /= 1024.0
    return f"{value:.{precision}f} PB"


def format_duration(seconds: float) -> str:
    """Formatera varaktighet till läsbar sträng."""
    if seconds < 0:
        return "0s"
    if seconds < 0.001:
        return f"{seconds * 1_000_000:.0f}µs"
    if seconds < 1.0:
        return f"{seconds * 1_000:.0f}ms"
    if seconds < 60.0:
        return f"{seconds:.1f}s"

    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"

    hours = minutes // 60
    minutes %= 60
    return f"{hours}h {minutes}m"


def format_number(number: int, separator: str = "\u00a0") -> str:
    """Formatera heltal med tusentalsavgränsare."""
    return f"{number:,}".replace(",", separator)


def open_file(path: PathLike) -> bool:
    """Öppna fil med OS-standardprogram."""
    return PlatformHelper.open_file(path)


def open_folder(path: PathLike) -> bool:
    """Öppna mapp i filhanterare."""
    return PlatformHelper.open_folder(path)


def ensure_path(path: PathLike) -> Path:
    """Konvertera PathLike till Path."""
    return path if isinstance(path, Path) else Path(path)


def get_home_directory() -> Path:
    """Returnera hemkatalog."""
    return Path.home()


def get_temp_directory() -> Path:
    """Returnera systemets tempkatalog."""
    return Path(tempfile.gettempdir())


def get_auto_field_type(field_name: str) -> AutoFieldType | None:
    """Returnera AutoFieldType för automatiskt fält."""
    upper = str(field_name).upper()

    number_keys = {"INTYG", "NUMMER", "NR", "NUMBER", "ID", "LÖPNUMMER"}
    if any(key in upper for key in number_keys):
        return AutoFieldType.NUMBER

    if any(key in upper for key in ("DATUM", "DATE", "IDAG", "TODAY")):
        return AutoFieldType.DATE

    if any(key in upper for key in ("TID", "TIME", "KLOCKAN")):
        return AutoFieldType.TIME

    if "TIMESTAMP" in upper or "TIDSSTÄMPEL" in upper:
        return AutoFieldType.DATETIME

    if "ÅR_NU" in upper or "CURRENTYEAR" in upper:
        return AutoFieldType.YEAR

    if "MÅNAD" in upper or "MONTH" in upper:
        return AutoFieldType.MONTH

    if "VECKA" in upper or "WEEK" in upper:
        return AutoFieldType.WEEK

    if "FÖRETAG" in upper or "COMPANY" in upper:
        return AutoFieldType.COMPANY

    return None


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


def is_engine_available() -> bool:
    """Returnera True om certificates engine är tillgänglig."""
    return ENGINE_AVAILABLE


@contextmanager
def temp_directory_ctx(
    prefix: str = "generator_",
    cleanup: bool = True,
) -> Iterator[Path]:
    """Context manager för temporär katalog."""
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield temp_dir
    finally:
        if cleanup:
            with suppress(Exception):
                import shutil

                shutil.rmtree(temp_dir)


@contextmanager
def temp_file_ctx(
    suffix: str = "",
    prefix: str = "generator_",
    cleanup: bool = True,
) -> Iterator[Path]:
    """Context manager för temporär fil."""
    file_descriptor, path_str = tempfile.mkstemp(suffix=suffix, prefix=prefix)
    os.close(file_descriptor)
    temp_path = Path(path_str)
    try:
        yield temp_path
    finally:
        if cleanup:
            with suppress(Exception):
                temp_path.unlink()


def thread_safe(func: Callable[..., T]) -> Callable[..., T]:
    """Gör en funktion trådsäker med RLock."""
    lock = threading.RLock()

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        with lock:
            return func(*args, **kwargs)

    return wrapper


def retry(
    max_attempts: int = RETRY_COUNT_DEFAULT,
    delay: float = RETRY_DELAY_DEFAULT,
    backoff: float = RETRY_BACKOFF_FACTOR,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator som försöker igen vid fel med exponentiell backoff."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            current_delay = delay
            last_exception: Exception | None = None

            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt < max_attempts - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff

            if last_exception is not None:
                raise last_exception

            return func(*args, **kwargs)

        return wrapper

    return decorator


__all__: list[str] = [
    "__version__",
    "__author__",
    "COPYRIGHT",
    "get_copyright",
    "IS_WINDOWS",
    "IS_MACOS",
    "IS_LINUX",
    "IS_UNIX",
    "IS_POSIX",
    "IS_FROZEN",
    "CURRENT_OS",
    "PYTHON_VERSION",
    "PYTHON_VERSION_STR",
    "CONFIG_FILENAME",
    "COUNTERS_FILENAME",
    "SETTINGS_FILENAME",
    "STATISTICS_FILENAME",
    "FOLDER_OUTPUT",
    "FOLDER_TEMPLATES",
    "FOLDER_CONFIG",
    "FOLDER_TEMP",
    "FOLDER_BACKUP",
    "FOLDER_LOGS",
    "FOLDER_ICON",
    "FOLDER_SENT",
    "VIN_LENGTH",
    "CONVERSION_TIMEOUT_DEFAULT",
    "CONVERSION_TIMEOUT_MIN",
    "CONVERSION_TIMEOUT_MAX",
    "RETRY_COUNT_DEFAULT",
    "RETRY_DELAY_DEFAULT",
    "RETRY_BACKOFF_FACTOR",
    "MAX_TEMPLATE_SIZE_MB",
    "MAX_FILENAME_LENGTH",
    "MAX_PATH_LENGTH",
    "PDF_DPI_DEFAULT",
    "PDF_DPI_MIN",
    "PDF_DPI_MAX",
    "PLACEHOLDER_PATTERN_PRIMARY",
    "PLACEHOLDER_PATTERNS",
    "_FORBIDDEN_CHARS",
    "PathLike",
    "ProgressCallback",
    "ErrorCallback",
    "AUTO_FIELDS",
    "FIELD_LABELS",
    "DEFAULT_VALUES",
    "ConversionMethod",
    "ValidationResult",
    "ScanStatus",
    "VINValidator",
    "RegValidator",
    "PlatformHelper",
    "safe_filename",
    "GenerationStatus",
    "AutoFieldType",
    "GeneratorError",
    "ConversionError",
    "TemplateError",
    "PlaceholderError",
    "MergeError",
    "CounterError",
    "ValidationError",
    "VINValidationError",
    "GeneratorTimeoutError",
    "ConfigurationError",
    "is_auto_field",
    "get_field_label",
    "normalize_field_name",
    "get_auto_field_type",
    "get_timestamp",
    "get_date_string",
    "get_time_string",
    "format_file_size",
    "format_duration",
    "format_number",
    "open_file",
    "open_folder",
    "ensure_path",
    "get_home_directory",
    "get_temp_directory",
    "is_engine_available",
    "temp_directory_ctx",
    "temp_file_ctx",
    "thread_safe",
    "retry",
]