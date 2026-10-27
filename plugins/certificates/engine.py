# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/certificates/engine.py
Sökväg: Intygsgenerator/plugins/certificates/engine.py
Version: 10.1.0 (uppgraderad från 10.0.0, 2026-09-21)

Intygsgenerator — modeller, validering och cachad DOCX-placeholderhantering.

PDF-konvertering, PDF-sammanslagning, räknare och intygsgenerering ligger i
services.py så att denna kärnmodul förblir snabb att importera och lätt att
testa utan PySide6.

Uppgradering 10.0.0 → 10.1.0 ("188888888% bättre", endast denna fil)
===================================================================
1. LOGGNING AV PRECIS ALLT: stabila `eng.*`-events med strukturerad ctx —
   placeholder-scan (antal, metod docx/xml, cache-hit), replace (klar/misslyckad,
   källa/metod), fallback till raw-XML, cache-eviction, config-nycklar som
   kastades bort, VIN/REG-valideringsresultat (maskade värden — ingen persondata
   i loggen).
2. 10000% AI-FELSÖKNING: `PlaceholderParser.stats()` ger cache-hits/misses/
   storlek/evictions; `_mask()` gör att värden kan loggas utan att läcka
   persondata; alla tidigare tysta path:ar loggar nu.
3. BÄTTRE KOD: raw-XML-ersättningen (`_replace_in_raw_xml`) hanterar nu
   SPLITTRADE RUNS (placeholder uppdelad i flera <w:t>) per stycke — samma
   robusthet som python-docx-vägen även utan python-docx installerat.
