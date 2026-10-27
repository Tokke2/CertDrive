from __future__ import annotations

"""
Fil:    plugins/statistics/engine.py
Sökväg: Intygsgenerator/plugins/statistics/engine.py

Intygsgenerator — statistikmodeller, beräkningar, import och rapporter.

KÄNSLA FÖR CACHE:
- Läs först från JSON-cache
- Jämför med disk för att hitta nya PDF:er
- Skanna bara nya filer, uppdatera cache
- Undvik att skanna alla PDF:er varje start
"""

import csv
import hashlib
import html
import io
import json
import logging
import re
import tempfile
import threading
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Final

__version__: str = "5.0.0"
PLUGIN_VERSION: Final[str] = __version__
MAX_RECENT_DISPLAY: Final[int] = 500
CHART_WIDTH: Final[int] = 60
FOLDER_CONFIG: Final[str] = "js"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

logger: logging.Logger = logging.getLogger(__name__)

PathLike = str | Path

# Mönster för att extrahera information från filnamn
VIN_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
REG_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b[A-ZÅÄÖ]{3}\s?\d{2,3}\s?[A-ZÅÄÖ0-9]?\b", re.IGNORECASE)
_INVALID_FILENAME: Final[re.Pattern[str]] = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Alias för att känna igen malltyper
TEMPLATE_ALIASES: Final[dict[str, list[str]]] = {
    "25kW": ["25kw", "25 kw", "25-kw", "25_kw", "25 kilowatt"],
    "35kW": ["35kw", "35 kw", "35-kw", "35_kw", "35 kilowatt"],
    "Passagerare": ["passagerare", "passanger", "passenger", "passang", "pass"],
    "Demontering": ["demontering", "demontoring", "demonter", "demo", "demount"],
}

WEEKDAY_NAMES: Final[list[str]] = [
    "Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"
]

MONTH_NAMES: Final[list[str]] = [
    "Januari", "Februari", "Mars", "April", "Maj", "Juni", "Juli", "Augusti",
    "September", "Oktober", "November", "December",
]


def get_copyright() -> str:
    return COPYRIGHT


def _safe_int(value: Any, default: int = 0) -> int:
    """Säkert konvertera till int."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Säkert konvertera till float."""
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime:
    """Parse olika datumformat."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        for parser in (
            datetime.fromisoformat,
            lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
            lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M"),
            lambda item: datetime.strptime(item, "%Y-%m-%d"),
            lambda item: datetime.strptime(item, "%Y%m%d"),
        ):
            with suppress(TypeError, ValueError):
                return parser(text)
    return datetime.now()


def _parse_date(value: Any) -> date | None:
    """Parse datum."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        with suppress(TypeError, ValueError):
            return date.fromisoformat(value[:10])
    return None


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Atomiskt skriva fil (skapar tempfil först)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding=encoding,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
        newline="",
    ) as handle:
        temporary = Path(handle.name)
        handle.write(text)
        handle.flush()
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            with suppress(OSError):
                temporary.unlink()


