from __future__ import annotations

# -*- coding: utf-8 -*-
"""
Fil:       plugins/generator/core.py
Projekt:   Intygsgenerator
Version:   5.0.1
Ansvar:    Kompatibilitetsstub — re-exporterar från certificates engine
           och lokal plugin.py. Innehåller bakåtkompatibla hjälpfunktioner.

Denna modul finns för äldre imports och för att generator-pluginet ska kunna
återanvända certificates-motorn utan duplicerad PDF-, mall- och counter-logik.

© Rickard Längkvist 2025–{year}
"""

import logging
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Final

__version__: str = "5.0.1"

_logger = logging.getLogger(__name__)

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_FORBIDDEN_CHARS: Final[str] = '<>:"/\\|?*\x00'
_INVALID_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

ENGINE_AVAILABLE: bool = False
PLUGIN_AVAILABLE: bool = False

_engine_safe_filename: Any = None
_engine_get_field_label: Any = None
_engine_platform_helper: Any = None


class ConversionMethod(Enum):
    """Fallback-konverteringsmetoder."""

    NONE = 0
    LIBREOFFICE = 1
    DOCX2PDF = 2
    COMTYPES = 3
    UNOCONV = 4
    ABIWORD = 5

    @property
    def display_name(self) -> str:
        names = {
            ConversionMethod.NONE: "Ingen",
            ConversionMethod.LIBREOFFICE: "LibreOffice",
            ConversionMethod.DOCX2PDF: "docx2pdf",
            ConversionMethod.COMTYPES: "Word COM",
            ConversionMethod.UNOCONV: "unoconv",
            ConversionMethod.ABIWORD: "AbiWord",
        }
        return names.get(self, "Ingen")


class ValidationResult(Enum):
    """Fallback-valideringsresultat."""

    VALID = 1
    WARNING = 2
    ERROR = 3


class ScanStatus(Enum):
    """Fallback-skanningsstatus."""

    IDLE = 1
    SCANNING = 2
    COMPLETE = 3
    ERROR = 4


@dataclass(slots=True)
class TemplateConfig:
    """Fallback TemplateConfig."""

    name: str = ""
    description: str = ""
    category: str = ""
    counter_prefix: str = ""
    counter_start: int = 1
    extra_fields: list[str] = field(default_factory=list)
    default_values: dict[str, str] = field(default_factory=dict)
    pdf_attachments_order: list[str] = field(default_factory=list)
    validate_vin: bool = True
    validate_reg: bool = False
    output_format: str = "pdf"
    filename_pattern: str = "{number} Intyg {prefix} {vin} {reg} {date}"


@dataclass(slots=True)
class Template:
    """Fallback Template."""

    name: str = ""
    folder: Path = field(default_factory=Path)
    docx_file: Path = field(default_factory=Path)
    pdf_attachments: list[Path] = field(default_factory=list)
    config: TemplateConfig = field(default_factory=TemplateConfig)
    placeholders: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.docx_file.exists() and self.docx_file.suffix.lower() == ".docx"

    def get_user_fields(self) -> list[str]:
        fields = list(dict.fromkeys(self.placeholders + self.config.extra_fields))
        return [name for name in fields if not is_auto_field(name)]

    def get_default(self, field_name: str) -> str:
        return self.config.default_values.get(field_name, "")


@dataclass(slots=True)
class GenerationResult:
    """Fallback GenerationResult."""

    success: bool = False
    output_path: Path | None = None
    number: int = 0
    filename: str = ""
    page_count: int = 0
    error: str | None = None
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    template_name: str = ""


@dataclass(slots=True)
class FieldValidation:
    """Fallback FieldValidation."""

    field_name: str = ""
    result: ValidationResult = ValidationResult.ERROR
    message: str = ""
    formatted_value: str = ""

    @property
    def is_valid(self) -> bool:
        return self.result != ValidationResult.ERROR

    @property
    def is_error(self) -> bool:
        return self.result == ValidationResult.ERROR