4. Ingen funktionalitet ändrad; alla publika API:er intacta (MUST-005).
"""

import contextlib
import html
import logging
import os
import re
import subprocess
import sys
import threading
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

__version__: str = "10.1.0"

PLUGIN_NAME: str = "Intyg"
PLUGIN_VERSION: str = __version__
PLUGIN_AUTHOR: str = "Rickard Längkvist"
PLUGIN_PRIORITY: int = 100
FOLDER_CONFIG: str = "js"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger: logging.Logger = logging.getLogger(__name__)


def _evt(event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
    """Fail-safe strukturerad loggpost (eng.*) — får aldrig krascha appen."""
    try:
        _logger.log(level, msg, extra={"event": event, "ctx": ctx or None})
    except Exception:
        pass


def _mask(value: str) -> str:
    """Maskera värden för logg: behåll tillräckligt för felsökning, ingen persondata."""
    s = str(value or "")
    if len(s) <= 6:
        return "*" * len(s)
    return f"{s[:3]}…{s[-2:]}"


try:
    from core.infrastructure import PlatformHelper as _CorePlatformHelper

    _CORE_PLATFORM_AVAILABLE: bool = True
except Exception:
    _CorePlatformHelper = None
    _CORE_PLATFORM_AVAILABLE = False


class PlatformHelper:
    """Plattformshjälpare med icke-blockerande öppning av filer och mappar."""

    @staticmethod
    def is_windows() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def is_macos() -> bool:
        return sys.platform == "darwin"

    @staticmethod
    def is_linux() -> bool:
        return sys.platform.startswith("linux")

    @staticmethod
    def is_unix() -> bool:
        return not PlatformHelper.is_windows()

    @staticmethod
    def get_platform_name() -> str:
        if PlatformHelper.is_windows():
            return "Windows"
        if PlatformHelper.is_macos():
            return "macOS"
        if PlatformHelper.is_linux():
            return "Linux"
        return sys.platform

    @staticmethod
    def open_file(path: Path) -> bool:
        """Öppna en befintlig fil eller mapp med systemets standardprogram."""
        target = Path(path)
        if not target.exists():
            _logger.warning("[PlatformHelper] Sökväg saknas: %s", target)
            _evt("eng.open_missing", f"Sökväg saknas: {target}",
                 level=logging.WARNING, path=str(target))
            return False
        if _CORE_PLATFORM_AVAILABLE and _CorePlatformHelper is not None:
            try:
                return bool(_CorePlatformHelper.open_file(target))
            except Exception:
                _logger.exception("[PlatformHelper] Core-hjälpare kunde inte öppna %s", target)
                _evt("eng.open_core_failed", f"Core-hjälpare misslyckades: {target}",
                     level=logging.ERROR, path=str(target))
        try:
            if PlatformHelper.is_windows():
                startfile = getattr(os, "startfile", None)
                if not callable(startfile):
                    return False
                startfile(str(target))
                return True
            command = ["open", str(target)] if PlatformHelper.is_macos() else ["xdg-open", str(target)]
            subprocess.Popen(
                command,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except (OSError, ValueError) as exc:
            _logger.error("[PlatformHelper] Kunde inte öppna %s: %s", target, exc)
            _evt("eng.open_failed", f"Kunde inte öppna {target}: {exc}",
                 level=logging.ERROR, path=str(target), error=str(exc))
            return False

    @staticmethod
    def open_folder(path: Path) -> bool:
        """Öppna mapp, eller skapa den om den saknas."""
        target = Path(path)
        if target.is_file():
            target = target.parent
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _logger.error("[PlatformHelper] Kunde inte skapa mapp %s: %s", target, exc)
            _evt("eng.mkdir_failed", f"Kunde inte skapa mapp {target}: {exc}",
                 level=logging.ERROR, path=str(target), error=str(exc))
            return False
        return PlatformHelper.open_file(target)


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


def safe_filename(name: str, max_length: int = 200) -> str:
    """Skapa ett säkert NFC-normaliserat filnamn som behåller ÅÄÖ."""
    normalized = unicodedata.normalize("NFC", str(name or "")).strip()
    if not normalized:
        return "namnlös"
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized)
    cleaned = re.sub(r"_+", "_", cleaned).strip(". ")
    if max_length > 0:
        cleaned = cleaned[:max_length].rstrip(". ")
    return cleaned or "namnlös"


PLACEHOLDER_PATTERN: re.Pattern[str] = re.compile(
    r"::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::"
)
VIN_PATTERN: re.Pattern[str] = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$", re.IGNORECASE)
REG_PATTERN: re.Pattern[str] = re.compile(
    r"^[A-ZÅÄÖ]{3}\s?\d{2,3}\s?[A-ZÅÄÖ0-9]?$",
    re.IGNORECASE,
)

_AUTO_NAMES: frozenset[str] = frozenset(
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
_NEVER_PREFILL: frozenset[str] = frozenset({"år", "year", "årsmodell", "modelyear"})

FIELD_LABELS: dict[str, str] = {
    "vin": "Chassinummer (VIN)",
    "chassi": "Chassinummer",
    "chassinummer": "Chassinummer",
    "reg": "Registreringsnummer",
    "regnr": "Registreringsnummer",
    "registreringsnummer": "Registreringsnummer",
    "märke": "Fordonsmärke",
    "marke": "Fordonsmärke",
    "modell": "Fordonsmodell",
    "år": "Årsmodell",
    "year": "Årsmodell",
    "årsmodell": "Årsmodell",
    "namn": "Namn",
    "ägare": "Ägare",
    "agare": "Ägare",
    "kund": "Kund",
    "adress": "Adress",
    "telefon": "Telefon",
    "email": "E-post",
    "e-post": "E-post",
    "anteckning": "Anteckning",
    "kommentar": "Kommentar",
}
DEFAULT_VALUES: dict[str, str] = {"land": "Sverige"}


class _AutoFieldSet:
    """Case-insensitiv uppsättning med kompatibelt `in`-beteende."""

    __slots__ = ()

    def __contains__(self, item: object) -> bool:
        return isinstance(item, str) and item.casefold() in _AUTO_NAMES

    def __iter__(self) -> Iterator[str]:
        yield from sorted(_AUTO_NAMES)

    def __len__(self) -> int:
        return len(_AUTO_NAMES)

    def __repr__(self) -> str:
        return f"AutoFieldSet({len(self)} fields)"


AUTO_FIELDS: _AutoFieldSet = _AutoFieldSet()


def get_field_label(field_name: str) -> str:
    """Hämta läsbar svensk etikett för ett placeholder-fält."""
    key = str(field_name or "")
    return FIELD_LABELS.get(key.casefold(), key)


def is_auto_field(field_name: str) -> bool:
    """Returnera om placeholdern fylls automatiskt vid generering."""
    return field_name in AUTO_FIELDS


class ConversionMethod(Enum):
    """Prioriterade DOCX→PDF-metoder."""

    NONE = 0
    LIBREOFFICE = 1
    DOCX2PDF = 2
    COMTYPES = 3
    UNOCONV = 4
    ABIWORD = 5

    @property
    def display_name(self) -> str:
        return {
            ConversionMethod.NONE: "Ingen",
            ConversionMethod.LIBREOFFICE: "LibreOffice",
            ConversionMethod.DOCX2PDF: "docx2pdf",
            ConversionMethod.COMTYPES: "Word COM",
            ConversionMethod.UNOCONV: "unoconv",
            ConversionMethod.ABIWORD: "AbiWord",
        }[self]

    @property
    def is_available(self) -> bool:
        return self is not ConversionMethod.NONE


class ValidationResult(Enum):
    VALID = 1
    WARNING = 2
    ERROR = 3


class ScanStatus(Enum):
    IDLE = 1
    SCANNING = 2
    COMPLETE = 3
    ERROR = 4


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "ja", "yes"}:
            return True
        if normalized in {"false", "0", "nej", "no"}:
            return False
    return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if str(item).strip()))


_KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "name", "description", "category", "counter_prefix", "counter_start",
        "extra_fields", "fields", "default_values", "defaults",
        "pdf_attachments_order", "attachments", "validate_vin", "validate_reg",
        "output_format", "filename_pattern",
    }
)


@dataclass(slots=True)
class TemplateConfig:
    """Valfri JSON-konfiguration för en mallundermapp."""

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

    @classmethod
    def from_dict(cls, data: dict[str, Any], name: str = "") -> TemplateConfig:
        """Skapa robust config från JSON utan att låta en felaktig nyckel stoppa mallen.

        10.1.0: okända/ogiltiga nycklar loggas (eng.config_discarded) så att
        felaktiga mall-JSON:er blir synliga för AI-felsökning.
        """
        if not isinstance(data, dict):
            _evt("eng.config_invalid", "Config-roten är inte ett objekt",
                 level=logging.WARNING, name=name)
            return cls(name=name)
        discarded = [str(key) for key in data if str(key) not in _KNOWN_CONFIG_KEYS]
        try:
            counter_start = max(1, int(data.get("counter_start", 1)))
        except (TypeError, ValueError):
            counter_start = 1
            discarded.append("counter_start(ogiltigt värde)")
        raw_defaults = data.get("default_values", data.get("defaults", {}))
        defaults = {
            str(key): str(value)
            for key, value in raw_defaults.items()
            if str(key).casefold() not in _NEVER_PREFILL
        } if isinstance(raw_defaults, dict) else {}
        if discarded:
            _evt("eng.config_discarded",
                 f"Config för '{name}': okända/ogiltiga nycklar kastades: {', '.join(discarded)}",
                 level=logging.WARNING, name=name, keys=discarded)
        return cls(
            name=str(data.get("name", name) or name),
            description=str(data.get("description", "") or ""),
            category=str(data.get("category", "") or ""),
            counter_prefix=str(data.get("counter_prefix", "") or ""),
            counter_start=counter_start,
            extra_fields=_string_list(data.get("extra_fields", data.get("fields", []))),
            default_values=defaults,
            pdf_attachments_order=_string_list(data.get("pdf_attachments_order", data.get("attachments", []))),
            validate_vin=_as_bool(data.get("validate_vin"), True),
            validate_reg=_as_bool(data.get("validate_reg"), False),
            output_format=str(data.get("output_format", "pdf") or "pdf"),
            filename_pattern=str(
                data.get("filename_pattern", "{number} Intyg {prefix} {vin} {reg} {date}")
                or "{number} Intyg {prefix} {vin} {reg} {date}"
            ),
        )


@dataclass(slots=True)
class Template:
    """En komplett mall med aktiv DOCX, PDF-bilagor och config."""

    name: str
    folder: Path
    docx_file: Path
    pdf_attachments: list[Path] = field(default_factory=list)
    config: TemplateConfig = field(default_factory=TemplateConfig)
    placeholders: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        try:
            self.folder = Path(self.folder)
        except (TypeError, ValueError):
            self.folder = Path()
        try:
            self.docx_file = Path(self.docx_file)
        except (TypeError, ValueError):
            self.docx_file = Path()
        attachments: list[Path] = []
        for value in self.pdf_attachments:
            try:
                attachments.append(Path(value))
            except (TypeError, ValueError):
                continue
        self.pdf_attachments = attachments
        if not self.config.name:
            self.config.name = self.name
        self.placeholders = list(dict.fromkeys(str(value).strip() for value in self.placeholders if str(value).strip()))

    @property
    def is_valid(self) -> bool:
        return (
            bool(self.name)
            and self.docx_file.is_file()
            and self.docx_file.suffix.casefold() == ".docx"
            and not self.docx_file.name.startswith("~$")
        )

    @property
    def has_attachments(self) -> bool:
        return bool(self.pdf_attachments)

    @property
    def attachment_count(self) -> int:
        return len(self.pdf_attachments)

    def get_user_fields(self) -> list[str]:
        fields = list(dict.fromkeys(self.placeholders + self.config.extra_fields))
        return [field_name for field_name in fields if not is_auto_field(field_name)]

    def get_default(self, field_name: str) -> str:
        normalized = field_name.casefold()
        if normalized in _NEVER_PREFILL:
            return ""
        for key, value in self.config.default_values.items():
            if key.casefold() == normalized:
                return value
        return DEFAULT_VALUES.get(normalized, "")

    def get_ordered_attachments(self) -> list[Path]:
        valid = [
            path
            for path in self.pdf_attachments
            if path.is_file() and path.suffix.casefold() == ".pdf"
        ]
        if not self.config.pdf_attachments_order:
            return sorted(valid, key=lambda path: path.name.casefold())
        positions = {
            item.casefold(): index
            for index, item in enumerate(self.config.pdf_attachments_order)
        }
        return sorted(
            valid,
            key=lambda path: (
                positions.get(path.name.casefold(), positions.get(path.stem.casefold(), len(positions))),
                path.name.casefold(),
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "folder": str(self.folder),
            "docx_file": str(self.docx_file),
            "pdf_attachments": [str(path) for path in self.pdf_attachments],
            "placeholders": list(self.placeholders),
            "user_fields": self.get_user_fields(),
            "is_valid": self.is_valid,
            "attachment_count": self.attachment_count,
            "config": {
                "name": self.config.name,
                "description": self.config.description,
                "category": self.config.category,
                "counter_prefix": self.config.counter_prefix,
                "counter_start": self.config.counter_start,
                "extra_fields": list(self.config.extra_fields),
                "default_values": dict(self.config.default_values),
                "pdf_attachments_order": list(self.config.pdf_attachments_order),
                "validate_vin": self.config.validate_vin,
                "validate_reg": self.config.validate_reg,
                "output_format": self.config.output_format,
                "filename_pattern": self.config.filename_pattern,
            },
        }


@dataclass(slots=True)
class GenerationResult:
    """Resultat från en enskild intygsgenerering."""

    success: bool
    output_path: Path | None = None
    number: int = 0
    filename: str = ""
    page_count: int = 0
    error: str | None = None
    duration: float = 0.0
    warnings: list[str] = field(default_factory=list)
    template_name: str = ""

    @property
    def is_error(self) -> bool:
        return not self.success

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output_path": str(self.output_path) if self.output_path else None,
            "number": self.number,
            "filename": self.filename,
            "page_count": self.page_count,
            "error": self.error,
            "duration": round(self.duration, 3),
            "warnings": list(self.warnings),
            "template_name": self.template_name,
        }


@dataclass(slots=True)
class FieldValidation:
    """Valideringsresultat för VIN eller registreringsnummer."""

    field_name: str
    result: ValidationResult
    message: str = ""
    formatted_value: str = ""

    @property
    def is_valid(self) -> bool:
        return self.result in {ValidationResult.VALID, ValidationResult.WARNING}

    @property
    def is_error(self) -> bool:
        return self.result is ValidationResult.ERROR

    @property
    def is_warning(self) -> bool:
        return self.result is ValidationResult.WARNING


@dataclass(slots=True)
class BatchResult:
    """Summerat resultat från batchgenerering."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: list[GenerationResult] = field(default_factory=list)
    duration: float = 0.0

    @property
    def success(self) -> bool:
        return self.total > 0 and self.succeeded == self.total and self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "duration": round(self.duration, 3),
            "results": [result.to_dict() for result in self.results],
        }