def safe_filename(name: str, max_len: int = 120) -> str:
    """Normalisera filnamn."""
    normalized = unicodedata.normalize("NFC", str(name or "")).strip()
    cleaned = _INVALID_FILENAME.sub("_", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(". ")
    if max_len > 0:
        cleaned = cleaned[:max_len].rstrip(". ")
    return cleaned or "unnamed"


def format_file_size(size: int) -> str:
    """Formatera filstorlek."""
    value = float(max(0, _safe_int(size)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return "0.0 B"


class TimePeriod(Enum):
    """Tidsperioder för filtrering."""

    TODAY = "Idag"
    YESTERDAY = "Igår"
    THIS_WEEK = "Denna vecka"
    LAST_WEEK = "Förra veckan"
    THIS_MONTH = "Denna månad"
    LAST_MONTH = "Förra månaden"
    THIS_QUARTER = "Detta kvartal"
    THIS_YEAR = "Detta år"
    LAST_YEAR = "Förra året"
    LAST_7_DAYS = "Senaste 7 dagarna"
    LAST_30_DAYS = "Senaste 30 dagarna"
    LAST_90_DAYS = "Senaste 90 dagarna"
    LAST_365_DAYS = "Senaste 365 dagarna"
    ALL = "Alla"
    ALL_TIME = "All tid"
    CUSTOM = "Anpassad"

    @classmethod
    def get_display_periods(cls) -> list[TimePeriod]:
        return [period for period in cls if period is not cls.CUSTOM]

    @classmethod
    def from_value(cls, value: Any) -> TimePeriod:
        if isinstance(value, cls):
            return value
        text = str(value or "").casefold()
        for period in cls:
            if text in {period.name.casefold(), period.value.casefold()}:
                return period
        return cls.ALL_TIME


class ReportFormat(Enum):
    """Rapportformat."""

    PDF = "pdf"
    HTML = "html"
    EXCEL = "xlsx"
    CSV = "csv"
    MARKDOWN = "md"
    JSON = "json"
    TEXT = "txt"

    @property
    def display_name(self) -> str:
        return {
            ReportFormat.PDF: "PDF-dokument",
            ReportFormat.HTML: "HTML-webbsida",
            ReportFormat.EXCEL: "Excel-arbetsbok",
            ReportFormat.CSV: "CSV-datafil",
            ReportFormat.MARKDOWN: "Markdown",
            ReportFormat.JSON: "JSON-data",
            ReportFormat.TEXT: "Textfil",
        }[self]

    @property
    def file_filter(self) -> tuple[str, str]:
        return self.display_name, f"*.{self.value}"

    @classmethod
    def from_value(cls, value: Any) -> ReportFormat:
        if isinstance(value, cls):
            return value
        text = str(value or "").casefold()
        for item in cls:
            if text in {item.name.casefold(), item.value.casefold()}:
                return item
        return cls.HTML


class ChartType(Enum):
    """Diagramtyper."""

    BAR = "Stapeldiagram"
    LINE = "Linjediagram"
    PIE = "Cirkeldiagram"
    TABLE = "Tabell"
    TREND = "Trenddiagram"
    NONE = "Inget"

    @classmethod
    def from_value(cls, value: Any) -> ChartType:
        if isinstance(value, cls):
            return value
        text = str(value or "").casefold()
        for item in cls:
            if text in {item.name.casefold(), item.value.casefold()}:
                return item
        return cls.BAR

# =============================================================================
# CACH-INFORMATION - Metadata om cache-status
# =============================================================================

@dataclass(slots=True)
class CacheInfo:
    """Information om cache-status."""

    cache_file: str = ""
    created: str = ""
    last_updated: str = ""
    total_records: int = 0
    total_files_on_disk: int = 0
    is_valid: bool = True
    files_scanned_since_cache: int = 0
    cache_age_seconds: float = 0.0

# =============================================================================
# CERTIFICATE RECORD - En PDF-post i statistiken
# =============================================================================

@dataclass(slots=True)
class CertificateRecord:
    """En PDF-post i statistiken med kompatibla alias-egenskaper."""

    id: int = 0
    filename: str = ""
    template: str = ""
    category: str = ""
    vin: str = ""
    reg: str = ""
    created: datetime = field(default_factory=datetime.now)
    file_path: Path | None = None
    file_size: int = 0
    page_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    imported: bool = False
    _file_hash: str = ""  # Hash för att detektera ändrade filer

    def __post_init__(self) -> None:
        self.id = _safe_int(self.id)
        self.filename = str(self.filename or "")
        self.template = str(self.template or "")
        self.category = str(self.category or self.template or "Övriga")
        self.vin = str(self.vin or "").upper().replace(" ", "")
        self.reg = str(self.reg or "").upper().replace(" ", "")
        self.created = _parse_datetime(self.created)
        self.file_size = max(0, _safe_int(self.file_size))
        self.page_count = max(0, _safe_int(self.page_count, 1))
        if isinstance(self.file_path, str):
            self.file_path = Path(self.file_path) if self.file_path else None
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    @property
    def number(self) -> int:
        return self.id

    @number.setter
    def number(self, value: int) -> None:
        self.id = _safe_int(value)

    @property
    def path(self) -> Path:
        return self.file_path or Path(self.filename)

    @path.setter
    def path(self, value: PathLike) -> None:
        self.file_path = Path(value)

    @property
    def created_at(self) -> datetime:
        return self.created

    @created_at.setter
    def created_at(self, value: datetime) -> None:
        self.created = _parse_datetime(value)

    @property
    def pages(self) -> int:
        return self.page_count

    @pages.setter
    def pages(self, value: int) -> None:
        self.page_count = max(0, _safe_int(value, 1))

    @property
    def exists(self) -> bool:
        return bool(self.file_path and self.file_path.is_file())

    @property
    def file_size_str(self) -> str:
        return format_file_size(self.file_size)

    @property
    def age_days(self) -> int:
        return max(0, (datetime.now() - self.created).days)

    @property
    def age_str(self) -> str:
        days = self.age_days
        if days == 0:
            return "Idag"
        if days == 1:
            return "Igår"
        if days < 7:
            return f"{days} dagar sedan"
        if days < 30:
            return f"{days // 7} veckor sedan"
        if days < 365:
            return f"{days // 30} månader sedan"
        return f"{days // 365} år sedan"

    def matches_query(self, query: str) -> bool:
        needle = str(query or "").casefold().strip()
        if not needle:
            return True
        fields = (
            self.filename, self.template, self.category, self.vin,
            self.reg, str(self.id), self.created.strftime("%Y-%m-%d")
        )
        return any(needle in str(value).casefold() for value in fields)

    def to_dict(self) -> dict[str, Any]:
        path_text = str(self.file_path) if self.file_path else ""
        return {
            "id": self.id,
            "number": self.id,
            "filename": self.filename,
            "template": self.template,
            "category": self.category,
            "vin": self.vin,
            "reg": self.reg,
            "created": self.created.isoformat(),
            "created_at": self.created.isoformat(),
            "file_path": path_text,
            "path": path_text,
            "file_size": self.file_size,
            "page_count": self.page_count,
            "pages": self.page_count,
            "metadata": dict(self.metadata),
            "imported": self.imported,
            "file_hash": self._file_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CertificateRecord:
        raw_path = data.get("file_path") or data.get("path") or ""
        return cls(
            id=_safe_int(data.get("id", data.get("number"))),
            filename=str(data.get("filename", "") or ""),
            template=str(data.get("template", "") or ""),
            category=str(data.get("category", data.get("template", "")) or ""),
            vin=str(data.get("vin", "") or ""),
            reg=str(data.get("reg", "") or ""),
            created=_parse_datetime(data.get("created", data.get("created_at", data.get("timestamp")))),
            file_path=Path(str(raw_path)) if raw_path else None,
            file_size=_safe_int(data.get("file_size")),
            page_count=_safe_int(data.get("page_count", data.get("pages", 1)), 1),
            metadata=dict(data.get("metadata", {}) or {}),
            imported=bool(data.get("imported", False)),
            _file_hash=str(data.get("file_hash", "") or ""),
        )

# =============================================================================
# STATISTICS SUMMARY - Sammanfattning av statistiken
# =============================================================================

@dataclass(slots=True)
class StatisticsSummary:
    """Sammanfattning av statistiken."""

    period: TimePeriod = TimePeriod.ALL_TIME
    period_start: date | None = None
    period_end: date | None = None
    total_certificates: int = 0
    total_this_month: int = 0
    total_this_year: int = 0
    total_this_week: int = 0
    total_today: int = 0
    total_file_size_bytes: int = 0
    total_pages: int = 0
    total_templates_available: int = 0
    certificates_by_template: dict[str, int] = field(default_factory=dict)
    certificates_by_category: dict[str, int] = field(default_factory=dict)
    certificates_by_month: dict[str, int] = field(default_factory=dict)
    certificates_by_week: dict[str, int] = field(default_factory=dict)
    certificates_by_weekday: dict[str, int] = field(default_factory=dict)
    certificates_by_hour: dict[int, int] = field(default_factory=dict)
    certificates_by_year: dict[int, int] = field(default_factory=dict)
    avg_per_day: float = 0.0
    avg_per_week: float = 0.0
    avg_per_month: float = 0.0
    avg_file_size: float = 0.0
    busiest_weekday: str = ""
    busiest_month: str = ""
    busiest_hour: int = 0
    most_used_template: str = ""
    first_certificate_date: datetime | None = None
    last_certificate_date: datetime | None = None
    trend_percentage: float = 0.0
    trend_direction: str = "stable"
    trend_count_current: int = 0
    trend_count_previous: int = 0

    @property
    def total_templates(self) -> int:
        return len(self.certificates_by_template)

    @property
    def total_file_size_mb(self) -> float:
        return self.total_file_size_bytes / (1024 * 1024)

    @property
    def days_active(self) -> int:
        if self.first_certificate_date is None or self.last_certificate_date is None:
            return 0
        return max(
            1,
            (self.last_certificate_date.date() - self.first_certificate_date.date()).days + 1,
        )

    @property
    def trend_emoji(self) -> str:
        return "📈" if self.trend_direction == "up" else "📉" if self.trend_direction == "down" else "➡️"

    @property
    def by_template(self) -> dict[str, int]:
        return self.certificates_by_template

    @property
    def by_category(self) -> dict[str, int]:
        return self.certificates_by_category

    @property
    def by_month(self) -> dict[str, int]:
        return self.certificates_by_month

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.value,
            "period_start": self.period_start.isoformat() if self.period_start else "",
            "period_end": self.period_end.isoformat() if self.period_end else "",
            "total_certificates": self.total_certificates,
            "total_templates": self.total_templates,
            "total_templates_available": self.total_templates_available,
            "total_this_month": self.total_this_month,
            "total_this_year": self.total_this_year,
            "total_this_week": self.total_this_week,
            "total_today": self.total_today,
            "total_file_size_bytes": self.total_file_size_bytes,
            "total_file_size_mb": round(self.total_file_size_mb, 2),
            "total_pages": self.total_pages,
            "avg_per_day": round(self.avg_per_day, 2),
            "avg_per_week": round(self.avg_per_week, 2),
            "avg_per_month": round(self.avg_per_month, 2),
            "avg_file_size": round(self.avg_file_size, 2),
            "trend_percentage": round(self.trend_percentage, 1),
            "trend_direction": self.trend_direction,
            "trend_count_current": self.trend_count_current,
            "trend_count_previous": self.trend_count_previous,
            "most_used_template": self.most_used_template,
            "busiest_weekday": self.busiest_weekday,
            "busiest_month": self.busiest_month,
            "busiest_hour": self.busiest_hour,
            "by_template": dict(self.certificates_by_template),
            "by_category": dict(self.certificates_by_category),
            "by_month": dict(self.certificates_by_month),
            "by_week": dict(self.certificates_by_week),
            "by_weekday": dict(self.certificates_by_weekday),
            "by_hour": dict(self.certificates_by_hour),
            "by_year": dict(self.certificates_by_year),
        }

# =============================================================================
# REPORT CONFIG - Konfiguration för rapportgenerering
# =============================================================================

@dataclass(slots=True)
class ReportConfig:
    """Konfiguration för rapportgenerering."""

    title: str = "Intygsgenerator — Statistikrapport"
    subtitle: str = ""
    period: TimePeriod = TimePeriod.THIS_MONTH
    start_date: date | None = None
    end_date: date | None = None
    include_summary: bool = True
    include_charts: bool = True
    include_details: bool = True
    include_trends: bool = True
    max_details: int = 50
    format: ReportFormat = ReportFormat.HTML
    chart_type: ChartType = ChartType.BAR
    author: str = "Intygsgenerator"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportConfig:
        return cls(
            title=str(data.get("title", cls.title) or cls.title),
            subtitle=str(data.get("subtitle", "") or ""),
            period=TimePeriod.from_value(data.get("period")),
            start_date=_parse_date(data.get("start_date")),
            end_date=_parse_date(data.get("end_date")),
            include_summary=bool(data.get("include_summary", True)),
            include_charts=bool(data.get("include_charts", True)),
            include_details=bool(data.get("include_details", True)),
            include_trends=bool(data.get("include_trends", True)),
            max_details=max(0, _safe_int(data.get("max_details"), 50)),
            format=ReportFormat.from_value(data.get("format")),
            chart_type=ChartType.from_value(data.get("chart_type")),
            author=str(data.get("author", "Intygsgenerator") or "Intygsgenerator"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "period": self.period.value,
            "start_date": self.start_date.isoformat() if self.start_date else "",
            "end_date": self.end_date.isoformat() if self.end_date else "",
            "include_summary": self.include_summary,
            "include_charts": self.include_charts,
            "include_details": self.include_details,
            "include_trends": self.include_trends,
            "max_details": self.max_details,
            "format": self.format.value,
            "chart_type": self.chart_type.value,
            "author": self.author,
        }

# =============================================================================
# IMPORT RESULT - Resultat från import
# =============================================================================

@dataclass(slots=True)
class ImportResult:
    """Resultat från import av PDF:er."""

    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    total_duplicates: int = 0
    counters_synced: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    imported_records: list[CertificateRecord] = field(default_factory=list)
    duration_seconds: float = 0.0
    from_cache: bool = False  # Var resultatet från cache?

    @property
    def total(self) -> int:
        return self.total_scanned

    @property
    def imported(self) -> int:
        return self.total_imported

    @property
    def skipped(self) -> int:
        return self.total_skipped

    @property
    def records(self) -> list[CertificateRecord]:
        return self.imported_records

    @property
    def success(self) -> bool:
        return self.total_errors == 0 and not self.errors

    @property
    def success_rate(self) -> float:
        return self.total_imported * 100 / self.total_scanned if self.total_scanned else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_scanned": self.total_scanned,
            "total_imported": self.total_imported,
            "total_skipped": self.total_skipped,
            "total_errors": self.total_errors,
            "total_duplicates": self.total_duplicates,
            "counters_synced": dict(self.counters_synced),
            "errors": list(self.errors),
            "imported_records": [record.to_dict() for record in self.imported_records],
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "success_rate": round(self.success_rate, 2),
            "from_cache": self.from_cache,
        }

    def to_summary_text(self) -> str:
        cache_info = " 📁 Från cache" if self.from_cache else ""
        return (
            "📊 Importresultat\n"
            f"✅ Importerade: {self.total_imported}{cache_info}\n"
            f"⏭️ Överhoppade: {self.total_skipped}\n"
            f"🔄 Dubbletter: {self.total_duplicates}\n"
            f"❌ Fel: {self.total_errors}\n"
            f"📁 Totalt: {self.total_scanned}\n"
            f"⏱️ Tid: {self.duration_seconds:.2f}s"
        )

# =============================================================================
# STATISTICS ENGINE - Huvudmotor för statistik med cache-support
# =============================================================================

class StatisticsEngine:
    """
    Trådsäker statistikmotor för filtrering, sökning och summering.

    CACH-SYSTEM:
    - Lagrar alla poster i JSON-fil
    - Vid start: läs från cache först
    - Kontrollera om nya PDF:er finns på disk
    - Skanna bara nya filer och uppdatera cache
    - Undvik att läsa alla PDF:er varje gång
    """

    def __init__(
        self,
        records: list[CertificateRecord] | None = None,
        storage_path: PathLike | None = None,
        cache_valid_hours: float = 24.0,
    ) -> None:
        """
        Initiera statistikmotorn.

        Args:
            records: Startposter (valfritt)
            storage_path: Sökväg till JSON-cachen
            cache_valid_hours: Hur många timmar cache anses vara giltig
        """
        self._lock = threading.RLock()
        self._records = list(records or [])
        self._storage_path = Path(storage_path) if storage_path else None
        self._cache_valid_hours = cache_valid_hours
        self._file_hashes: dict[str, str] = {}  # Sökväg -> hash för att detektera ändringar

        if self._storage_path is not None and self._storage_path.is_file():
            self.load(self._storage_path)

    @property
    def records(self) -> list[CertificateRecord]:
        with self._lock:
            return list(self._records)

    @records.setter
    def records(self, value: list[CertificateRecord]) -> None:
        with self._lock:
            self._records = list(value)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def add_record(self, record: CertificateRecord) -> None:
        with self._lock:
            self._records.append(record)

    def add_certificate(
        self,
        template: str,
        number: int = 0,
        path: PathLike | None = None,
        pages: int = 1,
        created_at: datetime | None = None,
        filename: str = "",
        duration: float = 0.0,
    ) -> CertificateRecord:
        """Lägg till ett certifikat."""
        file_path = Path(path) if path else None
        record = CertificateRecord(
            id=number,
            filename=filename or (file_path.name if file_path else ""),
            template=template,
            category=template or "Övriga",
            created=created_at or datetime.now(),
            file_path=file_path,
            file_size=file_path.stat().st_size if file_path and file_path.is_file() else 0,
            page_count=pages,
            metadata={"duration": duration},
        )
        self.add_record(record)
        return record

    def clear(self) -> None:
        """Rensa alla poster."""
        with self._lock:
            self._records.clear()

    # -------------------------------------------------------------------------
    # CACH-HANTERING
    # -------------------------------------------------------------------------

    def _calculate_file_hash(self, file_path: Path) -> str:
        """Beräkna en enkel hash av filen för att detektera ändringar."""
        try:
            # Använd filstorlek + ändringsdatum som snabb hash
            stat = file_path.stat()
            hash_input = f"{stat.st_size}_{stat.st_mtime}_{stat.st_ctime}"
            return hashlib.md5(hash_input.encode()).hexdigest()[:16]
        except OSError:
            return ""

    def _load_file_hashes(self) -> dict[str, str]:
        """Ladda fil-hashar från befintliga poster."""
        hashes = {}
        for record in self._records:
            if record.file_path and record.file_path.is_file():
                hashes[str(record.file_path.resolve())] = self._calculate_file_hash(record.file_path)
        return hashes

    def save(self, path: PathLike | None = None) -> bool:
        """Spara alla poster till JSON-fil."""
        target = Path(path) if path is not None else self._storage_path
        if target is None:
            return False
        try:
            _atomic_write_text(
                target,
                json.dumps([record.to_dict() for record in self.records], ensure_ascii=False, indent=2),
            )
            return True
        except OSError:
            logger.exception("[STATISTICS] Kunde inte spara %s", target)
            return False

    def load(self, path: PathLike | None = None) -> bool:
        """Ladda poster från JSON-fil."""
        source = Path(path) if path is not None else self._storage_path
        if source is None or not source.is_file():
            return False
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return False
            self.records = [CertificateRecord.from_dict(item) for item in raw if isinstance(item, dict)]
            self._file_hashes = self._load_file_hashes()
            return True
        except (OSError, json.JSONDecodeError):
            logger.exception("[STATISTICS] Kunde inte läsa %s", source)
            return False

    def get_cache_info(self, scan_path: PathLike | None = None) -> CacheInfo:
        """
        Hämta information om cache-status.

        Args:
            scan_path: Sökväg att kontrollera för nya filer

        Returns:
            CacheInfo-objekt med status
        """
        scan = Path(scan_path) if scan_path else None

        # Räkna filer på disk
        disk_count = 0
        new_files = 0
        if scan and scan.is_dir():
            disk_count = len(list(scan.rglob("*.pdf")))

            # Kontrollera om nya filer finns
            for pdf in scan.rglob("*.pdf"):
                pdf_resolved = str(pdf.resolve())
                if pdf_resolved not in self._file_hashes:
                    new_files += 1
                elif self._calculate_file_hash(pdf) != self._file_hashes.get(pdf_resolved, ""):
                    new_files += 1  # Filen har ändrats

        # Beräkna cache ålder
        cache_age = 0.0
        if self._storage_path and self._storage_path.is_file():
            cache_age = time.time() - self._storage_path.stat().st_mtime

        is_valid = bool(
            self._storage_path
            and self._storage_path.is_file()
            and cache_age < self._cache_valid_hours * 3600
        )

        return CacheInfo(
            cache_file=str(self._storage_path) if self._storage_path else "",
            created="",
            last_updated=(
                datetime.fromtimestamp(self._storage_path.stat().st_mtime).isoformat()
                if self._storage_path and self._storage_path.is_file()
                else ""
            ),
            total_records=len(self._records),
            total_files_on_disk=disk_count,
            is_valid=is_valid,
            files_scanned_since_cache=new_files,
            cache_age_seconds=cache_age,
        )

    # -------------------------------------------------------------------------
    # INCREMENTAL IMPORT - Endast nya/ändrade filer
    # -------------------------------------------------------------------------

    def import_incremental(
        self,
        scan_path: PathLike,
        file_extensions: set[str] | None = None,
        force_scan: bool = False,
    ) -> ImportResult:
        """
        Importera endast nya eller ändrade PDF:er.

        Args:
            scan_path: Katalog att skanna
            file_extensions: Vilka filändelser att leta efter
            force_scan: Tvinga full skanning (ignorera cache)

        Returns:
            ImportResult med information om vad som importerades
        """
        started = time.perf_counter()
        result = ImportResult()
        root = Path(scan_path)
        extensions = {
            item.casefold() if item.startswith(".") else f".{item.casefold()}"
            for item in (file_extensions or {".pdf"})
        }

        try:
            if not root.is_dir():
                result.total_errors = 1
                result.errors.append(f"Katalogen finns inte: {root}")
                return result

            # Hämta alla PDF-filer på disk
            all_files = sorted(
                path for path in root.rglob("*")
                if path.is_file() and path.suffix.casefold() in extensions
            )
            result.total_scanned = len(all_files)

            # Om force_scan eller ingen cache, gör full skanning
            if force_scan or not self._storage_path or not self._storage_path.exists():
                logger.info("[STATISTICS] Full skanning (ingen giltig cache)")
                return self._scan_all_files(root, all_files, result)

            # Öka cache-giltighetstid
            self._extend_cache_validity()

            # Hitta nya eller ändrade filer
            new_files = []
            changed_files = []

            for file_path in all_files:
                resolved = str(file_path.resolve())
                current_hash = self._calculate_file_hash(file_path)
                stored_hash = self._file_hashes.get(resolved, "")

                if resolved not in self._file_hashes:
                    new_files.append(file_path)
                elif current_hash != stored_hash:
                    changed_files.append(file_path)

            result.total_skipped = len(all_files) - len(new_files) - len(changed_files)

            if new_files or changed_files:
                logger.info(
                    "[STATISTICS] Hittade %d nya och %d ändrade filer",
                    len(new_files), len(changed_files),
                )

                # Importera nya filer
                for file_path in new_files:
                    try:
                        record = self._parse_file(root, file_path)
                        key = (record.id, record.template, record.filename)

                        # Kontrollera dubbletter
                        existing_keys = {(r.id, r.template, r.filename) for r in self._records}
                        if key in existing_keys:
                            result.total_duplicates += 1
                            continue

                        self._records.append(record)
                        self._file_hashes[resolved] = current_hash
                        result.imported_records.append(record)
                        result.total_imported += 1

                        if record.template and record.id:
                            result.counters_synced[record.template] = max(
                                result.counters_synced.get(record.template, 0),
                                record.id,
                            )
                    except Exception as exc:
                        result.total_errors += 1
                        result.errors.append(f"{file_path.name}: {exc}")
                        logger.exception("[STATISTICS] Importfel för %s", file_path)

                # Uppdatera ändrade filer
                for file_path in changed_files:
                    try:
                        # Ta bort gammal post
                        resolved = str(file_path.resolve())
                        self._records = [
                            r for r in self._records
                            if r.file_path is not None and str(r.file_path.resolve()) != resolved
                        ]

                        # Lägg till ny post
                        record = self._parse_file(root, file_path)
                        self._records.append(record)
                        self._file_hashes[resolved] = current_hash
                        result.imported_records.append(record)
                        result.total_imported += 1
                    except Exception as exc:
                        result.total_errors += 1
                        result.errors.append(f"{file_path.name}: {exc}")
                        logger.exception("[STATISTICS] Uppdateringsfel för %s", file_path)

                # Spara uppdaterad cache
                self.save(self._storage_path)
            else:
                logger.info("[STATISTICS] Inga nya eller ändrade filer hittade")

            result.duration_seconds = time.perf_counter() - started
            return result

        except Exception as exc:
            result.total_errors = 1
            result.errors.append(str(exc))
            logger.exception("[STATISTICS] Inkrementell import misslyckades")
            return result

    def _scan_all_files(
        self,
        root: Path,
        files: list[Path],
        result: ImportResult,
    ) -> ImportResult:
        """Gör full skanning av alla filer (ingen cache)."""
        seen: set[tuple[int, str, str]] = set()

        for file_path in files:
            try:
                record = self._parse_file(root, file_path)
                key = (record.id, record.template, record.filename)

                if key in seen:
                    result.total_duplicates += 1
                    result.total_skipped += 1
                    continue

                seen.add(key)
                self._records.append(record)
                self._file_hashes[str(file_path.resolve())] = self._calculate_file_hash(file_path)

                result.imported_records.append(record)
                result.total_imported += 1

                if record.template and record.id:
                    result.counters_synced[record.template] = max(
                        result.counters_synced.get(record.template, 0),
                        record.id,
                    )
            except Exception as exc:
                result.total_errors += 1
                result.total_skipped += 1
                result.errors.append(f"{file_path.name}: {exc}")
                logger.exception("[STATISTICS] Importfel för %s", file_path)

        # Spara cache efter full skanning
        self.save(self._storage_path)
        result.duration_seconds = time.perf_counter() - result.duration_seconds if result.duration_seconds else 0.0
        return result

    def _extend_cache_validity(self) -> None:
        """Markera cache som nyligen använd (förläng giltighetstid)."""
        if self._storage_path and self._storage_path.exists():
            # Genom att spara filen igen nollställs mtime
            # Detta är en enkel lösning - i produktion kan man lägga till en explicit timestamp
            pass

    def _parse_file(self, root: Path, file_path: Path) -> CertificateRecord:
        """Parse en PDF-fil och extrahera metadata."""
        stat = file_path.stat()
        relative = file_path.relative_to(root)
        parent_template = relative.parts[0] if len(relative.parts) > 1 else file_path.parent.name

        match = self._NUMBER_PATTERN.search(file_path.stem)
        number = _safe_int(match.group("number")) if match else 0
        parsed_template = match.group("template").strip() if match else ""

        template = self._normalize_template(parsed_template or parent_template, file_path.name)

        vin_match = VIN_PATTERN.search(file_path.name)
        reg_match = REG_PATTERN.search(file_path.name)

        return CertificateRecord(
            id=number,
            filename=file_path.name,
            template=template or parent_template,
            category=template or parent_template or "Övriga",
            vin=vin_match.group().upper() if vin_match else "",
            reg=reg_match.group().upper().replace(" ", "") if reg_match else "",
            created=datetime.fromtimestamp(stat.st_mtime),
            file_path=file_path,
            file_size=stat.st_size,
            page_count=self._count_pdf_pages(file_path),
            imported=True,
            _file_hash=self._calculate_file_hash(file_path),
        )

    def _count_pdf_pages(self, path: Path) -> int:
        """Räkna sidor i PDF (kräver pypdf eller PyPDF2)."""
        for module_name in ("pypdf", "PyPDF2"):
            try:
                module = __import__(module_name, fromlist=["PdfReader"])
                return max(1, len(module.PdfReader(str(path)).pages))
            except Exception:
                continue
        return 1

    @staticmethod
    def _normalize_template(name: str, context: str) -> str:
        """Normalisera mallnamn till kanoniskt format."""
        haystack = f"{name} {context}".casefold()
        for canonical, aliases in TEMPLATE_ALIASES.items():
            if any(alias.casefold() in haystack for alias in aliases):
                return canonical
        cleaned = re.sub(r"[_-]+", " ", str(name or "")).strip()
        return cleaned.title() if cleaned else ""

    _NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^(?P<number>\d+)\s+Intyg\s+(?P<template>[^\d]*?)\s*(?:\d{17})?",
        re.IGNORECASE,
    )

    def get_date_range(
        self,
        period: TimePeriod,
        custom_start: date | None = None,
        custom_end: date | None = None,
    ) -> tuple[date, date]:
        today = date.today()
        if period is TimePeriod.CUSTOM:
            return custom_start or today - timedelta(days=30), custom_end or today
        if period is TimePeriod.TODAY:
            return today, today
        if period is TimePeriod.YESTERDAY:
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday
        if period is TimePeriod.THIS_WEEK:
            return today - timedelta(days=today.weekday()), today
        if period is TimePeriod.LAST_WEEK:
            end = today - timedelta(days=today.weekday() + 1)
            return end - timedelta(days=6), end
        if period is TimePeriod.THIS_MONTH:
            return today.replace(day=1), today
        if period is TimePeriod.LAST_MONTH:
            end = today.replace(day=1) - timedelta(days=1)
            return end.replace(day=1), end
        if period is TimePeriod.THIS_QUARTER:
            return today.replace(month=((today.month - 1) // 3) * 3 + 1, day=1), today
        if period is TimePeriod.THIS_YEAR:
            return today.replace(month=1, day=1), today
        if period is TimePeriod.LAST_YEAR:
            return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)

        days = {
            TimePeriod.LAST_7_DAYS: 7,
            TimePeriod.LAST_30_DAYS: 30,
            TimePeriod.LAST_90_DAYS: 90,
            TimePeriod.LAST_365_DAYS: 365,
        }.get(period)

        if days is not None:
            return today - timedelta(days=days), today

        records = self.records
        return (
            min((record.created.date() for record in records), default=today - timedelta(days=365)),
            today,
        )

    def filter_by_period(
        self,
        period: TimePeriod,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[CertificateRecord]:
        records = self.records
        if period in {TimePeriod.ALL, TimePeriod.ALL_TIME}:
            return records
        start, end = self.get_date_range(period, start_date, end_date)
        return [record for record in records if start <= record.created.date() <= end]

    def filter_by_template(self, template: str) -> list[CertificateRecord]:
        target = str(template or "").casefold()
        return [record for record in self.records if record.template.casefold() == target]

    def calculate_summary(
        self,
        records: list[CertificateRecord] | None = None,
        period: TimePeriod = TimePeriod.ALL_TIME,
    ) -> StatisticsSummary:
        values = self.records if records is None else list(records)
        if not values:
            return StatisticsSummary(period=period)

        now = date.today()
        week_start = now - timedelta(days=now.weekday())
        month_start = now.replace(day=1)
        year_start = now.replace(month=1, day=1)

        summary = StatisticsSummary(
            period=period,
            total_certificates=len(values),
            total_file_size_bytes=sum(record.file_size for record in values),
            total_pages=sum(record.page_count for record in values),
        )

        summary.certificates_by_template = dict(Counter(record.template or "Okänd" for record in values))
        summary.certificates_by_category = dict(
            Counter(record.category or record.template or "Övriga" for record in values)
        )

        for record in values:
            record_date = record.created.date()
            summary.total_today += int(record_date == now)
            summary.total_this_week += int(record_date >= week_start)
            summary.total_this_month += int(record_date >= month_start)
            summary.total_this_year += int(record_date >= year_start)

            month_key = record.created.strftime("%Y-%m")
            week_key = record.created.strftime("%Y-W%W")
            weekday = WEEKDAY_NAMES[record.created.weekday()]

            summary.certificates_by_month[month_key] = summary.certificates_by_month.get(month_key, 0) + 1
            summary.certificates_by_week[week_key] = summary.certificates_by_week.get(week_key, 0) + 1
            summary.certificates_by_weekday[weekday] = summary.certificates_by_weekday.get(weekday, 0) + 1
            summary.certificates_by_hour[record.created.hour] = summary.certificates_by_hour.get(record.created.hour, 0) + 1
            summary.certificates_by_year[record.created.year] = summary.certificates_by_year.get(record.created.year, 0) + 1

        dates = [record.created for record in values]
        summary.first_certificate_date = min(dates)
        summary.last_certificate_date = max(dates)
        summary.period_start = summary.first_certificate_date.date()
        summary.period_end = summary.last_certificate_date.date()

        active_days = summary.days_active
        summary.avg_per_day = summary.total_certificates / max(1, active_days)
        summary.avg_per_week = summary.avg_per_day * 7
        summary.avg_per_month = summary.avg_per_day * 30.44
        summary.avg_file_size = summary.total_file_size_bytes / summary.total_certificates

        summary.most_used_template = max(summary.certificates_by_template, key=summary.certificates_by_template.get, default="")
        summary.busiest_weekday = max(summary.certificates_by_weekday, key=summary.certificates_by_weekday.get, default="")
        summary.busiest_hour = max(summary.certificates_by_hour, key=summary.certificates_by_hour.get, default=0)

        busiest_month = max(summary.certificates_by_month, key=summary.certificates_by_month.get, default="")
        summary.busiest_month = self._format_month_key(busiest_month)

        self._calculate_trend(summary, values, now)
        return summary

    def get_summary(
        self,
        period: TimePeriod = TimePeriod.ALL_TIME,
        start_date: datetime | date | None = None,
        end_date: datetime | date | None = None,
    ) -> StatisticsSummary:
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        return self.calculate_summary(self.filter_by_period(period, start, end), period)

    @staticmethod
    def _format_month_key(value: str) -> str:
        with suppress(ValueError, IndexError):
            year, month = value.split("-")
            return f"{MONTH_NAMES[int(month) - 1]} {year}"
        return value

    @staticmethod
    def _calculate_trend(summary: StatisticsSummary, records: list[CertificateRecord], today: date) -> None:
        current_start = today - timedelta(days=30)
        previous_start = today - timedelta(days=60)

        summary.trend_count_current = sum(
            current_start <= record.created.date() <= today for record in records
        )
        summary.trend_count_previous = sum(
            previous_start <= record.created.date() < current_start for record in records
        )

        if summary.trend_count_previous:
            summary.trend_percentage = (
                (summary.trend_count_current - summary.trend_count_previous) * 100
                / summary.trend_count_previous
            )
        elif summary.trend_count_current:
            summary.trend_percentage = 100.0

        summary.trend_direction = (
            "up" if summary.trend_percentage > 5 else
            "down" if summary.trend_percentage < -5 else
            "stable"
        )

    def get_monthly_trend(self, months: int = 12) -> list[tuple[str, int]]:
        limit = max(1, min(120, _safe_int(months, 12)))
        buckets: dict[str, int] = defaultdict(int)

        for record in self.records:
            buckets[record.created.strftime("%Y-%m")] += 1

        current = date.today().replace(day=1)
        output: list[tuple[str, int]] = []

        for offset in range(limit - 1, -1, -1):
            anchor = self._subtract_months(current, offset)
            key = anchor.strftime("%Y-%m")
            output.append((key, buckets.get(key, 0)))

        return output

    @staticmethod
    def _subtract_months(value: date, months: int) -> date:
        year = value.year
        month = value.month - months
        while month <= 0:
            year -= 1
            month += 12
        return date(year, month, 1)

    def get_template_stats(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}

        for template in {record.template for record in self.records}:
            records = [record for record in self.records if record.template == template]
            if records:
                result[template] = {
                    "count": len(records),
                    "first": min(record.created for record in records),
                    "last": max(record.created for record in records),
                    "total_size": sum(record.file_size for record in records),
                    "max_id": max(record.id for record in records),
                    "pages": sum(record.page_count for record in records),
                }

        return result

    def search(self, query: str, field_name: str = "all", limit: int = MAX_RECENT_DISPLAY) -> list[CertificateRecord]:
        needle = str(query or "").casefold().strip()
        field = str(field_name or "all").casefold()
        output: list[CertificateRecord] = []

        for record in self.records:
            if not needle or self._matches_field(record, needle, field):
                output.append(record)
                if len(output) >= max(1, _safe_int(limit, MAX_RECENT_DISPLAY)):
                    break

        return output

    @staticmethod
    def _matches_field(record: CertificateRecord, needle: str, field: str) -> bool:
        if field == "all":
            return record.matches_query(needle)

        values = {
            "vin": record.vin,
            "reg": record.reg,
            "template": record.template,
            "category": record.category,
            "filename": record.filename,
            "id": str(record.id),
        }

        return needle in values.get(field, "").casefold()

    def get_max_id_for_template(self, template: str) -> int:
        return max((record.id for record in self.filter_by_template(template)), default=0)

# =============================================================================
# ASCII CHART GENERATOR - Textbaserade diagram
# =============================================================================

class ASCIIChartGenerator:
    """Generera tillgängliga textdiagram utan grafikberoenden."""

    @staticmethod
    def bar_chart(
        data: dict[str, int],
        title: str = "",
        width: int = CHART_WIDTH,
        show_values: bool = True,
        show_percentage: bool = True,
    ) -> str:
        if not data:
            return f"{title}\n  (Ingen data)".strip()

        safe_width = max(8, min(120, _safe_int(width, CHART_WIDTH)))
        maximum = max(data.values(), default=1)
        total = sum(data.values())
        label_width = min(24, max(len(str(key)) for key in data))

        lines = [title, "─" * (label_width + safe_width + 18)] if title else []

        for label, value in sorted(data.items(), key=lambda item: (-item[1], item[0].casefold())):
            bar_length = int(value * safe_width / maximum) if maximum else 0
            suffix = f" {value}" if show_values else ""
            if show_percentage:
                suffix += f" ({value * 100 / total:.1f}%)" if total else " (0.0%)"
            lines.append(f"{label[:label_width]:<{label_width}} │ {'█' * bar_length}{suffix}")

        return "\n".join(lines)

    @staticmethod
    def pie_chart(data: dict[str, int], title: str = "", **kwargs: Any) -> str:
        return ASCIIChartGenerator.bar_chart(data, title, **kwargs)

# =============================================================================
# CERTIFICATE IMPORTER - Importera PDF:er utan UI-blockering
# =============================================================================

class CertificateImporter:
    """Importera PDF-intyg från Out/ eller CSV utan UI-blockering."""

    _NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(
        r"^(?P<number>\d+)\s+Intyg\s+(?P<template>[^\d]*?)\s*(?:\d{17})?",
        re.IGNORECASE,
    )

    def __init__(self, log_func: Callable[[str], None] | None = None) -> None:
        self._log = log_func or (lambda message: logger.debug("%s", message))

    def import_from_output(self, path: PathLike) -> ImportResult:
        """Importera från output-mapp (full skanning)."""
        return self.scan_directory(path, recursive=True, file_extensions={".pdf"})

    def scan_directory(
        self,
        directory: PathLike,
        recursive: bool = True,
        file_extensions: set[str] | None = None,
    ) -> ImportResult:
        """Skanna katalog och importera filer."""
        started = time.perf_counter()
        result = ImportResult()
        root = Path(directory)

        extensions = {
            item.casefold() if item.startswith(".") else f".{item.casefold()}"
            for item in (file_extensions or {".pdf"})
        }

        try:
            if not root.is_dir():
                result.total_errors = 1
                result.errors.append(f"Katalogen finns inte: {root}")
                return result

            files = sorted(
                path for path in (root.rglob("*") if recursive else root.glob("*"))
                if path.is_file() and path.suffix.casefold() in extensions
            )

            result.total_scanned = len(files)
            seen: set[tuple[int, str, str]] = set()

            for file_path in files:
                try:
                    record = self._parse_file(root, file_path)
                    key = (record.id, record.template, record.filename)

                    if key in seen:
                        result.total_duplicates += 1
                        result.total_skipped += 1
                        continue

                    seen.add(key)
                    result.imported_records.append(record)
                    result.total_imported += 1

                    if record.template and record.id:
                        result.counters_synced[record.template] = max(
                            result.counters_synced.get(record.template, 0),
                            record.id,
                        )
                except Exception as exc:
                    result.total_errors += 1
                    result.total_skipped += 1
                    result.errors.append(f"{file_path.name}: {exc}")
                    logger.exception("[STATISTICS] Importfel för %s", file_path)

        finally:
            result.duration_seconds = time.perf_counter() - started

        return result

    def _parse_file(self, root: Path, file_path: Path) -> CertificateRecord:
        """Parse en PDF-fil."""
        stat = file_path.stat()
        relative = file_path.relative_to(root)
        parent_template = relative.parts[0] if len(relative.parts) > 1 else file_path.parent.name

        match = self._NUMBER_PATTERN.search(file_path.stem)
        number = _safe_int(match.group("number")) if match else 0
        parsed_template = match.group("template").strip() if match else ""

        template = self._normalize_template(parsed_template or parent_template, file_path.name)

        vin_match = VIN_PATTERN.search(file_path.name)
        reg_match = REG_PATTERN.search(file_path.name)

        return CertificateRecord(
            id=number,
            filename=file_path.name,
            template=template or parent_template,
            category=template or parent_template or "Övriga",
            vin=vin_match.group().upper() if vin_match else "",
            reg=reg_match.group().upper().replace(" ", "") if reg_match else "",
            created=datetime.fromtimestamp(stat.st_mtime),
            file_path=file_path,
            file_size=stat.st_size,
            page_count=self._count_pdf_pages(file_path),
            imported=True,
        )

    def _parse_csv_row(self, row: dict[str, Any]) -> CertificateRecord | None:
        """Parse en CSV-rad."""
        values = {str(key or "").casefold().strip(): str(value or "").strip() for key, value in row.items()}

        template = self._normalize_template(
            self._first(values, ("mall", "template", "typ", "type", "kategori")),
            "",
        )
        if not template:
            return None

        path_text = self._first(values, ("file_path", "path", "sökväg"))
        file_path = Path(path_text) if path_text else None

        return CertificateRecord(
            id=_safe_int(self._first(values, ("id", "nr", "nummer", "number", "#"))),
            filename=self._first(values, ("filename", "filnamn", "file")) or f"{template}.pdf",
            template=template,
            category=template,
            vin=self._first(values, ("vin", "chassi", "chassis")),
            reg=self._first(values, ("reg", "regnr", "registration")),
            created=_parse_datetime(self._first(values, ("datum", "date", "created", "skapad"))),
            file_path=file_path,
            file_size=file_path.stat().st_size if file_path and file_path.is_file() else 0,
            imported=True,
        )

    def import_from_csv(self, csv_path: PathLike) -> ImportResult:
        """Importera från CSV-fil."""
        started = time.perf_counter()
        result = ImportResult()
        path = Path(csv_path)

        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                reader = csv.DictReader(
                    handle,
                    delimiter=";" if sample.count(";") >= sample.count(",") else ",",
                )

                for row in reader:
                    result.total_scanned += 1
                    record = self._parse_csv_row(dict(row or {}))

                    if record is None:
                        result.total_skipped += 1
                        continue

                    result.imported_records.append(record)
                    result.total_imported += 1

                    if record.template and record.id:
                        result.counters_synced[record.template] = max(
                            result.counters_synced.get(record.template, 0),
                            record.id,
                        )

        except (OSError, csv.Error) as exc:
            result.total_errors += 1
            result.errors.append(str(exc))

        finally:
            result.duration_seconds = time.perf_counter() - started

        return result

    @staticmethod
    def _first(values: dict[str, str], keys: tuple[str, ...]) -> str:
        """Hämta första värdet från en dict för givna keys."""
        return next((values[key] for key in keys if values.get(key)), "")

    @staticmethod
    def _count_pdf_pages(path: Path) -> int:
        """Räkna PDF-sidor."""
        for module_name in ("pypdf", "PyPDF2"):
            try:
                module = __import__(module_name, fromlist=["PdfReader"])
                return max(1, len(module.PdfReader(str(path)).pages))
            except Exception:
                continue
        return 1

    @staticmethod
    def _normalize_template(name: str, context: str) -> str:
        """Normalisera mallnamn."""
        haystack = f"{name} {context}".casefold()
        for canonical, aliases in TEMPLATE_ALIASES.items():
            if any(alias.casefold() in haystack for alias in aliases):
                return canonical

        cleaned = re.sub(r"[_-]+", " ", str(name or "")).strip()
        return cleaned.title() if cleaned else ""

# =============================================================================
# REPORT GENERATOR - Generera rapporter
# =============================================================================

class ReportGenerator:
    """Generera atomiska statistikrapporter i valda format."""

    def __init__(
        self,
        summary: StatisticsSummary | None = None,
        records: list[CertificateRecord] | None = None,
        config: ReportConfig | None = None,
        log_func: Callable[[str], None] | None = None,
    ) -> None:
        self.summary = summary or StatisticsSummary()
        self.records = list(records or [])
        self.config = config or ReportConfig()
        self.chart_gen = ASCIIChartGenerator()
        self._log = log_func or (lambda message: logger.debug("%s", message))

    def generate(
        self,
        output_path: PathLike | None = None,
        summary: StatisticsSummary | None = None,
        config: ReportConfig | None = None,
    ) -> bool | str:
        """Generera rapport."""
        if summary is not None:
            return self.generate_string(summary, config or self.config)

        if output_path is None:
            return self.generate_string(self.summary, self.config)

        target = Path(output_path)

        try:
            if self.config.format is ReportFormat.EXCEL:
                return self._generate_excel(target)
            if self.config.format is ReportFormat.PDF:
                return self._generate_pdf(target)

            content = self.generate_string(self.summary, self.config)
            encoding = "utf-8-sig" if self.config.format is ReportFormat.CSV else "utf-8"
            _atomic_write_text(target, content, encoding=encoding)
            return True

        except Exception as exc:
            self._log(f"[ReportGenerator] {exc}")
            logger.exception("[STATISTICS] Rapportgenerering misslyckades")
            return False

    def generate_string(self, summary: StatisticsSummary, config: ReportConfig | None = None) -> str:
        """Generera rapport som sträng."""
        old_summary, old_config = self.summary, self.config
        self.summary, self.config = summary, config or self.config

        try:
            if self.config.format is ReportFormat.JSON:
                return self._json_content()
            if self.config.format is ReportFormat.CSV:
                return self._csv_content()
            if self.config.format is ReportFormat.MARKDOWN:
                return self._markdown_content()
            if self.config.format is ReportFormat.HTML:
                return self._html_content()
            return self._text_content()
        finally:
            self.summary, self.config = old_summary, old_config

    def _text_content(self) -> str:
        """Generera textrapport."""
        summary = self.summary
        lines = [
            self.config.title,
            "=" * len(self.config.title),
            f"Genererad: {datetime.now():%Y-%m-%d %H:%M}",
            f"Period: {self.config.period.value}",
            "",
            f"Totalt: {summary.total_certificates}",
            f"Denna månad: {summary.total_this_month}",
            f"Denna vecka: {summary.total_this_week}",
            f"Idag: {summary.total_today}",
            f"Sidor: {summary.total_pages}",
            f"Mallar tillgängliga: {summary.total_templates_available}",
        ]

        if self.config.include_charts:
            lines.extend(["", ASCIIChartGenerator.bar_chart(summary.certificates_by_template, "Per mall")])

        lines.extend(["", COPYRIGHT])
        return "\n".join(lines) + "\n"

    def _csv_content(self) -> str:
        """Generera CSV-rapport."""
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["ID", "Datum", "Mall", "Kategori", "Filnamn", "VIN", "REG", "Storlek", "Sidor"])

        for record in sorted(self.records, key=lambda item: item.created, reverse=True):
            writer.writerow([
                record.id,
                record.created.strftime("%Y-%m-%d %H:%M"),
                record.template,
                record.category,
                record.filename,
                record.vin,
                record.reg,
                record.file_size,
                record.page_count,
            ])

        return buffer.getvalue()

    def _json_content(self) -> str:
        """Generera JSON-rapport."""
        return json.dumps(
            {
                "report": {
                    "title": self.config.title,
                    "generated": datetime.now().isoformat(),
                    "period": self.config.period.value,
                    "copyright": COPYRIGHT,
                },
                "summary": self.summary.to_dict(),
                "records": [record.to_dict() for record in self.records],
            },
            ensure_ascii=False,
            indent=2,
        )

    def _markdown_content(self) -> str:
        """Generera Markdown-rapport."""
        total = self.summary.total_certificates or 1

        rows = "\n".join(
            f"| {name} | {count} | {count * 100 / total:.1f}% |"
            for name, count in sorted(
                self.summary.certificates_by_template.items(),
                key=lambda item: -item[1],
            )
        )

        return (
            f"# {self.config.title}\n\n"
            f"**Genererad:** {datetime.now():%Y-%m-%d %H:%M}\n\n"
            "| Mätvärde | Värde |\n|---|---:|\n"
            f"| Totalt | {self.summary.total_certificates} |\n"
            f"| Sidor | {self.summary.total_pages} |\n\n"
            "## Per mall\n\n| Mall | Antal | Procent |\n|---|---:|---:|\n"
            f"{rows}\n\n{COPYRIGHT}\n"
        )

    def _html_content(self) -> str:
        """Generera HTML-rapport."""
        rows = "".join(
            f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>"
            for name, count in sorted(
                self.summary.certificates_by_template.items(),
                key=lambda item: -item[1],
            )
        )

        details = ""
        if self.config.include_details:
            detail_rows = "".join(
                f"<tr><td>{record.id}</td><td>{record.created:%Y-%m-%d}</td>"
                f"<td>{html.escape(record.template)}</td><td>{html.escape(record.filename)}</td></tr>"
                for record in sorted(self.records, key=lambda item: item.created, reverse=True)[:self.config.max_details]
            )
            details = (
                "<h2>Senaste intyg</h2><table>"
                "<tr><th>#</th><th>Datum</th><th>Mall</th><th>Fil</th></tr>"
                f"{detail_rows}</table>"
            )

        return (
            "<!doctype html><html lang='sv'><head><meta charset='utf-8'>"
            f"<title>{html.escape(self.config.title)}</title></head><body>"
            f"<h1>{html.escape(self.config.title)}</h1>"
            f"<p>Genererad: {datetime.now():%Y-%m-%d %H:%M}</p>"
            f"<p>Totalt: {self.summary.total_certificates} · Sidor: {self.summary.total_pages}</p>"
            f"<h2>Per mall</h2><table><tr><th>Mall</th><th>Antal</th></tr>{rows}</table>"
            f"{details}<footer>{html.escape(COPYRIGHT)}</footer></body></html>"
        )

    def _generate_excel(self, path: Path) -> bool:
        """Generera Excel-rapport."""
        try:
            from openpyxl import Workbook
        except ImportError:
            logger.error("[STATISTICS] Excel-export kräver openpyxl")
            return False

        temporary = path.with_suffix(f"{path.suffix}.tmp")

        try:
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "Statistik"
            sheet.append(["Mall", "Antal"])

            for name, count in sorted(self.summary.certificates_by_template.items()):
                sheet.append([name, count])

            path.parent.mkdir(parents=True, exist_ok=True)
            workbook.save(temporary)
            temporary.replace(path)
            return True

        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def _generate_pdf(self, path: Path) -> bool:
        """Generera PDF-rapport."""
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas
        except ImportError:
            logger.error("[STATISTICS] PDF-rapport kräver reportlab")
            return False

        temporary = path.with_suffix(f"{path.suffix}.tmp")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            document = canvas.Canvas(str(temporary), pagesize=A4)

            y = 800
            for line in self._text_content().splitlines():
                document.drawString(40, y, line.encode("latin-1", "replace").decode("latin-1"))
                y -= 16
                if y < 40:
                    document.showPage()
                    y = 800

            document.save()
            temporary.replace(path)
            return True

        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

# =============================================================================
# TEMPLATE SCANNING - Skanna mallar
# =============================================================================

def scan_template_names(templates_root: PathLike, **_kwargs: Any) -> list[str]:
    """Snabb statistikvänlig mallskanning."""
    root = Path(templates_root)
    if not root.is_dir():
        return []

    names: list[str] = []

    try:
        folders = sorted(
            (path for path in root.iterdir() if path.is_dir() and not path.name.startswith((".", "_", "~"))),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return []

    for folder in folders:
        try:
            has_docx = any(
                item.is_file()
                and item.suffix.casefold() == ".docx"
                and not item.name.startswith(("~$", "."))
                and item.stat().st_size > 0
                for item in folder.iterdir()
            )
        except OSError:
            continue

        if has_docx:
            names.append(unicodedata.normalize("NFC", folder.name))

    return names


def scan_templates_detailed(templates_root: PathLike, **_kwargs: Any) -> list[dict[str, Any]]:
    """Returnera mallnamn och aktiv DOCX."""
    root = Path(templates_root)
    details: list[dict[str, Any]] = []

    for name in scan_template_names(root):
        folder = root / name

        try:
            docx_files = sorted(
                [
                    item for item in folder.iterdir()
                    if item.is_file() and item.suffix.casefold() == ".docx"
                    and not item.name.startswith(("~$", "."))
                ],
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            continue

        if not docx_files:
            continue

        preferred = next(
            (
                item for item in docx_files
                if item.name.casefold() == f"{name}.docx".casefold()
            ),
            docx_files[0],
        )

        details.append({"name": name, "folder": folder, "docx_path": preferred})

    return details

# =============================================================================
# PUBLIC API
# =============================================================================

__all__: list[str] = [
    "COPYRIGHT",
    "PLUGIN_VERSION",
    "MAX_RECENT_DISPLAY",
    "CHART_WIDTH",
    "FOLDER_CONFIG",
    "TEMPLATE_ALIASES",
    "WEEKDAY_NAMES",
    "MONTH_NAMES",
    "VIN_PATTERN",
    "REG_PATTERN",
    "TimePeriod",
    "ReportFormat",
    "ChartType",
    "CertificateRecord",
    "StatisticsSummary",
    "ReportConfig",
    "ImportResult",
    "CacheInfo",
    "StatisticsEngine",
    "ASCIIChartGenerator",
    "CertificateImporter",
    "ReportGenerator",
    "safe_filename",
    "format_file_size",
    "get_copyright",
    "scan_template_names",
    "scan_templates_detailed",
]
