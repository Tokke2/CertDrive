from __future__ import annotations

"""
Fil:    plugins/statistics/plugin.py
Sökväg: Intygsgenerator/plugins/statistics/plugin.py

Intygsgenerator — PySide6-statistikflik för översikt, sökning, import och
rapportering. Tunga PDF-skanningar körs via QRunnable och UI:t uppdateras bara
från huvudtråden.
"""

import json
import logging
import os
import subprocess
import sys
import time
import tempfile
import threading
import webbrowser
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import COPYRIGHT

try:
    from .engine import (
        ASCIIChartGenerator,
        CHART_WIDTH as _CHART_WIDTH,
        CertificateImporter,
        CertificateRecord,
        ImportResult,
        MAX_RECENT_DISPLAY as _MAX_RECENT,
        ReportConfig,
        ReportFormat,
        ReportGenerator,
        StatisticsEngine,
        StatisticsSummary,
        TimePeriod,
        scan_template_names,
    )

    ENGINE_AVAILABLE: bool = True
except Exception as exc:
    ENGINE_AVAILABLE = False
    _ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
    from . import (
        ASCIIChartGenerator,
        CertificateImporter,
        CertificateRecord,
        ImportResult,
        ReportConfig,
        ReportFormat,
        ReportGenerator,
        StatisticsEngine,
        StatisticsSummary,
        TimePeriod,
        scan_template_names,
    )

    _CHART_WIDTH = 60
    _MAX_RECENT = 500
else:
    _ENGINE_ERROR = ""

__version__: str = "5.1.0"
__all__: list[str] = ["Plugin"]

PLUGIN_NAME: str = "Statistik"
PLUGIN_PRIORITY: int = 60
DATA_FILE: str = "statistics.json"
_SEARCH_DEBOUNCE_MS: int = 200
_INITIAL_SCAN_DELAY_MS: int = 500

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger: logging.Logger = logging.getLogger(__name__)


class _WorkerSignals(QObject):
    """Signals från statistikworkers till UI-tråden."""

    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str)




def _sp(base: int) -> int:
    """5.1.0: skala pixelmått efter fontmetrik (DPI/teckenstorlek, §17/§30)."""
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            height = app.fontMetrics().height()
            if height > 0:
                return max(base, int(round(base * height / 16.0)))
    except Exception:
        pass
    return base

class _ImportWorker(QRunnable):
    """Importera PDF-filer eller CSV utanför UI-tråden."""

    def __init__(self, source: Path, mode: str) -> None:
        super().__init__()
        self._source = source
        self._mode = mode
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            importer = CertificateImporter()
            if self._mode == "csv":
                self.signals.progress.emit("Importerar CSV…")
                result = importer.import_from_csv(self._source)
            else:
                self.signals.progress.emit("Skannar PDF-intyg…")
                result = importer.scan_directory(self._source, recursive=True)
            self.signals.finished.emit(result)
        except Exception as exc:
            _logger.exception("[STATISTICS] Importworker misslyckades")
            self.signals.error.emit(str(exc))