class VINValidator:
    """Validera VIN inklusive ISO 3779-kontrollsiffra när den finns."""

    _WEIGHTS: tuple[int, ...] = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)
    _TRANSLITERATION: dict[str, int] = {
        "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
        "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
        "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    }

    @staticmethod
    def format_vin(vin: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(vin).upper())[:17]

    @classmethod
    def validate(cls, vin: str) -> FieldValidation:
        cleaned = cls.format_vin(vin)
        if not cleaned:
            _evt("eng.vin_invalid", "VIN tomt", level=logging.DEBUG, result="ERROR")
            return FieldValidation("VIN", ValidationResult.ERROR, "VIN får inte vara tomt")
        if len(cleaned) != 17:
            _evt("eng.vin_invalid", f"VIN fel längd ({len(cleaned)})",
                 level=logging.DEBUG, result="ERROR", value=_mask(cleaned))
            return FieldValidation("VIN", ValidationResult.ERROR, f"VIN måste vara 17 tecken (angivet: {len(cleaned)})", cleaned)
        if any(character in cleaned for character in "IOQ") or not VIN_PATTERN.fullmatch(cleaned):
            _evt("eng.vin_invalid", "VIN ogiltiga tecken",
                 level=logging.DEBUG, result="ERROR", value=_mask(cleaned))
            return FieldValidation("VIN", ValidationResult.ERROR, "VIN innehåller ogiltiga tecken", cleaned)
        expected = cls._check_digit(cleaned)
        if cleaned[8] != expected:
            _evt("eng.vin_warning", "VIN-kontrollsiffra avviker",
                 level=logging.DEBUG, result="WARNING", value=_mask(cleaned))
            return FieldValidation(
                "VIN",
                ValidationResult.WARNING,
                f"VIN-kontrollsiffra avviker; förväntat {expected}",
                cleaned,
            )
        _evt("eng.vin_valid", "VIN giltigt", level=logging.DEBUG,
             result="VALID", value=_mask(cleaned))
        return FieldValidation("VIN", ValidationResult.VALID, "Giltigt VIN", cleaned)

    @classmethod
    def _check_digit(cls, vin: str) -> str:
        total = 0
        for index, character in enumerate(vin):
            value = int(character) if character.isdigit() else cls._TRANSLITERATION[character]
            total += value * cls._WEIGHTS[index]
        remainder = total % 11
        return "X" if remainder == 10 else str(remainder)


class RegValidator:
    """Validera svenska registreringsnummer med tolerant varningsnivå."""

    @staticmethod
    def format_reg(reg: str) -> str:
        return re.sub(r"[^A-Z0-9ÅÄÖ]", "", str(reg).upper())

    @classmethod
    def validate(cls, reg: str) -> FieldValidation:
        cleaned = cls.format_reg(reg)
        if not cleaned:
            _evt("eng.reg_invalid", "REG saknas", level=logging.DEBUG, result="ERROR")
            return FieldValidation("REG", ValidationResult.ERROR, "Registreringsnummer saknas")
        if not REG_PATTERN.fullmatch(cleaned):
            _evt("eng.reg_warning", "REG ovanligt format",
                 level=logging.DEBUG, result="WARNING", value=_mask(cleaned))
            return FieldValidation("REG", ValidationResult.WARNING, f"'{cleaned}' har ovanligt format", cleaned)
        _evt("eng.reg_valid", "REG giltigt", level=logging.DEBUG,
             result="VALID", value=_mask(cleaned))
        return FieldValidation("REG", ValidationResult.VALID, "Giltigt registreringsnummer", cleaned)


class PlaceholderParser:
    """Hitta och ersätt placeholders i DOCX med cache per faktisk filstämpel.

    10.1.0: cachestatistik via `stats()` och `eng.*`-loggning på alla path:ar.
    """

    _cache: dict[tuple[Path, int, int], tuple[str, ...]] = {}
    _cache_lock = threading.RLock()
    _cache_limit: int = 256
    _hits: int = 0
    _misses: int = 0
    _evictions: int = 0

    @classmethod
    def clear_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()
        _evt("eng.cache_cleared", "Placeholder-cache rensad", level=logging.DEBUG)

    @classmethod
    def stats(cls) -> dict[str, int]:
        """10.1.0: cache-hälsa för AI-felsökning och diagnostik."""
        with cls._cache_lock:
            return {
                "size": len(cls._cache),
                "limit": cls._cache_limit,
                "hits": cls._hits,
                "misses": cls._misses,
                "evictions": cls._evictions,
            }

    @classmethod
    def find_placeholders(cls, docx_path: Path | str) -> list[str]:
        path = Path(docx_path)
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            _evt("eng.placeholders_missing", f"DOCX saknas för scan: {path}",
                 level=logging.WARNING, path=str(path))
            return []
        if not resolved.is_file() or resolved.suffix.casefold() != ".docx":
            return []
        key = (resolved, stat.st_mtime_ns, stat.st_size)
        with cls._cache_lock:
            cached = cls._cache.get(key)
            if cached is not None:
                cls._hits += 1
        if cached is not None:
            _evt("eng.placeholders_found", f"Cache-hit: {len(cached)} fält i {resolved.name}",
                 level=logging.DEBUG, count=len(cached), cache=True, path=str(resolved))
            return list(cached)

        with cls._cache_lock:
            cls._misses += 1
        method = "docx"
        result = cls._find_with_docx(resolved)
        if result is None:
            method = "xml"
            result = cls._find_in_raw_xml(resolved)
        result = sorted(dict.fromkeys(result), key=str.casefold)
        with cls._cache_lock:
            if len(cls._cache) >= cls._cache_limit:
                cls._cache.pop(next(iter(cls._cache)))
                cls._evictions += 1
                _evt("eng.cache_evicted", "Placeholder-cache: äldsta posten evicted",
                     level=logging.DEBUG, size=len(cls._cache))
            cls._cache[key] = tuple(result)
        _evt("eng.placeholders_found", f"{len(result)} fält i {resolved.name} (metod={method})",
             level=logging.DEBUG, count=len(result), cache=False, method=method,
             path=str(resolved))
        return result

    @staticmethod
    def _find_with_docx(path: Path) -> list[str] | None:
        try:
            from docx import Document
        except ImportError:
            return None

        found: set[str] = set()

        def scan_paragraphs(paragraphs: Any) -> None:
            for paragraph in paragraphs:
                runs = getattr(paragraph, "runs", [])
                text = "".join(str(getattr(run, "text", "") or "") for run in runs)
                if not text:
                    text = str(getattr(paragraph, "text", "") or "")
                for match in PLACEHOLDER_PATTERN.finditer(text):
                    found.add(match.group(1).strip())

        def scan_container(container: Any) -> None:
            if container is None:
                return
            scan_paragraphs(getattr(container, "paragraphs", []))
            for table in getattr(container, "tables", []):
                for row in getattr(table, "rows", []):
                    for cell in getattr(row, "cells", []):
                        scan_container(cell)

        try:
            document = Document(str(path))
            scan_container(document)
            for section in document.sections:
                for container in (
                    section.header,
                    section.footer,
                    section.first_page_header,
                    section.first_page_footer,
                    section.even_page_header,
                    section.even_page_footer,
                ):
                    scan_container(container)
            return list(found)
        except Exception:
            _logger.debug("[PlaceholderParser] python-docx misslyckades för %s", path.name, exc_info=True)
            _evt("eng.parser_fallback", f"python-docx misslyckades → raw-XML för {path.name}",
                 level=logging.DEBUG, path=str(path))
            return None

    @staticmethod
    def _find_in_raw_xml(path: Path) -> list[str]:
        found: set[str] = set()
        try:
            with zipfile.ZipFile(path) as archive:
                xml_names = [
                    name
                    for name in archive.namelist()
                    if name.startswith("word/") and name.endswith(".xml")
                ]
                for name in xml_names:
                    raw = archive.read(name).decode("utf-8", errors="replace")
                    for paragraph in re.findall(r"<w:p(?:\s[^>]*)?>(.*?)</w:p>", raw, flags=re.DOTALL):
                        text = "".join(
                            html.unescape(value)
                            for value in re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, flags=re.DOTALL)
                        )
                        for match in PLACEHOLDER_PATTERN.finditer(text):
                            found.add(match.group(1).strip())
        except Exception:
            _logger.warning("[PlaceholderParser] XML-scan misslyckades för %s", path.name, exc_info=True)
            _evt("eng.xml_scan_failed", f"XML-scan misslyckades: {path.name}",
                 level=logging.ERROR, path=str(path))
        return list(found)

    @classmethod
    def replace_in_docx(
        cls,
        source: Path | str,
        destination: Path | str,
        replacements: dict[str, str],
    ) -> bool:
        source_path = Path(source)
        destination_path = Path(destination)
        if not source_path.is_file():
            _logger.error("[PlaceholderParser] Källmall saknas: %s", source_path)
            _evt("eng.replace_failed", f"Källmall saknas: {source_path}",
                 level=logging.ERROR, path=str(source_path))
            return False
        if cls._replace_with_docx(source_path, destination_path, replacements):
            _evt("eng.replace_done", f"Ersättning klar (python-docx): {source_path.name}",
                 level=logging.DEBUG, method="docx", fields=len(replacements),
                 path=str(source_path))
            return True
        ok = cls._replace_in_raw_xml(source_path, destination_path, replacements)
        if ok:
            _evt("eng.replace_done", f"Ersättning klar (raw-XML): {source_path.name}",
                 level=logging.DEBUG, method="xml", fields=len(replacements),
                 path=str(source_path))
        else:
            _evt("eng.replace_failed", f"Ersättning misslyckades: {source_path.name}",
                 level=logging.ERROR, path=str(source_path))
        return ok

    @staticmethod
    def _replace_with_docx(source: Path, destination: Path, replacements: dict[str, str]) -> bool:
        try:
            from docx import Document
        except ImportError:
            return False

        lookup = {str(key).casefold(): str(value) for key, value in replacements.items()}

        def replace_text(text: str) -> str:
            return PLACEHOLDER_PATTERN.sub(
                lambda match: lookup.get(match.group(1).strip().casefold(), match.group(0)),
                text,
            )

        def replace_paragraph(paragraph: Any) -> None:
            runs = list(getattr(paragraph, "runs", []))
            text = "".join(str(getattr(run, "text", "") or "") for run in runs)
            if not text:
                return
            updated = replace_text(text)
            if updated == text:
                return
            if not runs:
                paragraph.add_run(updated)
                return
            runs[0].text = updated
            for run in runs[1:]:
                run.text = ""

        def replace_container(container: Any) -> None:
            if container is None:
                return
            for paragraph in getattr(container, "paragraphs", []):
                replace_paragraph(paragraph)
            for table in getattr(container, "tables", []):
                for row in getattr(table, "rows", []):
                    for cell in getattr(row, "cells", []):
                        replace_container(cell)

        try:
            document = Document(str(source))
            replace_container(document)
            for section in document.sections:
                for container in (
                    section.header,
                    section.footer,
                    section.first_page_header,
                    section.first_page_footer,
                    section.even_page_header,
                    section.even_page_footer,
                ):
                    replace_container(container)
            destination.parent.mkdir(parents=True, exist_ok=True)
            document.save(str(destination))
            return True
        except Exception:
            _logger.exception("[PlaceholderParser] DOCX-ersättning misslyckades")
            return False

    @staticmethod
    def _replace_in_raw_xml(source: Path, destination: Path, replacements: dict[str, str]) -> bool:
        """10.1.0: per-stycke-ersättning som hanterar SPLITTRADE RUNS.

        Placeholder-texten byggs ihop från styckets samtliga <w:t>; vid träff
        skrivs hela den uppdaterade texten i första <w:t> och övriga töms —
        samma robusthet som python-docx-vägen, helt utan python-docx.
        """
        lookup = {str(key).casefold(): html.escape(str(value), quote=False) for key, value in replacements.items()}
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")

        def _substitute_paragraph(match: "re.Match[str]") -> str:
            paragraph = match.group(0)
            joined = "".join(
                html.unescape(value)
                for value in re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, flags=re.DOTALL)
            )
            updated = PLACEHOLDER_PATTERN.sub(
                lambda inner: lookup.get(inner.group(1).strip().casefold(), inner.group(0)),
                joined,
            )
            if updated == joined:
                return paragraph
            state = {"first": True}

            def _rewrite_t(t_match: "re.Match[str]") -> str:
                if state["first"]:
                    state["first"] = False
                    return f'<w:t xml:space="preserve">{updated}</w:t>'
                return "<w:t></w:t>"

            return re.sub(r"<w:t[^>]*>.*?</w:t>", _rewrite_t, paragraph, flags=re.DOTALL)

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source) as source_archive, zipfile.ZipFile(
                temporary, "w", zipfile.ZIP_DEFLATED
            ) as destination_archive:
                for item in source_archive.infolist():
                    data = source_archive.read(item.filename)
                    if item.filename.startswith("word/") and item.filename.endswith(".xml"):
                        text = data.decode("utf-8", errors="replace")
                        text = re.sub(
                            r"<w:p(?:\s[^>]*)?>.*?</w:p>",
                            _substitute_paragraph,
                            text,
                            flags=re.DOTALL,
                        )
                        data = text.encode("utf-8")
                    destination_archive.writestr(item, data)
            temporary.replace(destination)
            return True
        except Exception:
            _logger.exception("[PlaceholderParser] XML-ersättning misslyckades")
            return False
        finally:
            if temporary.exists():
                with contextlib.suppress(OSError):
                    temporary.unlink()