@dataclass(slots=True)
class BatchResult:
    """Fallback BatchResult."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[GenerationResult] = field(default_factory=list)
    duration: float = 0.0

    @property
    def success(self) -> bool:
        return self.succeeded > 0 and self.failed == 0


class VINValidator:
    """Fallback VINValidator."""

    @staticmethod
    def format_vin(vin: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(vin).upper())[:17]

    @classmethod
    def validate(cls, vin: str) -> FieldValidation:
        formatted = cls.format_vin(vin)
        if len(formatted) != 17:
            return FieldValidation(
                field_name="VIN",
                result=ValidationResult.ERROR,
                message=f"VIN måste vara 17 tecken (angivet: {len(formatted)})",
                formatted_value=formatted,
            )
        return FieldValidation(
            field_name="VIN",
            result=ValidationResult.VALID,
            message="Giltigt VIN",
            formatted_value=formatted,
        )


class RegValidator:
    """Fallback RegValidator."""

    @staticmethod
    def format_reg(reg: str) -> str:
        return re.sub(r"[^A-Z0-9ÅÄÖ]", "", str(reg).upper())

    @classmethod
    def validate(cls, reg: str) -> FieldValidation:
        formatted = cls.format_reg(reg)
        if not formatted:
            return FieldValidation(
                field_name="REG",
                result=ValidationResult.ERROR,
                message="Registreringsnummer saknas",
                formatted_value=formatted,
            )
        return FieldValidation(
            field_name="REG",
            result=ValidationResult.VALID,
            message="Giltigt",
            formatted_value=formatted,
        )


class PlaceholderParser:
    """Fallback PlaceholderParser."""

    @staticmethod
    def find_placeholders(docx_path: str | Path) -> list[str]:
        return []


class PDFConverter:
    """Fallback PDFConverter."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.is_available = False
        self.method_name = "Ingen"

    def refresh(self) -> None:
        return None

    def convert(self, *_args: Any, **_kwargs: Any) -> Path | None:
        return None


class PDFMerger:
    """Fallback PDFMerger."""

    @staticmethod
    def merge(*_args: Any, **_kwargs: Any) -> tuple[bool, int]:
        return False, 0

    @staticmethod
    def get_page_count(_path: str | Path) -> int:
        return 1


class CounterManager:
    """Fallback CounterManager."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self._data: dict[str, int] = {}

    def get_current(self, name: str) -> int:
        return self._data.get(name, 0)

    def get_next(self, name: str, start: int = 1) -> int:
        return max(self._data.get(name, 0) + 1, start)

    def increment(self, name: str, start: int = 1) -> int:
        value = self.get_next(name, start)
        self._data[name] = value
        return value

    def get_all(self) -> dict[str, int]:
        return dict(self._data)

    def get_total(self) -> int:
        return sum(self._data.values())

    def save(self) -> None:
        return None


class CertificateGenerator:
    """Fallback CertificateGenerator."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def generate(self, *_args: Any, **_kwargs: Any) -> GenerationResult:
        return GenerationResult(success=False, error="Certificates engine ej tillgänglig")

    def generate_batch(self, *_args: Any, **_kwargs: Any) -> BatchResult:
        return BatchResult()


class PlatformHelper:
    """Fallback PlatformHelper."""

    @staticmethod
    def open_file(path: str | Path) -> bool:
        return _direct_open(Path(path))

    @staticmethod
    def open_folder(path: str | Path) -> bool:
        target = Path(path)
        if target.is_file():
            target = target.parent
        with suppress_exception():
            target.mkdir(parents=True, exist_ok=True)
        return _direct_open(target)


AUTO_FIELDS: Any = frozenset(
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
    }
)
FIELD_LABELS: dict[str, str] = {
    "vin": "Chassinummer (VIN)",
    "reg": "Registreringsnummer",
    "regnr": "Registreringsnummer",
    "registreringsnummer": "Registreringsnummer",
    "år": "Årsmodell",
    "årsmodell": "Årsmodell",
    "märke": "Fordonsmärke",
    "modell": "Fordonsmodell",
    "namn": "Namn",
    "ägare": "Ägare",
}
DEFAULT_VALUES: dict[str, str] = {"land": "Sverige"}
PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(r"::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::")


