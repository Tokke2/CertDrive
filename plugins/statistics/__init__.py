from __future__ import annotations

"""
Fil:    plugins/statistics/__init__.py
Sökväg: Intygsgenerator/plugins/statistics/__init__.py

Intygsgenerator — paket-API, fail-safe statistikexporter och snabb mallskanning.

TemplateScanner läser endast Mallar/<MallNamn>/ och använder en DOCX-filstämpel
för cache. Statistikmotorn och UI-pluginet mjukimporteras sist så att ett
enskilt importfel inte stoppar resten av applikationen.
"""

import csv
import html
import json
import logging
import re
import threading
import unicodedata
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

__version__: str = "10.1.0"
__author__: str = "Rickard Längkvist"
__plugin_name__: str = "Statistik"
__description__: str = "Statistik, rapportering, import och mallskanning för Intygsgenerator"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

PLUGIN_PRIORITY: int = 60
FOLDER_CONFIG: str = "js"
CHART_WIDTH: int = 60
MAX_RECENT_DISPLAY: int = 500

_logger: logging.Logger = logging.getLogger(__name__)
_load_lock = threading.RLock()
_scanner_lock = threading.RLock()

ENGINE_AVAILABLE: bool = False
PLUGIN_AVAILABLE: bool = False
LOAD_ERRORS: dict[str, str] = {}

_TEMPLATE_PATTERN: re.Pattern[str] = re.compile(r"::\s*([A-Za-z0-9_åäöÅÄÖ]+)\s*::")
_IGNORED_FOLDER_PREFIXES: tuple[str, ...] = (".", "_", "~")
_IGNORED_FILE_PREFIXES: tuple[str, ...] = ("~$", ".")


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        with suppress(TypeError, ValueError):
            return datetime.fromisoformat(value)
    return datetime.now()


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFC", str(value).strip())


def _is_temp_docx(path: Path) -> bool:
    return path.name.startswith("~$")


class TimePeriod(Enum):
    """Tidsfilter som används av statistikfliken."""

    ALL = "Alla"
    ALL_TIME = "All tid"
    THIS_MONTH = "Denna månad"
    THIS_WEEK = "Denna vecka"
    TODAY = "Idag"

    @classmethod
    def from_value(cls, value: Any) -> TimePeriod:
        if isinstance(value, cls):
            return value
        text = str(value or "").casefold()
        for item in cls:
            if text in {item.name.casefold(), item.value.casefold()}:
                return item
        return cls.ALL

    @classmethod
    def get_display_periods(cls) -> list[TimePeriod]:
        return [cls.ALL, cls.THIS_MONTH, cls.THIS_WEEK, cls.TODAY]


class ReportFormat(Enum):
    TEXT = "txt"
    HTML = "html"
    JSON = "json"
    CSV = "csv"

    @property
    def display_name(self) -> str:
        return self.value.upper()

    @property
    def file_filter(self) -> tuple[str, str]:
        return self.display_name, f"*.{self.value}"


class ChartType(Enum):
    NONE = "Inget"
    BAR = "Stapeldiagram"
    LINE = "Linjediagram"
    PIE = "Cirkeldiagram"


@dataclass(slots=True)
class CertificateRecord:
    """En statistikpost för ett genererat eller importerat PDF-intyg."""

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

    @property
    def exists(self) -> bool:
        return bool(self.file_path and self.file_path.is_file())

    @property
    def file_size_str(self) -> str:
        value = float(max(0, self.file_size))
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return "0.0 B"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "template": self.template,
            "category": self.category,
            "vin": self.vin,
            "reg": self.reg,
            "created": self.created.isoformat(),
            "file_path": str(self.file_path) if self.file_path else "",
            "file_size": self.file_size,
            "page_count": self.page_count,
            "metadata": dict(self.metadata),
            "imported": self.imported,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CertificateRecord:
        if not isinstance(data, dict):
            return cls()
        path_text = str(data.get("file_path", data.get("path", "")) or "")
        return cls(
            id=_safe_int(data.get("id")),
            filename=str(data.get("filename", "") or ""),
            template=str(data.get("template", "") or ""),
            category=str(data.get("category", "") or ""),
            vin=str(data.get("vin", "") or ""),
            reg=str(data.get("reg", "") or ""),
            created=_parse_datetime(data.get("created", data.get("created_at", data.get("timestamp")))),
            file_path=Path(path_text) if path_text else None,
            file_size=max(0, _safe_int(data.get("file_size"))),
            page_count=max(0, _safe_int(data.get("page_count", data.get("pages", 1)), 1)),
            metadata=dict(data.get("metadata", {}) or {}),
            imported=bool(data.get("imported", False)),
        )