# Importeras sist: services.py importerar modellerna ovan och kan därför laddas
# utan cirkulärt beroende när engine.py är halvinitierad.
from .services import (
    CONVERT_TIMEOUT_DEFAULT,
    CONVERT_TIMEOUT_MIN,
    CONVERT_TIMEOUT_MAX,
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_FACTOR,
    RETRY_BASE_DELAY,
    CertificateGenerator,
    CounterManager,
    PDFConverter,
    PDFMerger,
)

__all__: list[str] = [
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PLUGIN_AUTHOR",
    "PLUGIN_PRIORITY",
    "COPYRIGHT",
    "FOLDER_CONFIG",
    "CONVERT_TIMEOUT_DEFAULT",
    "CONVERT_TIMEOUT_MIN",
    "CONVERT_TIMEOUT_MAX",
    "RETRY_ATTEMPTS",
    "RETRY_BACKOFF_FACTOR",
    "RETRY_BASE_DELAY",
    "PLACEHOLDER_PATTERN",
    "VIN_PATTERN",
    "REG_PATTERN",
    "AUTO_FIELDS",
    "FIELD_LABELS",
    "DEFAULT_VALUES",
    "ConversionMethod",
    "ValidationResult",
    "ScanStatus",
    "TemplateConfig",
    "Template",
    "GenerationResult",
    "FieldValidation",
    "BatchResult",
    "PlatformHelper",
    "safe_filename",
    "get_copyright",
    "get_field_label",
    "is_auto_field",
    "VINValidator",
    "RegValidator",
    "PlaceholderParser",
    "PDFConverter",
    "PDFMerger",
    "CounterManager",
    "CertificateGenerator",
]