class suppress_exception:
    """Minimal context manager som loggar på debug i stället för att vara tyst."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc is not None:
            _logger.debug("[generator/core] ignorerat fallback-fel: %s", exc)
        return True


try:
    from plugins.certificates.engine import (
        AUTO_FIELDS,
        DEFAULT_VALUES,
        FIELD_LABELS,
        PLACEHOLDER_PATTERN,
        BatchResult,
        CertificateGenerator,
        ConversionMethod,
        CounterManager,
        FieldValidation,
        GenerationResult,
        PDFConverter,
        PDFMerger,
        PlaceholderParser,
        PlatformHelper,
        RegValidator,
        ScanStatus,
        Template,
        TemplateConfig,
        VINValidator,
        ValidationResult,
        get_field_label as _engine_get_field_label,
        safe_filename as _engine_safe_filename,
    )

    _engine_platform_helper = PlatformHelper
    ENGINE_AVAILABLE = True
    _logger.debug("[generator/core] certificates engine laddad")
except Exception as engine_err:
    ENGINE_AVAILABLE = False
    _logger.debug(
        "[generator/core] certificates engine ej tillgänglig: %s",
        engine_err,
        exc_info=True,
    )


try:
    from .plugin import AutostartManager, GeneratorSettings

    PLUGIN_AVAILABLE = True
    _logger.debug("[generator/core] plugin.py laddad")
except Exception as plugin_err:
    PLUGIN_AVAILABLE = False
    _logger.debug(
        "[generator/core] plugin.py ej tillgänglig: %s",
        plugin_err,
        exc_info=True,
    )

    class AutostartManager:
        """Fallback AutostartManager när plugin.py inte kan laddas."""

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.app_name = "Intygsgenerator"

        @property
        def is_enabled(self) -> bool:
            return False

        def enable(self, minimized: bool = True) -> bool:
            return False

        def disable(self) -> bool:
            return True

        def toggle(self) -> bool:
            return False

        def set_enabled(self, enabled: bool, minimized: bool = True) -> bool:
            return self.enable(minimized) if enabled else self.disable()

    @dataclass(slots=True, eq=True)
    class GeneratorSettings:
        """Fallback GeneratorSettings när plugin.py inte kan laddas."""

        start_with_os: bool = False
        minimize_to_tray: bool = True
        start_minimized: bool = True
        show_notifications: bool = True
        auto_open_pdf: bool = False
        company_name: str = ""
        company_org_number: str = ""
        company_address: str = ""
        company_phone: str = ""
        company_email: str = ""

        @classmethod
        def field_names(cls) -> list[str]:
            return [
                "start_with_os",
                "minimize_to_tray",
                "start_minimized",
                "show_notifications",
                "auto_open_pdf",
                "company_name",
                "company_org_number",
                "company_address",
                "company_phone",
                "company_email",
            ]

        def to_dict(self) -> dict[str, Any]:
            return {name: getattr(self, name) for name in self.field_names()}

        @classmethod
        def from_dict(cls, data: dict[str, Any]) -> GeneratorSettings:
            obj = cls()
            if not isinstance(data, dict):
                return obj
            for key, value in data.items():
                if key in cls.field_names():
                    setattr(obj, key, value)
            return obj

        @classmethod
        def from_config(cls, config: Any) -> GeneratorSettings:
            if config is None:
                return cls()
            data: dict[str, Any] = {}
            for field_name in cls.field_names():
                getter = getattr(config, "get", None)
                if callable(getter):
                    with suppress_exception():
                        data[field_name] = getter(f"generator.{field_name}", getattr(cls(), field_name))
            return cls.from_dict(data)

        def save_to_config(self, config: Any) -> bool:
            if config is None:
                return False
            setter = getattr(config, "set", None)
            if not callable(setter):
                return False
            try:
                for key, value in self.to_dict().items():
                    setter(f"generator.{key}", value)
                saver = getattr(config, "save", None)
                if callable(saver):
                    saver()
                return True
            except Exception:
                _logger.exception("[generator/core] GeneratorSettings.save_to_config misslyckades")
                return False


BatchGenerationResult = BatchResult
TemplateScanner: Any = None
CacheManager: Any = None


def safe_filename(name: str, max_length: int = 200) -> str:
    """
    Skapa säkert filnamn.

    Delegerar till certificates engine om tillgänglig. Fallback bevarar ÅÄÖ
    genom NFC-normalisering och ersätter OS-reserverade tecken.
    """
    if ENGINE_AVAILABLE and _engine_safe_filename is not None:
        with suppress_exception():
            return str(_engine_safe_filename(name, max_length))

    if not name or not str(name).strip():
        return "namnlös"

    normalized = unicodedata.normalize("NFC", str(name))
    cleaned = _INVALID_FILENAME_RE.sub("_", normalized)
    cleaned = re.sub(r"_+", "_", cleaned).strip(". ").strip()

    if max_length > 0:
        cleaned = cleaned[:max_length].rstrip(". ")

    return cleaned or "namnlös"


def is_auto_field(field_name: str) -> bool:
    """Returnera True om fältet är automatiskt."""
    if not field_name:
        return False

    if AUTO_FIELDS and hasattr(AUTO_FIELDS, "__contains__"):
        return field_name in AUTO_FIELDS

    fallback_auto = {
        "intyg",
        "nr",
        "nummer",
        "datum",
        "idag",
        "år_nu",
        "månad",
        "dag",
        "tid",
        "timestamp",
    }
    return field_name.lower() in fallback_auto


def get_field_label(field_name: str, default: str | None = None) -> str:
    """Returnera displaynamn för fält."""
    if ENGINE_AVAILABLE and _engine_get_field_label is not None:
        with suppress_exception():
            return str(_engine_get_field_label(field_name))

    if FIELD_LABELS and hasattr(FIELD_LABELS, "get"):
        label = FIELD_LABELS.get(field_name)
        if label is None:
            label = FIELD_LABELS.get(field_name.lower())
        if label is not None:
            return str(label)

    return default if default is not None else field_name


def normalize_field_name(name: str) -> str:
    """Normalisera fältnamn till versaler utan blanksteg."""
    return str(name).upper().strip().replace(" ", "_")


def get_timestamp() -> str:
    """Returnera aktuell tidsstämpel i ISO 8601-format."""
    return datetime.now().isoformat()


def format_duration(seconds: float) -> str:
    """Formatera tid som läsbar sträng."""
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
    hours = minutes // 60
    minutes %= 60

    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def format_file_size(size_bytes: int | float, precision: int = 1) -> str:
    """Formatera filstorlek som läsbar sträng."""
    size = max(0.0, float(size_bytes))
    if size < 1024.0:
        return f"{int(size)} B"

    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024.0 or unit == "TB":
            return f"{size:.{precision}f} {unit}"

    return f"{size:.{precision}f} TB"


def open_file(path: str | Path) -> bool:
    """Öppna fil med OS-standardprogram."""
    target = Path(path)

    if ENGINE_AVAILABLE and _engine_platform_helper is not None:
        with suppress_exception():
            return bool(_engine_platform_helper.open_file(target))

    with suppress_exception():
        from plugins import OSHelper

        return bool(OSHelper.open_file(target))

    return _direct_open(target)


def open_folder(path: str | Path) -> bool:
    """Öppna mapp i filhanterare."""
    target = Path(path)
    if target.is_file():
        target = target.parent

    with suppress_exception():
        target.mkdir(parents=True, exist_ok=True)

    if ENGINE_AVAILABLE and _engine_platform_helper is not None:
        with suppress_exception():
            return bool(_engine_platform_helper.open_folder(target))

    with suppress_exception():
        from plugins import OSHelper

        return bool(OSHelper.open_folder(target))

    return _direct_open(target)


def _direct_open(path: Path) -> bool:
    """Direkt OS-öppning utan hjälpklasser."""
    if not path.exists():
        return False

    try:
        if sys.platform == "win32":
            import os

            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        return True
    except Exception as exc:
        _logger.debug("[generator/core] _direct_open: %s", exc)
        return False


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


def is_plugin_available() -> bool:
    """Returnera True om generator plugin.py är tillgänglig."""
    return PLUGIN_AVAILABLE


__all__: list[str] = [
    "Template",
    "TemplateConfig",
    "GenerationResult",
    "FieldValidation",
    "BatchResult",
    "BatchGenerationResult",
    "VINValidator",
    "RegValidator",
    "PlaceholderParser",
    "PDFConverter",
    "PDFMerger",
    "CounterManager",
    "CertificateGenerator",
    "PlatformHelper",
    "AUTO_FIELDS",
    "FIELD_LABELS",
    "DEFAULT_VALUES",
    "PLACEHOLDER_PATTERN",
    "ConversionMethod",
    "ValidationResult",
    "ScanStatus",
    "AutostartManager",
    "GeneratorSettings",
    "TemplateScanner",
    "CacheManager",
    "ENGINE_AVAILABLE",
    "PLUGIN_AVAILABLE",
    "safe_filename",
    "is_auto_field",
    "get_field_label",
    "normalize_field_name",
    "get_timestamp",
    "format_duration",
    "format_file_size",
    "open_file",
    "open_folder",
    "get_copyright",
    "is_engine_available",
    "is_plugin_available",
]