@dataclass(slots=True)
class StatisticsSummary:
    """Sammanfattning för vald tidsperiod."""

    period: TimePeriod = TimePeriod.ALL
    total_certificates: int = 0
    total_today: int = 0
    total_this_week: int = 0
    total_this_month: int = 0
    total_pages: int = 0
    total_templates_available: int = 0
    avg_per_day: float = 0.0
    avg_per_week: float = 0.0
    avg_per_month: float = 0.0
    certificates_by_template: dict[str, int] = field(default_factory=dict)
    certificates_by_weekday: dict[str, int] = field(default_factory=dict)
    certificates_by_category: dict[str, int] = field(default_factory=dict)
    certificates_by_month: dict[str, int] = field(default_factory=dict)
    most_used_template: str = ""
    busiest_weekday: str = ""
    total_file_size_mb: float = 0.0
    days_active: int = 0
    trend_emoji: str = "→"
    trend_percentage: float = 0.0
    trend_count_current: int = 0
    trend_count_previous: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period.value,
            "total_certificates": self.total_certificates,
            "total_today": self.total_today,
            "total_this_week": self.total_this_week,
            "total_this_month": self.total_this_month,
            "total_pages": self.total_pages,
            "total_templates_available": self.total_templates_available,
            "avg_per_day": self.avg_per_day,
            "avg_per_week": self.avg_per_week,
            "avg_per_month": self.avg_per_month,
            "certificates_by_template": dict(self.certificates_by_template),
            "certificates_by_weekday": dict(self.certificates_by_weekday),
            "certificates_by_category": dict(self.certificates_by_category),
            "certificates_by_month": dict(self.certificates_by_month),
            "most_used_template": self.most_used_template,
            "busiest_weekday": self.busiest_weekday,
            "total_file_size_mb": self.total_file_size_mb,
            "days_active": self.days_active,
            "trend_emoji": self.trend_emoji,
            "trend_percentage": self.trend_percentage,
        }


@dataclass(slots=True)
class ReportConfig:
    title: str = "Statistikrapport"
    period: TimePeriod = TimePeriod.ALL
    format: ReportFormat = ReportFormat.TEXT
    include_summary: bool = True
    include_charts: bool = True
    include_details: bool = True
    max_details: int = 50
    chart_type: ChartType = ChartType.BAR

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReportConfig:
        try:
            report_format = ReportFormat(str(data.get("format", ReportFormat.TEXT.value)))
        except ValueError:
            report_format = ReportFormat.TEXT
        try:
            chart_type = ChartType(str(data.get("chart_type", ChartType.BAR.value)))
        except ValueError:
            chart_type = ChartType.BAR
        return cls(
            title=str(data.get("title", "Statistikrapport") or "Statistikrapport"),
            period=TimePeriod.from_value(data.get("period")),
            format=report_format,
            include_summary=bool(data.get("include_summary", True)),
            include_charts=bool(data.get("include_charts", True)),
            include_details=bool(data.get("include_details", True)),
            max_details=max(1, _safe_int(data.get("max_details"), 50)),
            chart_type=chart_type,
        )


@dataclass(slots=True)
class ImportResult:
    total_scanned: int = 0
    total_imported: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    total_duplicates: int = 0
    imported_records: list[CertificateRecord] = field(default_factory=list)
    counters_synced: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

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
    def success(self) -> bool:
        return self.total_errors == 0

    def to_summary_text(self) -> str:
        return (
            f"Totalt: {self.total_scanned}\n"
            f"Importerade: {self.total_imported}\n"
            f"Hoppade över: {self.total_skipped}\n"
            f"Fel: {self.total_errors}"
        )