class _StatisticsStatusBar(QWidget):
    """Lätt statusrad med tidsbegränsade meddelanden."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._message = QLabel("Redo", self)
        self._stats = QLabel(self)
        self._stats.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._message, stretch=1)
        layout.addWidget(self._stats)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._message.setText("Redo"))

    def set_message(self, text: str, duration_ms: int = 0) -> None:
        self._timer.stop()
        self._message.setText(text)
        if duration_ms > 0:
            self._timer.start(duration_ms)

    def set_success(self, text: str, duration_ms: int = 3000) -> None:
        self.set_message(f"✅ {text}", duration_ms)

    def set_error(self, text: str, duration_ms: int = 5000) -> None:
        self.set_message(f"❌ {text}", duration_ms)

    def set_stats(self, text: str) -> None:
        self._stats.setText(text)


class Plugin:
    """IPlugin-implementation för statistik, import och rapporter."""

    name: str = PLUGIN_NAME
    version: str = __version__
    description: str = "Statistik, rapporter och import av intyg"
    priority: int = PLUGIN_PRIORITY

    def __init__(self) -> None:
        self._context: Any | None = None
        self._event_bus: Any | None = None
        self._log = _logger
        self._pool = QThreadPool.globalInstance()
        self._lock = threading.RLock()
        self._unsubscribers: list[Callable[[], None]] = []
        self._loaded = False
        self._initial_import_started = False
        self._import_active = False

        self._records: list[CertificateRecord] = []
        self._engine = StatisticsEngine()
        self._template_names: list[str] = []
        self._output_path: Path | None = None
        self._templates_path: Path | None = None
        self._data_file: Path | None = None

        self._tab: QWidget | None = None
        self._status_bar: _StatisticsStatusBar | None = None
        self._period_combo: QComboBox | None = None
        self._summary_text: QPlainTextEdit | None = None
        self._charts_text: QPlainTextEdit | None = None
        self._records_tree: QTreeWidget | None = None
        self._records_count: QLabel | None = None
        self._search_edit: QLineEdit | None = None
        self._search_timer: QTimer | None = None
        self._import_text: QPlainTextEdit | None = None
        self._quick_stats: QLabel | None = None
        self._report_title: QLineEdit | None = None
        self._report_period: QComboBox | None = None
        self._report_format: QComboBox | None = None
        self._include_summary: QCheckBox | None = None
        self._include_charts: QCheckBox | None = None
        self._include_details: QCheckBox | None = None
        self._report_preview: QPlainTextEdit | None = None

    # ── Livscykel ─────────────────────────────────────────────────────────

    def on_load(self, context: Any) -> None:
        if self._loaded:
            return
        self._context = context
        self._event_bus = getattr(context, "event_bus", None)
        get_logger = getattr(context, "get_logger", None)
        if callable(get_logger):
            try:
                self._log = get_logger("Statistics")
            except Exception:
                _logger.exception("[STATISTICS] Kunde inte hämta applogger")
        self._setup_paths(context)
        self._load_data()
        self._engine = StatisticsEngine(self._records)
        self._refresh_template_names()
        self._subscribe_events()
        self._loaded = True
        self._log.info(
            "[STATISTICS] Laddad: %d poster, %d mallar, engine=%s",
            len(self._records),
            len(self._template_names),
            "OK" if ENGINE_AVAILABLE else _ENGINE_ERROR,
        )

    def setup(self, context: Any) -> None:
        self.on_load(context)

    def on_unload(self) -> None:
        self._loaded = False
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                self._log.exception("[STATISTICS] Kunde inte avregistrera event")
        self._unsubscribers.clear()
        self._save_data()
        self._context = None
        self._event_bus = None

    def on_close(self) -> None:
        self.on_unload()

    def get_tab(self) -> QWidget | None:
        if self._tab is not None:
            return self._tab
        try:
            self._tab = self._build_tab()
            self._refresh_ui()
            QTimer.singleShot(_INITIAL_SCAN_DELAY_MS, self._start_initial_import)
            return self._tab
        except Exception as exc:
            self._log.exception("[STATISTICS] Kunde inte bygga statistikflik")
            return self._build_error_tab(str(exc))

    # ── Sökvägar, lagring och EventBus ────────────────────────────────────

    def _setup_paths(self, context: Any) -> None:
        def get_path(key: str, fallback: Path) -> Path:
            getter = getattr(context, "get_path", None)
            if callable(getter):
                try:
                    value = getter(key)
                    if value:
                        return Path(value)
                except Exception:
                    self._log.exception("[STATISTICS] Kunde inte hämta path %s", key)
            return fallback

        self._output_path = get_path("output", Path("Out"))
        self._templates_path = get_path("templates", Path("Mallar"))
        config_path = get_path("config", Path("js"))
        for path in (self._output_path, config_path):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._log.error("[STATISTICS] Kunde inte skapa %s: %s", path, exc)
        self._data_file = config_path / DATA_FILE

    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        subscriptions: tuple[tuple[str, Callable[[Any], None]], ...] = (
            ("certificate.generated", self._on_certificate_generated),
            ("statistics.request", self._on_statistics_request),
            ("refresh_ui", self._on_refresh_ui),
            ("refresh_templates", self._on_refresh_templates),
            ("templates.scanned", self._on_templates_scanned),
            ("app.shutdown", self._on_app_shutdown),
        )
        for event_name, callback in subscriptions:
            try:
                unsubscribe = self._event_bus.subscribe(event_name, callback)
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
            except Exception:
                self._log.exception("[STATISTICS] Kunde inte prenumerera på %s", event_name)

    def _emit_event(self, event_name: str, data: dict[str, Any]) -> None:
        context = self._context
        emitter = getattr(context, "emit_event", None) if context is not None else None
        if callable(emitter):
            try:
                emitter(event_name, data)
                return
            except Exception:
                self._log.exception("[STATISTICS] Event %s misslyckades", event_name)
        bus = self._event_bus
        emit = getattr(bus, "emit", None) if bus is not None else None
        if callable(emit):
            try:
                emit(event_name, data)
            except Exception:
                self._log.exception("[STATISTICS] EventBus %s misslyckades", event_name)

    def _load_data(self) -> None:
        if self._data_file is None or not self._data_file.is_file():
            self._records = []
            return
        try:
            raw = json.loads(self._data_file.read_text(encoding="utf-8"))
            values = raw.get("records", []) if isinstance(raw, dict) else raw
            self._records = (
                [
                    CertificateRecord.from_dict(item)
                    for item in values
                    if isinstance(item, dict)
                ]
                if isinstance(values, list)
                else []
            )
        except (OSError, json.JSONDecodeError):
            self._log.exception("[STATISTICS] Kunde inte läsa %s", self._data_file)
            self._records = []

    def _save_data(self) -> None:
        if self._data_file is None:
            return
        temporary = self._data_file.with_suffix(f"{self._data_file.suffix}.tmp")
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "records": [record.to_dict() for record in self._records],
                "saved": datetime.now().isoformat(timespec="seconds"),
                "version": self.version,
                "copyright": COPYRIGHT,
            }
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self._data_file)
        except OSError:
            self._log.exception("[STATISTICS] Kunde inte spara %s", self._data_file)
        finally:
            if temporary.exists():
                with suppress(OSError):
                    temporary.unlink()

    def _refresh_template_names(self) -> None:
        if self._templates_path is None:
            self._template_names = []
            return
        try:
            self._template_names = sorted(
                dict.fromkeys(scan_template_names(self._templates_path)),
                key=str.casefold,
            )
        except Exception:
            self._log.exception("[STATISTICS] Mallskanning misslyckades")
            self._template_names = []

    # ── Händelser och poster ──────────────────────────────────────────────

    def _on_certificate_generated(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        try:
            path_text = str(data.get("path", "") or "")
            file_path = Path(path_text) if path_text else None
            values = data.get("values", {}) if isinstance(data.get("values"), dict) else {}
            created = datetime.now()
            if data.get("timestamp"):
                with suppress(TypeError, ValueError):
                    created = datetime.fromisoformat(str(data["timestamp"]))
            record = CertificateRecord(
                id=int(data.get("number", 0) or 0),
                filename=str(
                    data.get("filename", file_path.name if file_path else "") or ""
                ),
                template=str(data.get("template", "") or ""),
                category=str(data.get("template", "") or ""),
                vin=str(values.get("VIN", values.get("vin", "")) or ""),
                reg=str(values.get("Reg", values.get("REG", values.get("reg", ""))) or ""),
                created=created,
                file_path=file_path,
                file_size=file_path.stat().st_size if file_path and file_path.is_file() else 0,
                page_count=int(data.get("pages", 1) or 1),
                metadata={"duration": data.get("duration", 0.0)},
            )
            self._add_record(record)
        except Exception:
            self._log.exception("[STATISTICS] Kunde inte registrera genererat intyg")

    def _on_statistics_request(self, _data: Any = None) -> None:
        self._emit_event("statistics.response", {"data": self.get_statistics()})

    def _on_refresh_ui(self, _data: Any = None) -> None:
        QTimer.singleShot(0, self._refresh_ui)

    def _on_refresh_templates(self, _data: Any = None) -> None:
        self._refresh_template_names()
        QTimer.singleShot(0, self._refresh_ui)

    def _on_templates_scanned(self, data: Any = None) -> None:
        if isinstance(data, dict) and isinstance(data.get("names"), list):
            self._template_names = sorted(
                dict.fromkeys(str(name) for name in data["names"]), key=str.casefold
            )
        else:
            self._refresh_template_names()
        QTimer.singleShot(0, self._refresh_ui)

    def _on_app_shutdown(self, _data: Any = None) -> None:
        self._save_data()

    def _add_record(self, record: CertificateRecord) -> bool:
        with self._lock:
            identity = str(record.file_path or record.filename)
            existing = {str(item.file_path or item.filename) for item in self._records}
            if identity and identity in existing:
                return False
            self._records.append(record)
            self._engine = StatisticsEngine(self._records)
            self._save_data()
        self._sync_counter(record.template, record.id)
        self._emit_event(
            "statistics.updated",
            {
                "source": "certificate",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            },
        )
        QTimer.singleShot(0, self._refresh_ui)
        return True

    def _sync_counter(self, template_name: str, number: int) -> bool:
        """Kompatibilitet för importerade intyg när Certificates-pluginet finns."""
        if not template_name or number <= 0 or self._context is None:
            return False
        manager = getattr(self._context, "_plugin_manager", None) or getattr(
            self._context, "plugin_manager", None
        )
        getter = getattr(manager, "get_plugin", None)
        if not callable(getter):
            return False
        try:
            certificates = getter("Intyg") or getter("Certificates")
            counter = getattr(certificates, "_counter_manager", None) if certificates is not None else None
            setter = getattr(counter, "set", None)
            current = getattr(counter, "get_current", None)
            if callable(setter) and callable(current) and number > int(current(template_name)):
                setter(template_name, number)
                return True
        except Exception:
            self._log.exception("[STATISTICS] Kunde inte synka räknare")
        return False

    # ── Bakgrundsimport ───────────────────────────────────────────────────

    def _start_initial_import(self) -> None:
        if self._initial_import_started or self._output_path is None:
            return
        self._initial_import_started = True
        self._start_import_worker(self._output_path, "folder", quiet=True)

    @Slot()
    def _on_scan_output(self) -> None:
        if self._output_path is not None:
            self._start_import_worker(self._output_path, "folder", quiet=False)

    @Slot()
    def _on_import_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self._tab, "Välj mapp att importera")
        if selected:
            self._start_import_worker(Path(selected), "folder", quiet=False)

    @Slot()
    def _on_import_csv(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self._tab, "Välj CSV-fil", "", "CSV-filer (*.csv);;Alla filer (*.*)"
        )
        if selected:
            self._start_import_worker(Path(selected), "csv", quiet=False)

    def _start_import_worker(self, source: Path, mode: str, *, quiet: bool) -> None:
        if self._import_active:
            return
        self._import_active = True
        self._import_started_at = time.perf_counter()  # 5.1.0
        self._log.info(
            "[STATISTICS] Import startad: %s (%s)", source, mode,
            extra={"event": "stat.import_started",
                   "ctx": {"source": str(source), "mode": mode}},
        )
        if self._status_bar is not None:
            self._status_bar.set_message("Importerar…")
        if self._import_text is not None and not quiet:
            self._import_text.setPlainText(f"⏳ Importerar {source}…")
        worker = _ImportWorker(source, mode)
        worker.signals.finished.connect(
            lambda result, silent=quiet: self._on_import_finished(result, silent),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)
        worker.signals.progress.connect(self._on_worker_progress, Qt.ConnectionType.QueuedConnection)
        self._pool.start(worker)

    def _on_import_finished(self, result: Any, quiet: bool) -> None:
        self._import_active = False
        if not isinstance(result, ImportResult):
            return
        # 5.1.0: strukturerad stat.*-logg med ms.
        self._log.info(
            "[STATISTICS] Import klar: %d poster", len(result.imported_records),
            extra={"event": "stat.import_done",
                   "ctx": {"records": len(result.imported_records),
                           "ms": round((time.perf_counter() - getattr(self, "_import_started_at", time.perf_counter())) * 1000, 1)}},
        )
        added, synced = self._merge_import_result(result)
        self._refresh_ui()
        summary = result.to_summary_text() + f"\n\n✅ Nya poster: {added}\n🔢 Synkade räknare: {synced}"
        if self._import_text is not None:
            self._import_text.setPlainText(summary)
        if self._status_bar is not None:
            self._status_bar.set_success(f"{added} nya intyg", 4000)
        self._emit_event(
            "statistics.imported",
            {"count": added, "counters": dict(result.counters_synced)},
        )
        if not quiet:
            QMessageBox.information(self._tab, "Import klar", summary)

    def _merge_import_result(self, result: ImportResult) -> tuple[int, int]:
        added = 0
        synced = 0
        with self._lock:
            existing = {str(record.file_path or record.filename) for record in self._records}
            for record in result.imported_records:
                identity = str(record.file_path or record.filename)
                if identity and identity not in existing:
                    self._records.append(record)
                    existing.add(identity)
                    added += 1
            self._engine = StatisticsEngine(self._records)
            self._save_data()
        for template, number in result.counters_synced.items():
            synced += int(self._sync_counter(template, number))
        return added, synced

    @Slot(str)
    def _on_worker_error(self, error: str) -> None:
        self._import_active = False
        self._log.error("[STATISTICS] Worker-fel: %s", error)
        if self._status_bar is not None:
            self._status_bar.set_error(error)
        if self._import_text is not None:
            self._import_text.setPlainText(f"❌ Fel:\n{error}")

    @Slot(str)
    def _on_worker_progress(self, message: str) -> None:
        if self._status_bar is not None:
            self._status_bar.set_message(message)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_tab(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._build_toolbar())
        tabs = QTabWidget(root)
        tabs.addTab(self._build_overview_tab(), "📊 Översikt")
        tabs.addTab(self._build_charts_tab(), "📈 Diagram")
        tabs.addTab(self._build_records_tab(), "📋 Intyg")
        tabs.addTab(self._build_import_tab(), "📥 Import")
        tabs.addTab(self._build_reports_tab(), "📄 Rapporter")
        layout.addWidget(tabs, stretch=1)
        self._status_bar = _StatisticsStatusBar(root)
        layout.addWidget(self._status_bar)
        self._search_timer = QTimer(root)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._perform_search)
        return root

    def _build_error_tab(self, error: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label = QLabel(f"⚠️ Statistikmodulen kunde inte laddas\n\n{error}", widget)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return widget

    def _build_toolbar(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        for label, callback in (
            ("🔄 Uppdatera", self._refresh_ui),
            ("📥 Skanna output", self._on_scan_output),
            ("📄 Snabbrapport", self._on_quick_report),
        ):
            button = QPushButton(label, widget)
            button.setMinimumHeight(_sp(34))
            font = button.font()
            font.setBold(True)
            button.setFont(font)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addWidget(QLabel("Period:", widget))
        self._period_combo = self._period_selector(widget)
        self._period_combo.currentIndexChanged.connect(lambda _index: self._refresh_ui())
        layout.addWidget(self._period_combo)
        layout.addStretch(1)
        self._quick_stats = QLabel("📊 Laddar…", widget)
        layout.addWidget(self._quick_stats)
        return widget

    @staticmethod
    def _period_selector(parent: QWidget) -> QComboBox:
        combo = QComboBox(parent)
        for period in TimePeriod.get_display_periods():
            combo.addItem(period.value, period)
        return combo

    def _build_overview_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._summary_text = QPlainTextEdit(widget)
        self._summary_text.setReadOnly(True)
        self._summary_text.setFont(self._mono_font(10))
        layout.addWidget(self._summary_text)
        return widget

    def _build_charts_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self._charts_text = QPlainTextEdit(widget)
        self._charts_text.setReadOnly(True)
        self._charts_text.setFont(self._mono_font(9))
        layout.addWidget(self._charts_text)
        return widget

    def _build_records_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("🔍 Sök:", widget))
        self._search_edit = QLineEdit(widget)
        self._search_edit.setPlaceholderText("Mall, VIN, REG, filnamn eller nummer…")
        self._search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self._search_edit, stretch=1)
        clear = QPushButton("Rensa", widget)
        clear.clicked.connect(self._clear_search)
        search_row.addWidget(clear)
        self._records_count = QLabel(widget)
        search_row.addWidget(self._records_count)
        layout.addLayout(search_row)
        self._records_tree = QTreeWidget(widget)
        self._records_tree.setHeaderLabels(
            ["#", "Datum", "Mall", "Kategori", "VIN", "REG", "Filnamn"]
        )
        header = self._records_tree.header()
        for index, width in enumerate((60, 140, 130, 120, 170, 95, 280)):
            header.resizeSection(index, width)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self._records_tree.setAlternatingRowColors(True)
        self._records_tree.itemDoubleClicked.connect(self._open_record)
        layout.addWidget(self._records_tree)
        return widget

    def _build_import_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        group = QGroupBox("📥 Import av intyg", widget)
        group_layout = QVBoxLayout(group)
        information = QLabel(
            "Importera PDF-intyg från en mapp eller statistikposter från CSV. "
            "Räknare synkas när Certificates-pluginet är tillgängligt.",
            group,
        )
        information.setWordWrap(True)
        group_layout.addWidget(information)
        row = QHBoxLayout()
        for label, callback in (
            ("📂 Från mapp…", self._on_import_folder),
            ("📄 Från CSV…", self._on_import_csv),
            ("🔄 Skanna output", self._on_scan_output),
        ):
            button = QPushButton(label, group)
            button.clicked.connect(callback)
            row.addWidget(button)
        row.addStretch(1)
        group_layout.addLayout(row)
        layout.addWidget(group)
        result_group = QGroupBox("Resultat", widget)
        result_layout = QVBoxLayout(result_group)
        self._import_text = QPlainTextEdit(result_group)
        self._import_text.setReadOnly(True)
        self._import_text.setFont(self._mono_font(9))
        result_layout.addWidget(self._import_text)
        layout.addWidget(result_group, stretch=1)
        return widget

    def _build_reports_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget(scroll)
        layout = QVBoxLayout(inner)
        settings = QGroupBox("⚙️ Rapportinställningar", inner)
        form = QFormLayout(settings)
        self._report_title = QLineEdit("Intygsgenerator — Statistikrapport", settings)
        form.addRow("Titel:", self._report_title)
        self._report_period = self._period_selector(settings)
        form.addRow("Period:", self._report_period)
        self._report_format = QComboBox(settings)
        for report_format in ReportFormat:
            self._report_format.addItem(report_format.display_name, report_format)
        self._set_combo_data(self._report_format, ReportFormat.HTML)
        form.addRow("Format:", self._report_format)
        checks = QWidget(settings)
        checks_layout = QHBoxLayout(checks)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        self._include_summary = QCheckBox("Sammanfattning", checks)
        self._include_charts = QCheckBox("Diagram", checks)
        self._include_details = QCheckBox("Detaljer", checks)
        for checkbox in (self._include_summary, self._include_charts, self._include_details):
            checkbox.setChecked(True)
            checks_layout.addWidget(checkbox)
        checks_layout.addStretch(1)
        form.addRow("Inkludera:", checks)
        layout.addWidget(settings)
        buttons = QHBoxLayout()
        for label, callback in (
            ("📄 Generera rapport", self._on_generate_report),
            ("👁️ Förhandsgranska", self._on_preview_report),
            ("🖨️ Snabbrapport", self._on_quick_report),
        ):
            button = QPushButton(label, inner)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        preview_group = QGroupBox("Förhandsvisning", inner)
        preview_layout = QVBoxLayout(preview_group)
        self._report_preview = QPlainTextEdit(preview_group)
        self._report_preview.setReadOnly(True)
        self._report_preview.setFont(self._mono_font(9))
        preview_layout.addWidget(self._report_preview)
        layout.addWidget(preview_group, stretch=1)
        scroll.setWidget(inner)
        return scroll

    @staticmethod
    def _mono_font(point_size: int) -> QFont:
        font = QFont()
        font.setFamily(
            "Consolas" if sys.platform == "win32"
            else "Menlo" if sys.platform == "darwin"
            else "Monospace"
        )
        font.setPointSize(point_size)
        return font

    # ── UI-refresh, sökning och rapporter ─────────────────────────────────

    def _selected_period(self) -> TimePeriod:
        data = self._period_combo.currentData() if self._period_combo is not None else None
        return data if isinstance(data, TimePeriod) else TimePeriod.ALL_TIME

    def _refresh_ui(self) -> None:
        if self._tab is None:
            return
        try:
            period = self._selected_period()
            filtered = self._engine.filter_by_period(period)
            summary = self._engine.calculate_summary(filtered, period)
            summary.total_templates_available = len(self._template_names)
            self._update_overview(summary, period)
            self._update_charts(summary)
            self._update_records_tree(filtered)
            if self._quick_stats is not None:
                self._quick_stats.setText(
                    f"📊 {summary.total_certificates:,} intyg │ "
                    f"{len(self._template_names)} mallar │ "
                    f"{summary.avg_per_day:.1f}/dag │ "
                    f"{summary.trend_emoji} {summary.trend_percentage:+.1f}%"
                )
            if self._status_bar is not None:
                self._status_bar.set_stats(f"{len(filtered)} poster")
        except Exception:
            self._log.exception("[STATISTICS] UI-refresh misslyckades")

    def _update_overview(self, summary: StatisticsSummary, period: TimePeriod) -> None:
        if self._summary_text is None:
            return
        text = (
            f"STATISTIK — {period.value}\n"
            f"{'═' * 64}\n\n"
            f"  Totalt:       {summary.total_certificates:>10,}\n"
            f"  Idag:         {summary.total_today:>10,}\n"
            f"  Denna vecka:  {summary.total_this_week:>10,}\n"
            f"  Denna månad:  {summary.total_this_month:>10,}\n"
            f"  Sidor:        {summary.total_pages:>10,}\n"
            f"  Mallar:       {len(self._template_names):>10}\n\n"
        )
        if summary.certificates_by_template:
            text += ASCIIChartGenerator.bar_chart(
                summary.certificates_by_template, "PER MALL", width=_CHART_WIDTH
            )
        self._summary_text.setPlainText(text)

    def _update_charts(self, summary: StatisticsSummary) -> None:
        if self._charts_text is None:
            return
        charts = [
            ASCIIChartGenerator.bar_chart(
                summary.certificates_by_template, "📊 PER MALL", width=_CHART_WIDTH
            )
        ]
        monthly = self._engine.get_monthly_trend(12)
        if monthly:
            charts.append(
                ASCIIChartGenerator.bar_chart(dict(monthly), "📈 TREND (12 MÅN)", width=40)
            )
        if summary.certificates_by_weekday:
            charts.append(
                ASCIIChartGenerator.bar_chart(
                    summary.certificates_by_weekday, "📅 PER VECKODAG", width=_CHART_WIDTH
                )
            )
        self._charts_text.setPlainText("\n\n".join(charts))

    def _update_records_tree(self, records: list[CertificateRecord]) -> None:
        if self._records_tree is None:
            return
        self._records_tree.setUpdatesEnabled(False)
        try:
            self._records_tree.clear()
            shown = min(len(records), _MAX_RECENT)
            for record in sorted(records, key=lambda item: item.created, reverse=True)[:shown]:
                item = QTreeWidgetItem(
                    [
                        str(record.id),
                        record.created.strftime("%Y-%m-%d %H:%M"),
                        record.template,
                        record.category,
                        record.vin,
                        record.reg,
                        record.filename,
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, str(record.file_path or ""))
                self._records_tree.addTopLevelItem(item)
            if self._records_count is not None:
                self._records_count.setText(f"Visar {shown} av {len(records)}")
        finally:
            self._records_tree.setUpdatesEnabled(True)

    def _on_search_changed(self, _text: str) -> None:
        if self._search_timer is not None:
            self._search_timer.start()

    def _perform_search(self) -> None:
        query = self._search_edit.text().strip() if self._search_edit is not None else ""
        self._update_records_tree(self._engine.search(query) if query else self._records)

    @Slot()
    def _clear_search(self) -> None:
        if self._search_edit is not None:
            self._search_edit.clear()
        self._update_records_tree(self._records)

    @Slot(QTreeWidgetItem, int)
    def _open_record(self, item: QTreeWidgetItem, _column: int) -> None:
        path_text = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if path_text and Path(path_text).is_file():
            self._open_path(Path(path_text))
        else:
            QMessageBox.warning(self._tab, "Filen saknas", item.text(6))

    def _get_report_config(self) -> ReportConfig:
        period_data = (
            self._report_period.currentData()
            if self._report_period is not None
            else TimePeriod.ALL_TIME
        )
        format_data = (
            self._report_format.currentData()
            if self._report_format is not None
            else ReportFormat.HTML
        )
        return ReportConfig(
            title=(
                self._report_title.text().strip()
                if self._report_title is not None and self._report_title.text().strip()
                else "Statistikrapport"
            ),
            period=period_data if isinstance(period_data, TimePeriod) else TimePeriod.ALL_TIME,
            format=format_data if isinstance(format_data, ReportFormat) else ReportFormat.HTML,
            include_summary=self._include_summary.isChecked() if self._include_summary is not None else True,
            include_charts=self._include_charts.isChecked() if self._include_charts is not None else True,
            include_details=self._include_details.isChecked() if self._include_details is not None else True,
        )

    @Slot()
    def _on_generate_report(self) -> None:
        config = self._get_report_config()
        suffix = config.format.value
        selected, _ = QFileDialog.getSaveFileName(
            self._tab,
            "Spara rapport",
            f"rapport_{datetime.now():%Y%m%d}.{suffix}",
            f"Rapport (*.{suffix});;Alla filer (*.*)",
        )
        if not selected:
            return
        if self._generate_report(Path(selected), config):
            QMessageBox.information(self._tab, "Rapport", f"✅ Sparad:\n{selected}")
            if config.format in {ReportFormat.HTML, ReportFormat.PDF}:
                webbrowser.open(Path(selected).as_uri())
        else:
            QMessageBox.critical(self._tab, "Rapportfel", "Kunde inte generera rapporten.")

    @Slot()
    def _on_preview_report(self) -> None:
        config = self._get_report_config()
        config.format = ReportFormat.TEXT
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            if self._generate_report(temporary, config) and self._report_preview is not None:
                self._report_preview.setPlainText(temporary.read_text(encoding="utf-8"))
        finally:
            with suppress(OSError):
                temporary.unlink()

    @Slot()
    def _on_quick_report(self) -> None:
        config = self._get_report_config()
        config.format = ReportFormat.HTML
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".html", delete=False) as handle:
            temporary = Path(handle.name)
        if self._generate_report(temporary, config):
            webbrowser.open(temporary.as_uri())
            if self._status_bar is not None:
                self._status_bar.set_success("Snabbrapport öppnad")

    def _generate_report(self, target: Path, config: ReportConfig) -> bool:
        filtered = self._engine.filter_by_period(config.period)
        summary = self._engine.calculate_summary(filtered, config.period)
        summary.total_templates_available = len(self._template_names)
        return bool(ReportGenerator(summary, filtered, config).generate(target))

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: Any) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                startfile = getattr(os, "startfile", None)
                if callable(startfile):
                    startfile(str(path))
                    return
                raise RuntimeError("Windows startfile är inte tillgänglig")
            command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
            subprocess.Popen(
                command,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, ValueError):
            self._log.exception("[STATISTICS] Kunde inte öppna %s", path)
            QMessageBox.warning(self._tab, "Kunde inte öppna", str(path))

    # ── Publikt API ───────────────────────────────────────────────────────

    def get_statistics(self) -> dict[str, Any]:
        summary = self._engine.calculate_summary(self._records, TimePeriod.ALL_TIME)
        summary.total_templates_available = len(self._template_names)
        return {
            "records": len(self._records),
            "engine_available": ENGINE_AVAILABLE,
            "templates_available": len(self._template_names),
            "template_names": list(self._template_names),
            "data_file": str(self._data_file or ""),
            "output_path": str(self._output_path or ""),
            "summary": summary.to_dict(),
            "version": self.version,
            "copyright": COPYRIGHT,
        }