WEEKDAY_NAMES: list[str] = [
    "Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag"
]
MONTH_NAMES: list[str] = [
    "Januari", "Februari", "Mars", "April", "Maj", "Juni", "Juli", "Augusti",
    "September", "Oktober", "November", "December",
]
TEMPLATE_ALIASES: dict[str, str] = {
    "35kw": "35kW",
    "35 kw": "35kW",
    "demontering": "Demontering",
    "passagerare": "Passagerare",
}


class StatisticsEngine:
    """Trådsäker fallback-motor för statistikposter."""

    def __init__(
        self,
        records: list[CertificateRecord] | None = None,
        storage_path: Path | str | None = None,
        **_kwargs: Any,
    ) -> None:
        self._lock = threading.RLock()
        self._records = list(records or [])
        self._storage_path = Path(storage_path) if storage_path else None
        if self._storage_path is not None and self._storage_path.is_file():
            self.load(self._storage_path)

    @property
    def records(self) -> list[CertificateRecord]:
        with self._lock:
            return list(self._records)

    def add_record(self, record: CertificateRecord) -> None:
        with self._lock:
            self._records.append(record)

    def add_certificate(self, **data: Any) -> CertificateRecord:
        record = CertificateRecord.from_dict(data)
        self.add_record(record)
        return record

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def filter_by_period(self, period: TimePeriod, **_kwargs: Any) -> list[CertificateRecord]:
        now = datetime.now()
        if period in {TimePeriod.ALL, TimePeriod.ALL_TIME}:
            return self.records
        if period is TimePeriod.TODAY:
            boundary = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period is TimePeriod.THIS_WEEK:
            boundary = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            boundary = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return [record for record in self.records if record.created >= boundary]

    def calculate_summary(self, records: list[CertificateRecord], period: TimePeriod) -> StatisticsSummary:
        by_template: dict[str, int] = {}
        by_weekday: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_month: dict[str, int] = {}
        total_pages = 0
        total_size = 0
        active_days: set[str] = set()
        for record in records:
            template = record.template or "Okänd"
            weekday = WEEKDAY_NAMES[record.created.weekday()]
            category = record.category or "Okategoriserad"
            month = record.created.strftime("%Y-%m")
            by_template[template] = by_template.get(template, 0) + 1
            by_weekday[weekday] = by_weekday.get(weekday, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1
            by_month[month] = by_month.get(month, 0) + 1
            total_pages += max(0, record.page_count)
            total_size += max(0, record.file_size)
            active_days.add(record.created.date().isoformat())
        now = datetime.now()
        today_count = sum(record.created.date() == now.date() for record in records)
        week_start = (now - timedelta(days=now.weekday())).date()
        week_count = sum(record.created.date() >= week_start for record in records)
        month_count = sum(record.created.year == now.year and record.created.month == now.month for record in records)
        days_active = len(active_days)
        return StatisticsSummary(
            period=period,
            total_certificates=len(records),
            total_today=today_count,
            total_this_week=week_count,
            total_this_month=month_count,
            total_pages=total_pages,
            avg_per_day=len(records) / max(1, days_active),
            avg_per_week=len(records) / max(1, days_active / 7),
            avg_per_month=len(records) / max(1, days_active / 30),
            certificates_by_template=by_template,
            certificates_by_weekday=by_weekday,
            certificates_by_category=by_category,
            certificates_by_month=by_month,
            most_used_template=max(by_template, key=by_template.get, default=""),
            busiest_weekday=max(by_weekday, key=by_weekday.get, default=""),
            total_file_size_mb=total_size / (1024 * 1024),
            days_active=days_active,
        )

    def get_summary(self, period: TimePeriod = TimePeriod.ALL, **kwargs: Any) -> StatisticsSummary:
        return self.calculate_summary(self.filter_by_period(period, **kwargs), period)

    def search(self, query: str, field_name: str = "all", limit: int = MAX_RECENT_DISPLAY) -> list[CertificateRecord]:
        needle = str(query).casefold().strip()
        if not needle:
            return self.records[:max(0, limit)]
        found: list[CertificateRecord] = []
        for record in self.records:
            values = {
                "filename": record.filename,
                "template": record.template,
                "category": record.category,
                "vin": record.vin,
                "reg": record.reg,
                "id": str(record.id),
            }
            haystack = values.get(field_name, " ".join(values.values()))
            if needle in haystack.casefold():
                found.append(record)
                if len(found) >= max(1, limit):
                    break
        return found

    def get_monthly_trend(self, months: int = 12) -> list[tuple[str, int]]:
        limit = max(1, min(120, int(months)))
        buckets: dict[str, int] = {}
        for record in self.records:
            key = record.created.strftime("%Y-%m")
            buckets[key] = buckets.get(key, 0) + 1
        return sorted(buckets.items())[-limit:]

    def save(self, path: Path | str | None = None) -> bool:
        target = Path(path) if path is not None else self._storage_path
        if target is None:
            return False
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = [record.to_dict() for record in self._records]
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(target)
            return True
        except OSError:
            _logger.exception("[STATISTICS] Kunde inte spara %s", target)
            return False
        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def load(self, path: Path | str | None = None) -> bool:
        source = Path(path) if path is not None else self._storage_path
        if source is None or not source.is_file():
            return False
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return False
            records = [CertificateRecord.from_dict(item) for item in raw if isinstance(item, dict)]
            with self._lock:
                self._records = records
            return True
        except (OSError, json.JSONDecodeError):
            _logger.exception("[STATISTICS] Kunde inte läsa %s", source)
            return False


class ASCIIChartGenerator:
    """Textdiagram för rapporter utan externa grafikberoenden."""

    @staticmethod
    def bar_chart(data: dict[str, int], title: str = "", width: int = CHART_WIDTH, **_kwargs: Any) -> str:
        if not data:
            return f"{title}\n(Ingen data)".strip()
        safe_width = max(12, min(120, int(width)))
        maximum = max(data.values(), default=1)
        lines = [title, "─" * safe_width] if title else ["─" * safe_width]
        for name, value in sorted(data.items(), key=lambda item: (-item[1], item[0].casefold())):
            bar_length = int(value * max(1, safe_width - 20) / maximum) if maximum else 0
            lines.append(f"{name[:16]:<16} {'█' * bar_length} {value}")
        return "\n".join(lines)

    @staticmethod
    def pie_chart(data: dict[str, int], title: str = "", **kwargs: Any) -> str:
        return ASCIIChartGenerator.bar_chart(data, title, **kwargs)


class CertificateImporter:
    """Importera befintliga PDF-filer från Out/<MallNamn>/ utan UI-beroende."""

    _NUMBER_PATTERN = re.compile(r"^(?P<number>\d+)\s+Intyg\s+", re.IGNORECASE)

    def __init__(self, log: logging.Logger | None = None, **_kwargs: Any) -> None:
        self._log = log or _logger

    def scan_directory(self, path: Path | str, recursive: bool = True, **_kwargs: Any) -> ImportResult:
        started = datetime.now()
        root = Path(path)
        result = ImportResult()
        if not root.is_dir():
            result.total_errors = 1
            result.errors.append(f"Sökväg saknas: {root}")
            return result
        files = sorted(root.rglob("*.pdf") if recursive else root.glob("*.pdf"))
        result.total_scanned = len(files)
        seen: set[Path] = set()
        for file_path in files:
            try:
                resolved = file_path.resolve()
                if resolved in seen:
                    result.total_duplicates += 1
                    result.total_skipped += 1
                    continue
                seen.add(resolved)
                result.imported_records.append(self._record_from_pdf(root, file_path))
                result.total_imported += 1
            except Exception as exc:
                result.total_skipped += 1
                result.total_errors += 1
                result.errors.append(f"{file_path.name}: {exc}")
                self._log.exception("[STATISTICS] PDF-import misslyckades: %s", file_path)
        result.duration_seconds = (datetime.now() - started).total_seconds()
        return result

    def import_from_output(self, path: Path | str) -> ImportResult:
        return self.scan_directory(path, recursive=True)

    def import_from_csv(self, path: Path | str) -> ImportResult:
        source = Path(path)
        result = ImportResult()
        if not source.is_file():
            result.total_errors = 1
            result.errors.append(f"CSV saknas: {source}")
            return result
        try:
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                reader = csv.DictReader(handle, delimiter=";" if sample.count(";") >= sample.count(",") else ",")
                for row in reader:
                    result.total_scanned += 1
                    result.imported_records.append(CertificateRecord.from_dict(dict(row or {})))
                    result.total_imported += 1
        except (OSError, csv.Error) as exc:
            result.total_errors += 1
            result.errors.append(str(exc))
        return result

    def _record_from_pdf(self, root: Path, file_path: Path) -> CertificateRecord:
        relative = file_path.relative_to(root)
        template = relative.parts[0] if len(relative.parts) > 1 else file_path.parent.name
        match = self._NUMBER_PATTERN.search(file_path.name)
        number = _safe_int(match.group("number")) if match else 0
        stat = file_path.stat()
        return CertificateRecord(
            id=number,
            filename=file_path.name,
            template=template,
            created=datetime.fromtimestamp(stat.st_mtime),
            file_path=file_path,
            file_size=stat.st_size,
            page_count=self._page_count(file_path),
            imported=True,
        )

    @staticmethod
    def _page_count(path: Path) -> int:
        for module_name in ("pypdf", "PyPDF2"):
            try:
                module = __import__(module_name, fromlist=["PdfReader"])
                return len(module.PdfReader(str(path)).pages)
            except Exception:
                continue
        return 1


class ReportGenerator:
    """Skriv statistikrapport atomiskt som text, JSON, CSV eller HTML."""

    def __init__(self, summary: StatisticsSummary, records: list[CertificateRecord], config: ReportConfig, **_kwargs: Any) -> None:
        self._summary = summary
        self._records = list(records)
        self._config = config

    def generate(self, path: Path | str) -> bool:
        destination = Path(path)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = self._content()
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(destination)
            return True
        except OSError:
            _logger.exception("[STATISTICS] Kunde inte skriva rapport %s", destination)
            return False
        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def _content(self) -> str:
        if self._config.format is ReportFormat.JSON:
            return json.dumps(self._summary.to_dict(), ensure_ascii=False, indent=2)
        if self._config.format is ReportFormat.CSV:
            rows = ["Mall;Antal"]
            rows.extend(f"{name};{count}" for name, count in sorted(self._summary.certificates_by_template.items()))
            return "\n".join(rows)
        if self._config.format is ReportFormat.HTML:
            rows = "".join(
                f"<tr><td>{html.escape(name)}</td><td>{count}</td></tr>"
                for name, count in sorted(self._summary.certificates_by_template.items())
            )
            return (
                "<!doctype html><html lang='sv'><head><meta charset='utf-8'>"
                f"<title>{html.escape(self._config.title)}</title></head><body>"
                f"<h1>{html.escape(self._config.title)}</h1><table>"
                "<tr><th>Mall</th><th>Antal</th></tr>"
                f"{rows}</table><p>Totalt: {self._summary.total_certificates}</p></body></html>"
            )
        lines = [self._config.title, "=" * len(self._config.title), ""]
        lines.append(f"Totalt intyg: {self._summary.total_certificates}")
        lines.append(f"Mallar tillgängliga: {self._summary.total_templates_available}")
        lines.append(f"Totalt sidor: {self._summary.total_pages}")
        if self._config.include_charts:
            lines.extend(["", ASCIIChartGenerator.bar_chart(self._summary.certificates_by_template, "Mallfördelning")])
        return "\n".join(lines).rstrip() + "\n"


@dataclass(slots=True)
class TemplateInfo:
    """Metadata för en giltig mall i Mallar/<MallNamn>/."""

    name: str = ""
    folder: Path = field(default_factory=Path)
    docx_path: Path = field(default_factory=Path)
    attachments: list[Path] = field(default_factory=list)
    config_path: Path | None = None
    config_data: dict[str, Any] = field(default_factory=dict)
    placeholder_count: int = 0
    is_valid: bool = False

    @property
    def attachment_count(self) -> int:
        return len(self.attachments)

    @property
    def has_config(self) -> bool:
        return self.config_path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "folder": str(self.folder),
            "docx_path": str(self.docx_path),
            "attachments": [str(path) for path in self.attachments],
            "config_path": str(self.config_path) if self.config_path else None,
            "config_data": dict(self.config_data),
            "placeholder_count": self.placeholder_count,
            "is_valid": self.is_valid,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TemplateInfo:
        return cls(
            name=str(data.get("name", "") or ""),
            folder=Path(str(data.get("folder", "") or "")),
            docx_path=Path(str(data.get("docx_path", "") or "")),
            attachments=[Path(str(path)) for path in data.get("attachments", []) or []],
            config_path=Path(str(data["config_path"])) if data.get("config_path") else None,
            config_data=dict(data.get("config_data", {}) or {}),
            placeholder_count=max(0, _safe_int(data.get("placeholder_count"))),
            is_valid=bool(data.get("is_valid", False)),
        )


class TemplateScanner:
    """Skanna direktunderkataloger enligt MALL-001→009 med DOCX-stämpelcache."""

    def __init__(self, log: logging.Logger | None = None) -> None:
        self._log = log or _logger
        self._lock = threading.RLock()
        self._cache: dict[tuple[Path, int, int, int], TemplateInfo] = {}

    def scan(self, templates_root: Path | str, use_cache: bool = True) -> list[TemplateInfo]:
        root = Path(templates_root)
        if not root.is_dir():
            self._log.warning("[STATISTICS] Mallmapp saknas: %s", root)
            return []
        try:
            folders = sorted(
                (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(_IGNORED_FOLDER_PREFIXES)),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            self._log.error("[STATISTICS] Kunde inte läsa %s: %s", root, exc)
            return []
        templates: list[TemplateInfo] = []
        for folder in folders:
            info = self._scan_folder(folder, use_cache)
            if info is not None:
                templates.append(info)
        return templates

    def _scan_folder(self, folder: Path, use_cache: bool) -> TemplateInfo | None:
        try:
            entries = tuple(folder.iterdir())
            folder_stamp = folder.stat().st_mtime_ns
        except OSError:
            return None
        docx_files = [
            path
            for path in entries
            if path.is_file()
            and path.suffix.casefold() == ".docx"
            and not path.name.startswith(_IGNORED_FILE_PREFIXES)
            and self._nonempty(path)
        ]
        if not docx_files:
            self._log.debug("[STATISTICS] Hoppade över %s: ingen DOCX", folder.name)
            return None
        docx_files.sort(key=lambda path: path.name.casefold())
        preferred = f"{folder.name}.docx".casefold()
        docx_path = next((path for path in docx_files if path.name.casefold() == preferred), docx_files[0])
        try:
            stat = docx_path.stat()
        except OSError:
            return None
        cache_key = (docx_path.resolve(), stat.st_mtime_ns, stat.st_size, folder_stamp)
        if use_cache:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                return TemplateInfo.from_dict(cached.to_dict())

        pdf_files = sorted(
            [path for path in entries if path.is_file() and path.suffix.casefold() == ".pdf" and not path.name.startswith(".")],
            key=lambda path: path.name.casefold(),
        )
        expected_json = f"{folder.name}.json".casefold()
        config_path = next((path for path in entries if path.is_file() and path.name.casefold() == expected_json), None)
        config_data = self._read_config(config_path) if config_path else {}
        info = TemplateInfo(
            name=_normalize_name(folder.name),
            folder=folder,
            docx_path=docx_path,
            attachments=pdf_files,
            config_path=config_path,
            config_data=config_data,
            placeholder_count=self._count_placeholders(docx_path),
            is_valid=True,
        )
        with self._lock:
            if len(self._cache) >= 512:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = info
        return TemplateInfo.from_dict(info.to_dict())

    @staticmethod
    def _nonempty(path: Path) -> bool:
        try:
            return path.stat().st_size > 0
        except OSError:
            return False

    def _read_config(self, path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            return dict(raw) if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            self._log.warning("[STATISTICS] Kunde inte läsa mallconfig: %s", path)
            return {}

    @staticmethod
    def _count_placeholders(path: Path) -> int:
        found: set[str] = set()
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if not name.startswith("word/") or not name.endswith(".xml"):
                        continue
                    raw = archive.read(name).decode("utf-8", errors="replace")
                    for paragraph in re.findall(r"<w:p(?:\s[^>]*)?>(.*?)</w:p>", raw, flags=re.DOTALL):
                        text = "".join(html.unescape(value) for value in re.findall(r"<w:t[^>]*>(.*?)</w:t>", paragraph, flags=re.DOTALL))
                        found.update(match.group(1).strip() for match in _TEMPLATE_PATTERN.finditer(text))
        except (OSError, zipfile.BadZipFile):
            return 0
        return len(found)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def get_template_names(self, templates_root: Path | str) -> list[str]:
        return [info.name for info in self.scan(templates_root)]

    def get_template(self, templates_root: Path | str, name: str) -> TemplateInfo | None:
        target = _normalize_name(name).casefold()
        return next((info for info in self.scan(templates_root) if info.name.casefold() == target), None)

    def get_statistics(self, templates_root: Path | str) -> dict[str, Any]:
        templates = self.scan(templates_root)
        return {
            "total_templates": len(templates),
            "total_attachments": sum(info.attachment_count for info in templates),
            "total_placeholders": sum(info.placeholder_count for info in templates),
            "templates_with_config": sum(info.has_config for info in templates),
            "names": [info.name for info in templates],
        }


_SCANNER_INSTANCE: TemplateScanner | None = None


def get_scanner() -> TemplateScanner:
    global _SCANNER_INSTANCE
    with _scanner_lock:
        if _SCANNER_INSTANCE is None:
            _SCANNER_INSTANCE = TemplateScanner()
        return _SCANNER_INSTANCE


def scan_templates(templates_root: Path | str, use_cache: bool = True) -> list[TemplateInfo]:
    return get_scanner().scan(templates_root, use_cache=use_cache)


# Importera verklig motor först efter att fallback-API och TemplateScanner finns.
try:
    from .engine import (
        ASCIIChartGenerator,
        CertificateImporter,
        CertificateRecord,
        ChartType,
        ImportResult,
        MONTH_NAMES,
        ReportConfig,
        ReportFormat,
        ReportGenerator,
        StatisticsEngine,
        StatisticsSummary,
        TEMPLATE_ALIASES,
        TimePeriod,
        WEEKDAY_NAMES,
        scan_template_names as _engine_scan_template_names,
    )

    ENGINE_AVAILABLE = True
except Exception as exc:
    with _load_lock:
        LOAD_ERRORS["engine"] = f"{type(exc).__name__}: {exc}"
    _logger.exception("[STATISTICS] engine.py kunde inte laddas; fallback används")
    _engine_scan_template_names = None


def scan_template_names(templates_root: Path | str, **kwargs: Any) -> list[str]:
    """Hämta mallnamn via statistics.engine eller den cachade TemplateScanner."""
    if _engine_scan_template_names is not None:
        try:
            return list(_engine_scan_template_names(templates_root, **kwargs))
        except TypeError:
            return list(_engine_scan_template_names(templates_root))
        except Exception:
            _logger.exception("[STATISTICS] engine.scan_template_names misslyckades")
    return get_scanner().get_template_names(templates_root)


try:
    from .plugin import Plugin

    PLUGIN_AVAILABLE = True
except Exception as exc:
    with _load_lock:
        LOAD_ERRORS["plugin"] = f"{type(exc).__name__}: {exc}"
    _logger.exception("[STATISTICS] plugin.py kunde inte laddas; fallback används")

    class Plugin:
        """Fail-safe plugin som låter resten av appen fortsätta vid UI-importfel."""

        name: str = __plugin_name__
        version: str = __version__
        description: str = f"{__description__} (degraded mode)"
        priority: int = PLUGIN_PRIORITY

        def __init__(self) -> None:
            self._context: Any | None = None
            self._unsubscribers: list[Any] = []

        def on_load(self, context: Any) -> None:
            self._context = context
            emitter = getattr(context, "emit_event", None)
            if callable(emitter):
                try:
                    emitter(
                        "error.occurred",
                        {
                            "source": "statistics",
                            "message": LOAD_ERRORS.get("plugin", "Statistics UI kunde inte laddas"),
                            "level": "error",
                        },
                    )
                except Exception:
                    _logger.exception("[STATISTICS] Kunde inte emittera fallback-fel")

        def setup(self, context: Any) -> None:
            self.on_load(context)

        def on_unload(self) -> None:
            for unsubscribe in self._unsubscribers:
                if callable(unsubscribe):
                    try:
                        unsubscribe()
                    except Exception:
                        _logger.exception("[STATISTICS] Kunde inte avregistrera fallback-event")
            self._unsubscribers.clear()
            self._context = None

        def on_close(self) -> None:
            self.on_unload()

        def get_tab(self) -> Any | None:
            return None

        def get_statistics(self) -> dict[str, Any]:
            return {
                "available": False,
                "engine_available": ENGINE_AVAILABLE,
                "plugin_available": PLUGIN_AVAILABLE,
                "load_errors": get_load_errors(),
            }


def get_copyright() -> str:
    return COPYRIGHT


def is_fully_loaded() -> bool:
    with _load_lock:
        return ENGINE_AVAILABLE and PLUGIN_AVAILABLE


def is_engine_available() -> bool:
    with _load_lock:
        return ENGINE_AVAILABLE


def is_plugin_available() -> bool:
    with _load_lock:
        return PLUGIN_AVAILABLE


def get_load_errors() -> dict[str, str]:
    with _load_lock:
        return dict(LOAD_ERRORS)


def get_plugin_class() -> type[Any]:
    return Plugin


def get_plugin_info() -> dict[str, Any]:
    with _load_lock:
        errors = dict(LOAD_ERRORS)
        engine_available = ENGINE_AVAILABLE
        plugin_available = PLUGIN_AVAILABLE
    return {
        "id": "statistics",
        "name": __plugin_name__,
        "version": __version__,
        "author": __author__,
        "description": __description__,
        "copyright": COPYRIGHT,
        "plugin_class": Plugin,
        "priority": getattr(Plugin, "priority", PLUGIN_PRIORITY),
        "has_tab": True,
        "engine_available": engine_available,
        "plugin_available": plugin_available,
        "fully_loaded": engine_available and plugin_available,
        "load_errors": errors,
        "platforms": ["windows", "macos", "linux"],
        "features": ["statistics", "reporting", "import", "template_scanning"],
        "events": {
            "listens_to": [
                "statistics.certificate_generated",
                "certificate.generated",
                "certificates.imported",
                "templates.scanned",
                "refresh_templates",
                "app.shutdown",
            ],
            "emits": ["statistics.response", "statistics.updated", "statistics.imported"],
        },
    }


__all__: list[str] = [
    "Plugin",
    "COPYRIGHT",
    "FOLDER_CONFIG",
    "CHART_WIDTH",
    "MAX_RECENT_DISPLAY",
    "TemplateInfo",
    "TemplateScanner",
    "get_scanner",
    "scan_templates",
    "scan_template_names",
    "TimePeriod",
    "ReportFormat",
    "ChartType",
    "CertificateRecord",
    "StatisticsSummary",
    "ReportConfig",
    "ImportResult",
    "StatisticsEngine",
    "ASCIIChartGenerator",
    "CertificateImporter",
    "ReportGenerator",
    "TEMPLATE_ALIASES",
    "WEEKDAY_NAMES",
    "MONTH_NAMES",
    "get_copyright",
    "is_fully_loaded",
    "is_engine_available",
    "is_plugin_available",
    "get_load_errors",
    "get_plugin_class",
    "get_plugin_info",
]
