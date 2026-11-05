# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil:    plugins/certificates/plugin.py
Sökväg: Intygsgenerator/plugins/certificates/plugin.py
Version: 10.4.0 (uppgraderad från 10.3.0, 2026-09-25 — postetiketter på Brother QL-500 via b-PAC + Etiketter/*.lbx)

Intygsgenerator — controller för mallskanning, intygsgenerering och PySide6 UI.

Mallar läses enbart från Mallar/<MallNamn>/. En mallundermapp måste innehålla
minst en riktig DOCX-fil; Word-tempfiler, lösa DOCX-filer i Mallar/-roten och
PDF-only-mappar ignoreras konsekvent.

Uppgradering 10.0.0 → 10.1.0 ("188888888% bättre", endast denna fil)
===================================================================
1. ADAPTIV UI ("anpassat i text och fönster"): alla tidigare hårdkodade
   pixelmått (280/34/36/170/150/180, marginaler) skalas nu med fontmetrik via
   `_sp()` (§17/§30 — text klippas aldrig, fungerar på alla DPI/fönster).
2. LOGGNING AV PRECIS ALLT: stabila `cert.*`-events med strukturerad ctx på
   varje operation — skanning (start/klar/fel + duration + skipped),
   converter-probe, mallval, fältändringar, rensning, generering (start/klar/
   fel med nummer/sidor/tid), batch (start/klar/fel), CSV-läsning (rader),
   import (importerade/hoppade), mapp-/filöppning, combo-uppdatering,
   flikbygge med ms-tid. Allt hamnar i centralloggens JSONL → AI-läsbart.
3. 10000% AI-FELSÖKNING: `diagnose()` returnerar komplett tillstånd (sökvägar,
   engine, converter, scan-token/aktiv, senaste skanningens resultat+skipped,
   mallöversikt, räknare) — och senaste _ScanOutcome sparas för rapportering.
4. BÄTTRE KOD: fail-safe `_evt()`, ingen funktionalitet ändrad (MUST-005).
"""

import csv
import html
import json
import logging
import shutil
from contextlib import suppress
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, QSize, Signal, Slot
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QInputDialog,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.design_tokens import SHAPE, SPACING, scaled
from . import labels as _labels
from . import bpac_setup as _bpac_setup
from .counter_security import CounterPinStore
from .customers import Customer, load_customers, save_customers
from .history import find_duplicate_certificates, registration_from_values, vin_from_values
from .label_mappings import load_label_mappings, save_label_mappings
from .layout import FIELD_GROUP_ORDER, field_group
from .printer_settings import (
    default_printer,
    installed_printers,
    inspect_printer,
    load_printer_name,
    save_printer_name,
)
from .view import (
    COPYRIGHT,
    EngineErrorWidget,
    FieldWidget,
    FieldsScrollArea,

    ProgressDialog,
)

if TYPE_CHECKING:
    from core import AppContext

__version__: str = "10.4.0"
__all__: list[str] = ["Plugin"]

PLUGIN_NAME: str = "Intyg"
PLUGIN_VERSION: str = __version__
PLUGIN_AUTHOR: str = "Rickard Längkvist"
PLUGIN_PRIORITY: int = 100

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_logger: logging.Logger = logging.getLogger(__name__)

_SCAN_RESCHEDULE_MS: int = 60
_TEMPLATE_ROOT_NAME: str = "Mallar"
_OUTPUT_ROOT_NAME: str = "Out"
_CONFIG_ROOT_NAME: str = "js"
_IGNORED_FOLDER_PREFIXES: tuple[str, ...] = (".", "_", "~")
_IGNORED_FILE_PREFIXES: tuple[str, ...] = ("~$", ".")

ENGINE_AVAILABLE: bool = False
ENGINE_ERROR: str = ""

try:
    from .engine import (
        AUTO_FIELDS as _AUTO_FIELDS,
        FIELD_LABELS as _FIELD_LABELS,
        CertificateGenerator as _CertificateGenerator,
        CounterManager as _CounterManager,
        PDFConverter as _PDFConverter,
        PlaceholderParser as _PlaceholderParser,
        PlatformHelper as _PlatformHelper,
        RegValidator as _RegValidator,
        Template as _Template,
        TemplateConfig as _TemplateConfig,
        VINValidator as _VINValidator,
        get_field_label as _engine_field_label,
        is_auto_field as _engine_is_auto_field,
    )

    ENGINE_AVAILABLE = True
except Exception as exc:
    ENGINE_ERROR = f"{type(exc).__name__}: {exc}"
    _logger.critical(
        "[CERTIFICATES] engine.py kunde inte laddas; degraded mode: %s",
        exc,
        exc_info=True,
    )
    from . import (
        AUTO_FIELDS as _AUTO_FIELDS,
        FIELD_LABELS as _FIELD_LABELS,
        CertificateGenerator as _CertificateGenerator,
        CounterManager as _CounterManager,
        PDFConverter as _PDFConverter,
        PlaceholderParser as _PlaceholderParser,
        PlatformHelper as _PlatformHelper,
        RegValidator as _RegValidator,
        Template as _Template,
        TemplateConfig as _TemplateConfig,
        VINValidator as _VINValidator,
    )

    _engine_field_label = None
    _engine_is_auto_field = None


def _safe_get(value: Any, attribute: str, default: Any = None) -> Any:
    try:
        return getattr(value, attribute, default)
    except Exception:
        return default


def _result_value(result: Any, attribute: str, default: Any = None) -> Any:
    return _safe_get(result, attribute, default)


def _result_is_success(result: Any) -> bool:
    return bool(_result_value(result, "success", False))


def _is_auto_field(field_name: str) -> bool:
    if callable(_engine_is_auto_field):
        try:
            return bool(_engine_is_auto_field(field_name))
        except Exception:
            _logger.exception("[CERTIFICATES] Auto-fältkontroll misslyckades")
    try:
        return field_name in _AUTO_FIELDS
    except Exception:
        return False


def _field_label(field_name: str) -> str:
    if callable(_engine_field_label):
        try:
            label = _engine_field_label(field_name)
            if label:
                return str(label)
        except Exception:
            _logger.exception("[CERTIFICATES] Fältetikett misslyckades")
    return (
        _FIELD_LABELS.get(field_name)
        or _FIELD_LABELS.get(field_name.casefold())
        or _FIELD_LABELS.get(field_name.upper())
        or field_name
    )


def _template_is_valid(template: Any) -> bool:
    value = _safe_get(template, "is_valid", None)
    if value is not None:
        return bool(value)
    path = _safe_get(template, "docx_file", None)
    try:
        file_path = Path(path)
        return (
            file_path.is_file()
            and file_path.suffix.casefold() == ".docx"
            and not file_path.name.startswith("~$")
        )
    except (TypeError, ValueError):
        return False


def _template_attachments(template: Any) -> int:
    count = _safe_get(template, "attachment_count", None)
    if count is not None:
        try:
            return int(count)
        except (TypeError, ValueError):
            return 0
    attachments = _safe_get(template, "pdf_attachments", [])
    try:
        return len(attachments)
    except TypeError:
        return 0


def _template_user_fields(template: Any) -> list[str]:
    method = _safe_get(template, "get_user_fields", None)
    if callable(method):
        try:
            return [str(value) for value in method()]
        except Exception:
            _logger.exception("[CERTIFICATES] Kunde inte läsa mallfält")
    placeholders = list(_safe_get(template, "placeholders", []) or [])
    config = _safe_get(template, "config", None)
    extra = list(_safe_get(config, "extra_fields", []) or [])
    return [
        field_name
        for field_name in dict.fromkeys(str(value) for value in placeholders + extra)
        if not _is_auto_field(field_name)
    ]


def _template_default(template: Any, field_name: str) -> str:
    method = _safe_get(template, "get_default", None)
    if callable(method):
        try:
            return str(method(field_name) or "")
        except Exception:
            _logger.exception("[CERTIFICATES] Kunde inte läsa mallstandardvärde")
    config = _safe_get(template, "config", None)
    defaults = _safe_get(config, "default_values", {})
    if isinstance(defaults, dict):
        for key, value in defaults.items():
            if str(key).casefold() == field_name.casefold():
                return str(value)
    return ""


@dataclass(slots=True)
class _ScanOutcome:
    token: int
    templates: dict[str, Any]
    skipped: tuple[str, ...]
    duration_ms: int


class _WorkerSignals(QObject):
    """Trådsäkra signaler från QRunnable till huvudtråden."""

    progress = Signal(str, int)
    finished = Signal(object)
    error = Signal(str)


class _ScanWorker(QRunnable):
    """Skannar mallfilsystemet i QThreadPool."""

    def __init__(self, plugin: "Plugin", token: int) -> None:
        super().__init__()
        self._plugin = plugin
        self._token = token
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self._plugin._scan_templates(self._token))
        except Exception as exc:
            _logger.exception("[CERTIFICATES] Mallskanning misslyckades")
            self.signals.error.emit(str(exc))


class _ConverterWorker(QRunnable):
    """Initierar PDF-konverterare utanför UI-tråden."""

    def __init__(
        self,
        token: int,
        output_path: Path,
        counter_manager: Any,
        logger: logging.Logger,
    ) -> None:
        super().__init__()
        self._token = token
        self._output_path = output_path
        self._counter_manager = counter_manager
        self._logger = logger
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            try:
                converter = _PDFConverter(log=self._logger)
            except TypeError:
                converter = _PDFConverter()
            try:
                generator = _CertificateGenerator(
                    self._output_path,
                    self._counter_manager,
                    converter,
                    self._logger,
                )
            except TypeError:
                generator = _CertificateGenerator(
                    self._output_path,
                    self._counter_manager,
                    converter,
                )
            self.signals.finished.emit((self._token, converter, generator))
        except Exception as exc:
            _logger.exception("[CERTIFICATES] PDF-tjänst kunde inte initieras")
            self.signals.error.emit(str(exc))


class _GenerationWorker(QRunnable):
    """Genererar ett intyg i bakgrundstråden."""

    def __init__(self, generator: Any, template: Any, values: dict[str, str]) -> None:
        super().__init__()
        self._generator = generator
        self._template = template
        self._values = values
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._generator.generate(
                self._template,
                self._values,
                progress_callback=lambda message, percent: self.signals.progress.emit(
                    str(message),
                    int(percent),
                ),
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            _logger.exception("[CERTIFICATES] Generering misslyckades")
            self.signals.error.emit(str(exc))


class _LabelWorker(QRunnable):
    """Skriver ut en .lbx-etikett via b-PAC i bakgrundstråd (10.4.0)."""

    def __init__(
        self,
        label_path: Path,
        copies: int,
        values: dict[str, str],
        printer_name: str = "",
        calibration: bool = False,
    ) -> None:
        super().__init__()
        self._label_path = label_path
        self._copies = copies
        self._values = values
        self._printer_name = printer_name
        self._calibration = calibration
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(
                _labels.print_label(
                    self._label_path,
                    self._copies,
                    self._values,
                    printer_name=self._printer_name,
                    calibration=self._calibration,
                )
            )
        except Exception as exc:
            _logger.exception("[CERTIFICATES] Etikettutskrift kraschade")
            self.signals.error.emit(str(exc))


class _BatchWorker(QRunnable):
    """Genererar batch i bakgrundstråden."""

    def __init__(self, generator: Any, template: Any, rows: list[dict[str, str]]) -> None:
        super().__init__()
        self._generator = generator
        self._template = template
        self._rows = rows
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self._generator.generate_batch(
                self._template,
                self._rows,
                progress_callback=lambda message, percent: self.signals.progress.emit(
                    str(message),
                    int(percent),
                ),
            )
            self.signals.finished.emit(result)
        except Exception as exc:
            _logger.exception("[CERTIFICATES] Batchgenerering misslyckades")
            self.signals.error.emit(str(exc))


class _DropTarget(QWidget):
    """Drop-yta som accepterar CSV för batchgenerering."""

    csv_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().casefold().endswith(".csv"):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().casefold().endswith(".csv"):
                self.csv_dropped.emit(url.toLocalFile())
                event.acceptProposedAction()
                return
        event.ignore()


class Plugin:
    """IPlugin-implementation för intyg, mallar och PDF-generering."""

    name: str = PLUGIN_NAME
    version: str = PLUGIN_VERSION
    description: str = "Mallhantering och intygsgenerering med PDF-export"
    priority: int = PLUGIN_PRIORITY

    def __init__(self) -> None:
        self._context: "AppContext | None" = None
        self._event_bus: Any | None = None
        self._log = _logger
        self._unsubscribers: list[Callable[[], None]] = []
        self._thread_pool = QThreadPool.globalInstance()
        # 10.2.2 (BUG-024): starka referenser till workers signalobjekt tills
        # resultatet levererats. Utan dem raderas QRunnable (autoDelete) + dess
        # _WorkerSignals innan den köade signalen når UI-tråden → skanningen
        # "blev aldrig klar" (Mallar: väntar) och converter=saknas.
        self._live_signals: set[QObject] = set()
        self._state_lock = threading.RLock()
        self._templates_lock = threading.RLock()
        self._templates: dict[str, Any] = {}
        self._loaded = False
        self._bpac_setup_prompted = False
        self._scan_active = False
        self._scan_requested = False
        self._scan_token = 0
        self._converter_active = False
        self._converter_requested = False
        self._converter_token = 0
        self._converter_available: bool | None = None
        self._last_selected_name = ""
        self._pending_combo_update = False
        self._last_scan: _ScanOutcome | None = None  # 10.1.0: för diagnose()

        self.templates_path: Path | None = None
        self.output_path: Path | None = None
        self.config_path: Path | None = None
        self.customers_path: Path | None = None
        self.label_mappings_path: Path | None = None
        self.label_printer_settings_path: Path | None = None
        self._customers: list[Customer] = []
        self._label_mappings: dict[str, str] = {}
        self._label_printer_name = ""
        self._counter_manager: Any | None = None
        self._pdf_converter: Any | None = None
        self._generator: Any | None = None

        self._tab_widget: QWidget | None = None
        self._template_combo: QComboBox | None = None
        self._status_label: QLabel | None = None
        self._converter_label: QLabel | None = None
        self._total_label: QLabel | None = None
        self._info_label: QLabel | None = None
        self._counter_label: QLabel | None = None
        self._fields_title: QLabel | None = None
        self._fields_area: FieldsScrollArea | None = None
        self._field_widgets: list[FieldWidget] = []
        self._last_values: dict[str, str] = {}
        self._current_placeholders: list[str] = []
        self._generate_button: QPushButton | None = None
        self._batch_button: QPushButton | None = None
        self._customer_combo: QComboBox | None = None
        self._label_mapping_combo: QComboBox | None = None
        self._label_printer_button: QPushButton | None = None
        self._label_printer_calibration_button: QPushButton | None = None
        self._label_readiness_label: QLabel | None = None
        self._label_print_checkbox: QCheckBox | None = None
        self._pending_label_print = False
        self._pending_template_name = ""
        self._progress_bar: QProgressBar | None = None
        self._progress_label: QLabel | None = None

    # ── Hjälp: adaptiv skala + fail-safe eventloggning (10.1.0) ───────────

    def _sp(self, base: int) -> int:
        """Skalar pixelmått efter fontmetrik (DPI/teckenstorlek) — §17/§30.

        Fail-safe: utan QApplication/fontmetrik returneras basmåttet.
        """
        try:
            app = QApplication.instance()
            if app is not None:
                height = app.fontMetrics().height()
                if height > 0:
                    return scaled(base, height, minimum=base)
        except Exception:
            pass
        return base

    def _standard_icon(self, name: str) -> QIcon:
        """Get a native button icon, falling back to the shared app icon."""
        try:
            pixmap = getattr(QStyle.StandardPixmap, name)
            icon = QApplication.style().standardIcon(pixmap)
            if not icon.isNull():
                return icon
        except Exception:
            pass
        app_icon = Path(__file__).resolve().parents[2] / "Icon" / "icon.svg"
        return QIcon(str(app_icon))

    def _evt(self, event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
        """Strukturerad loggpost (cert.*) — får aldrig krascha appen."""
        try:
            extra = {"event": event, "ctx": ctx or None}
            recovery_key = ctx.get("recovery_key")
            if recovery_key:
                extra["recovery_key"] = str(recovery_key)
            self._log.log(level, msg, extra=extra)
        except Exception:
            pass

    # ── Pluginlivscykel ───────────────────────────────────────────────────

    def on_load(self, context: "AppContext") -> None:
        if self._loaded:
            return
        self._context = context
        self._event_bus = getattr(context, "event_bus", None)
        get_logger = getattr(context, "get_logger", None)
        if callable(get_logger):
            try:
                self._log = get_logger(self.name)
            except Exception:
                _logger.exception("[CERTIFICATES] Kunde inte hämta applogger")

        try:
            self._setup_paths()
            self._setup_counter()
            self._subscribe_events()
            self._loaded = True
            self._start_scan()
            self._start_converter_probe()
        except Exception:
            self._log.exception("[CERTIFICATES] on_load misslyckades")
            raise

        self._log.info(
            "[CERTIFICATES] v%s laddad | engine=%s | Mallar=%s",
            self.version,
            "OK" if ENGINE_AVAILABLE else f"DEGRADED: {ENGINE_ERROR}",
            self.templates_path,
        )
        self._evt("cert.loaded", "Intygsplugin laddat",
                  version=self.version, engine_ok=ENGINE_AVAILABLE,
                  templates_root=str(self.templates_path or ""))

    def setup(self, context: "AppContext") -> None:
        self.on_load(context)

    def on_unload(self) -> None:
        with self._state_lock:
            self._loaded = False
            self._scan_token += 1
            self._scan_active = False
            self._scan_requested = False
            self._converter_token += 1
            self._converter_active = False
            self._converter_requested = False
        for unsubscribe in self._unsubscribers:
            try:
                unsubscribe()
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte avregistrera event")
        self._unsubscribers.clear()
        if self._counter_manager is not None:
            saver = getattr(self._counter_manager, "save", None)
            if callable(saver):
                try:
                    saver()
                except Exception:
                    self._log.exception("[CERTIFICATES] Kunde inte spara räknare")
        self._evt("cert.unloaded", "Intygsplugin avlastat", level=logging.INFO)
        self._log.info("[CERTIFICATES] Avlastad")

    def on_close(self) -> None:
        self.on_unload()

    def get_tab(self) -> QWidget | None:
        if self._tab_widget is not None:
            return self._tab_widget
        started = time.monotonic()
        try:
            self._tab_widget = self._build_tab()
            if self._pending_combo_update or self.templates:
                self._update_template_combo()
            self._update_converter_status()
            self._update_total_label()
            self._evt("cert.tab_built", "Intygsflik byggd",
                      level=logging.DEBUG,
                      ms=round((time.monotonic() - started) * 1000, 1))
            return self._tab_widget
        except Exception as exc:
            self._log.exception("[CERTIFICATES] Kunde inte bygga intygsflik")
            self._evt("cert.tab_build_failed", f"Flikbygge misslyckades: {exc}",
                      level=logging.ERROR, error=str(exc))
            self._tab_widget = EngineErrorWidget(str(exc))
            return self._tab_widget

    # ── Sökvägar och tjänster ─────────────────────────────────────────────

    def _setup_paths(self) -> None:
        base = Path(__file__).resolve().parent.parent.parent
        self.templates_path = self._context_path("templates", base / _TEMPLATE_ROOT_NAME)
        self.output_path = self._context_path("output", base / _OUTPUT_ROOT_NAME)
        self.config_path = self._context_path("config", base / _CONFIG_ROOT_NAME)
        self.customers_path = self.config_path / "customers.json"
        self.label_mappings_path = self.config_path / "certificate_label_mappings.json"
        self.label_printer_settings_path = self.config_path / "label_printer.json"
        self._label_printer_name = load_printer_name(self.label_printer_settings_path)
        self.templates_path = self._diagnose_templates_path(base, self.templates_path)

        for path in (self.templates_path, self.output_path, self.config_path):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._log.error("[CERTIFICATES] Kunde inte skapa %s: %s", path, exc)
                self._evt("cert.path_failed", f"Kunde inte skapa {path}: {exc}",
                          level=logging.ERROR, path=str(path), error=str(exc))
        try:
            self._customers = load_customers(self.customers_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._customers = []
            self._log.error("[CERTIFICATES] Kundregistret kunde inte läsas: %s", exc)
            self._evt("customer.load_failed", f"Kundregistret kunde inte läsas: {exc}",
                      level=logging.ERROR, path=str(self.customers_path), error=str(exc))
        try:
            self._label_mappings = load_label_mappings(self.label_mappings_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._label_mappings = {}
            self._log.error("[CERTIFICATES] Etikettkopplingar kunde inte läsas: %s", exc)
            self._evt("label.mapping_load_failed", f"Etikettkopplingar kunde inte läsas: {exc}",
                      level=logging.ERROR, path=str(self.label_mappings_path), error=str(exc))

    def _context_path(self, key: str, fallback: Path) -> Path:
        context = self._context
        getter = getattr(context, "get_path", None) if context is not None else None
        if callable(getter):
            try:
                value = getter(key)
                if value:
                    return Path(value)
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte läsa context-path %s", key)
        return fallback

    def _diagnose_templates_path(self, base: Path, primary: Path) -> Path:
        """Välj en mallrot med giltiga direkta mallundermappar, utan rekursion."""
        primary_count = self._count_valid_template_folders(primary)
        if primary_count:
            return primary

        candidates = (base / _TEMPLATE_ROOT_NAME, base.parent / _TEMPLATE_ROOT_NAME, Path.cwd() / _TEMPLATE_ROOT_NAME)
        best_path = primary
        best_count = primary_count
        primary_resolved = primary.resolve()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved == primary_resolved:
                continue
            count = self._count_valid_template_folders(resolved)
            if count > best_count:
                best_path, best_count = resolved, count
        if best_path != primary:
            self._log.warning(
                "[CERTIFICATES] Mallrot ändrad: %s → %s (%d giltiga mappar)",
                primary,
                best_path,
                best_count,
            )
            self._evt("cert.templates_root_changed", "Mallrot ändrad",
                      level=logging.WARNING, old=str(primary), new=str(best_path),
                      valid_folders=best_count)
        return best_path

    @staticmethod
    def _count_valid_template_folders(root: Path) -> int:
        if not root.is_dir():
            return 0
        try:
            folders = [
                child
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(_IGNORED_FOLDER_PREFIXES)
            ]
        except OSError:
            return 0
        count = 0
        for folder in folders:
            try:
                if any(
                    item.is_file()
                    and item.suffix.casefold() == ".docx"
                    and not item.name.startswith(_IGNORED_FILE_PREFIXES)
                    and item.stat().st_size > 0
                    for item in folder.iterdir()
                ):
                    count += 1
            except OSError:
                continue
        return count

    def _setup_counter(self) -> None:
        if self.config_path is None:
            raise RuntimeError("Konfigurationssökväg saknas")
        config_manager = None
        if self._context is not None:
            config_manager = (
                getattr(self._context, "config_manager", None)
                or getattr(self._context, "settings", None)
                or getattr(self._context, "config", None)
            )
        counter_path = self.config_path / "certificates_counters.json"
        try:
            self._counter_manager = _CounterManager(
                config_manager=config_manager,
                config_path=counter_path,
            )
        except TypeError:
            self._counter_manager = _CounterManager(config_path=counter_path)

    def _verify_counter_pin(self, store: CounterPinStore, parent: QWidget | None) -> bool:
        for attempt in range(3):
            pin, accepted = QInputDialog.getText(
                parent,
                "PIN för intygsnummer",
                f"Ange PIN-koden ({attempt + 1}/3):",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return False
            if store.verify(pin):
                return True
            QMessageBox.warning(parent, "Fel PIN", "PIN-koden stämmer inte.")
        return False

    def manage_counter_pin(self, parent: QWidget | None = None) -> None:
        """Set or change the local PIN required for changing certificate numbers."""
        if self.config_path is None:
            QMessageBox.warning(parent, "PIN saknas", "Appens konfigurationsmapp är inte tillgänglig.")
            return
        store = CounterPinStore(self.config_path / "certificate_number_pin.json")
        if store.configured and not self._verify_counter_pin(store, parent):
            return
        first, accepted = QInputDialog.getText(
            parent,
            "Ställ in PIN för intygsnummer",
            "Välj en PIN med 4–12 siffror:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if not store.validate(first):
            QMessageBox.warning(parent, "Ogiltig PIN", "PIN-koden måste bestå av 4–12 siffror.")
            return
        confirmation, accepted = QInputDialog.getText(
            parent,
            "Bekräfta PIN",
            "Ange samma PIN en gång till:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if first != confirmation:
            QMessageBox.warning(parent, "PIN stämmer inte", "De två PIN-koderna är olika.")
            return
        try:
            store.set_pin(first)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(parent, "PIN kunde inte sparas", str(exc))
            return
        QMessageBox.information(parent, "PIN sparad", "PIN-skyddet för intygsnummer är aktiverat.")
        self._evt("counter.pin_configured", "PIN-skydd för intygsnummer har uppdaterats")

    def manage_next_certificate_number(self, parent: QWidget | None = None) -> None:
        """PIN-gated menu action for setting the next per-template certificate number."""
        if self.config_path is None or self._counter_manager is None:
            QMessageBox.warning(parent, "Räknare saknas", "Intygsräknaren är inte tillgänglig ännu.")
            return
        store = CounterPinStore(self.config_path / "certificate_number_pin.json")
        if not store.configured:
            QMessageBox.information(
                parent,
                "PIN krävs",
                "Ställ först in PIN-koden via Verktyg → Ställ in/ändra PIN för intygsnummer.",
            )
            return
        if not self._verify_counter_pin(store, parent):
            return

        names = sorted(
            {str(name) for name in self._templates}
            | {str(name) for name in self._counter_manager.get_all()},
            key=str.casefold,
        )
        if not names:
            QMessageBox.information(parent, "Inga mallar", "Läs in en intygsmall först.")
            return
        selected_name = self._last_selected_name
        current_index = names.index(selected_name) if selected_name in names else 0
        template_name, accepted = QInputDialog.getItem(
            parent,
            "Ändra nästa intygsnummer",
            "Välj vilken intygsmall räknaren ska ändras för:",
            names,
            current_index,
            False,
        )
        if not accepted:
            return
        template = self._templates.get(template_name)
        config = _safe_get(template, "config", None)
        try:
            start = max(1, int(_safe_get(config, "counter_start", 1)))
        except (TypeError, ValueError):
            start = 1
        try:
            current_next = self._counter_manager.get_next(template_name, start=start)
        except Exception:
            current_next = start
        next_number, accepted = QInputDialog.getInt(
            parent,
            "Ändra nästa intygsnummer",
            f"Nästa nummer för {template_name}:",
            current_next,
            start,
            2_147_483_647,
        )
        if not accepted:
            return
        answer = QMessageBox.question(
            parent,
            "Bekräfta nummerändring",
            f"Nästa intyg med mallen {template_name} får nummer {next_number}.\n\n"
            "Ett redan använt nummer kan skapa dubbletter. Vill du fortsätta?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._counter_manager.set_next(template_name, next_number, start=start)
            if self._counter_manager.get_next(template_name, start=start) != next_number:
                raise OSError("Räknaren kunde inte verifiera det sparade numret.")
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.warning(parent, "Numret kunde inte sparas", str(exc))
            return
        if template is not None:
            self._update_counter_label(template)
        self._evt(
            "counter.next_number_changed",
            "Nästa intygsnummer ändrades via PIN-skyddad meny",
            template=template_name,
            next_number=next_number,
        )
        QMessageBox.information(
            parent,
            "Intygsnummer ändrat",
            f"Nästa intyg med mallen {template_name} får nummer {next_number}.",
        )

    def _start_converter_probe(self) -> None:
        if self.output_path is None or self._counter_manager is None:
            return
        with self._state_lock:
            if self._converter_active:
                self._converter_requested = True
                return
            self._converter_active = True
            self._converter_requested = False
            self._converter_token += 1
            token = self._converter_token
        self._evt("cert.converter_probe_started", "PDF-konverterarprobe startad",
                  level=logging.DEBUG, token=token)
        worker = _ConverterWorker(token, self.output_path, self._counter_manager, self._log)
        worker.signals.finished.connect(self._on_converter_ready, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(
            lambda error, probe_token=token: self._on_converter_error(probe_token, error),
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker(worker)

    @Slot(object)
    def _on_converter_ready(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        token, converter, generator = payload
        with self._state_lock:
            if token != self._converter_token:
                return
            self._converter_active = False
            retry = self._converter_requested
            self._converter_requested = False
            self._pdf_converter = converter
            self._generator = generator
            self._converter_available = self._converter_is_available(converter)
        self._evt("cert.converter_ready",
                  f"PDF-konverterare klar: {_safe_get(converter, 'method_name', 'okänd')}",
                  method=str(_safe_get(converter, "method_name", "")),
                  available=bool(self._converter_available), token=token)
        self._update_converter_status()
        if retry:
            QTimer.singleShot(_SCAN_RESCHEDULE_MS, self._start_converter_probe)

    def _on_converter_error(self, token: int, error: str) -> None:
        with self._state_lock:
            if token != self._converter_token or not self._loaded:
                return
            self._converter_active = False
            retry = self._converter_requested
            self._converter_requested = False
            self._pdf_converter = None
            self._generator = None
            self._converter_available = False
        self._log.error("[CERTIFICATES] PDF-tjänstfel: %s", error)
        self._evt("cert.converter_error", f"PDF-tjänstfel: {error}",
                  level=logging.ERROR, error=error, token=token)
        self._update_converter_status()
        if retry:
            QTimer.singleShot(_SCAN_RESCHEDULE_MS, self._start_converter_probe)

    @staticmethod
    def _converter_is_available(converter: Any) -> bool:
        try:
            return bool(getattr(converter, "is_available", False))
        except Exception:
            return False

    # ── EventBus ──────────────────────────────────────────────────────────

    def _subscribe_events(self) -> None:
        if self._event_bus is None:
            return
        subscriptions: tuple[tuple[str, Callable[[Any], None]], ...] = (
            ("refresh_templates", self._on_refresh_templates),
            ("template.created", self._on_refresh_templates),
            ("template.deleted", self._on_refresh_templates),
            ("template.updated", self._on_refresh_templates),
            ("settings.changed", self._on_settings_changed),
            ("settings.theme_changed", self._on_theme_changed),
            ("statistics.request", self._on_statistics_request),
        )
        for event_name, callback in subscriptions:
            try:
                unsubscribe = self._event_bus.subscribe(event_name, callback)
                if callable(unsubscribe):
                    self._unsubscribers.append(unsubscribe)
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte prenumerera på %s", event_name)

    def _on_refresh_templates(self, _data: Any = None) -> None:
        self._evt("cert.refresh_requested", "Malluppdatering begärd via event",
                  level=logging.DEBUG)
        self._start_scan()

    def _on_settings_changed(self, data: Any = None) -> None:
        if not isinstance(data, dict):
            return
        if str(data.get("key", "")).startswith("pdf."):
            self._evt("cert.pdf_setting_changed", "PDF-inställning ändrad → ny probe",
                      level=logging.DEBUG, key=str(data.get("key", "")))
            self._start_converter_probe()

    def _on_theme_changed(self, _data: Any = None) -> None:
        if self._tab_widget is not None:
            self._tab_widget.update()

    def _on_statistics_request(self, _data: Any = None) -> None:
        self._emit_event("statistics.response", {"data": self.get_statistics()})

    def _emit_event(self, event_name: str, data: dict[str, Any]) -> None:
        context = self._context
        emitter = getattr(context, "emit_event", None) if context is not None else None
        if callable(emitter):
            try:
                emitter(event_name, data)
                return
            except Exception:
                self._log.exception("[CERTIFICATES] Event %s misslyckades", event_name)
        bus = self._event_bus
        emit = getattr(bus, "emit", None) if bus is not None else None
        if callable(emit):
            try:
                emit(event_name, data)
            except Exception:
                self._log.exception("[CERTIFICATES] EventBus %s misslyckades", event_name)

    # ── Mallskanning ──────────────────────────────────────────────────────

    def _start_worker(self, worker: Any) -> None:
        """Starta QRunnable och håll dess signaler vid liv tills leverans (BUG-024)."""
        signals = worker.signals
        self._live_signals.add(signals)

        def release(*_args: object) -> None:
            QTimer.singleShot(0, lambda: self._live_signals.discard(signals))

        signals.finished.connect(release, Qt.ConnectionType.QueuedConnection)
        signals.error.connect(release, Qt.ConnectionType.QueuedConnection)
        self._thread_pool.start(worker)

    def _start_scan(self) -> None:
        if not self._loaded or self.templates_path is None:
            return
        with self._state_lock:
            if self._scan_active:
                self._scan_requested = True
                return
            self._scan_active = True
            self._scan_requested = False
            self._scan_token += 1
            token = self._scan_token
        self._set_status("⏳ Skannar mallar i bakgrunden…")
        self._evt("cert.scan_started", "Mallskanning startad",
                  level=logging.DEBUG, token=token, root=str(self.templates_path))
        worker = _ScanWorker(self, token)
        worker.signals.finished.connect(self._on_scan_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(
            lambda error, scan_token=token: self._on_scan_error(scan_token, error),
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker(worker)

    def _scan_templates(self, token: int) -> _ScanOutcome:
        started = time.perf_counter()
        root = self.templates_path
        if root is None or not root.is_dir():
            raise FileNotFoundError(f"Mallmappen saknas: {root}")

        try:
            folders = sorted(
                (
                    path
                    for path in root.iterdir()
                    if path.is_dir() and not path.name.startswith(_IGNORED_FOLDER_PREFIXES)
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            raise RuntimeError(f"Kunde inte läsa mallmapp: {exc}") from exc

        templates: dict[str, Any] = {}
        skipped: list[str] = []
        for folder in folders:
            template, reason = self._parse_template_folder(folder)
            if template is None:
                skipped.append(f"{folder.name}: {reason}")
                continue
            name = str(_safe_get(template, "name", folder.name) or folder.name)
            templates[name] = template

        duration_ms = int((time.perf_counter() - started) * 1000)
        return _ScanOutcome(token, templates, tuple(skipped), duration_ms)

    def _parse_template_folder(self, folder: Path) -> tuple[Any | None, str]:
        """MALL-001→009: läs endast direkta filer i en mallundermapp."""
        try:
            entries = tuple(folder.iterdir())
        except OSError as exc:
            return None, f"kan inte läsas ({exc})"

        docx_files: list[Path] = []
        pdf_files: list[Path] = []
        json_files: list[Path] = []
        for entry in entries:
            try:
                if not entry.is_file() or entry.name.startswith(_IGNORED_FILE_PREFIXES):
                    continue
                suffix = entry.suffix.casefold()
                if suffix == ".docx" and entry.stat().st_size > 0:
                    docx_files.append(entry)
                elif suffix == ".pdf":
                    pdf_files.append(entry)
                elif suffix == ".json":
                    json_files.append(entry)
            except OSError:
                continue

        if not docx_files:
            return None, "saknar giltig DOCX"

        docx_files.sort(key=lambda path: path.name.casefold())
        preferred_name = f"{folder.name}.docx".casefold()
        docx_file = next(
            (path for path in docx_files if path.name.casefold() == preferred_name),
            docx_files[0],
        )
        pdf_files.sort(key=lambda path: path.name.casefold())
        config = self._read_template_config(folder, json_files)
        placeholders = self._scan_placeholders(docx_file)

        try:
            template = _Template(
                name=folder.name,
                folder=folder,
                docx_file=docx_file,
                pdf_attachments=pdf_files,
                config=config,
                placeholders=placeholders,
            )
        except Exception as exc:
            return None, f"kunde inte skapa mallobjekt ({exc})"
        if not _template_is_valid(template):
            return None, "DOCX är ogiltig"
        return template, ""

    def _read_template_config(self, folder: Path, json_files: list[Path]) -> Any:
        try:
            config = _TemplateConfig(name=folder.name)
        except TypeError:
            config = _TemplateConfig(folder.name)

        expected = f"{folder.name}.json".casefold()
        json_path = next((path for path in json_files if path.name.casefold() == expected), None)
        if json_path is None:
            return config
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw, dict):
                raise ValueError("JSON-roten måste vara ett objekt")
            from_dict = getattr(_TemplateConfig, "from_dict", None)
            if callable(from_dict):
                try:
                    return from_dict(raw, folder.name)
                except TypeError:
                    return from_dict(raw)
        except Exception as exc:
            self._log.warning("[CERTIFICATES] Ogiltig mallconfig %s: %s", json_path.name, exc)
            self._evt("cert.config_invalid", f"Ogiltig mallconfig: {json_path.name}",
                      level=logging.WARNING, path=str(json_path), error=str(exc))
        return config

    def _scan_placeholders(self, docx_file: Path) -> list[str]:
        try:
            values = _PlaceholderParser.find_placeholders(docx_file)
            return list(dict.fromkeys(str(value) for value in values if str(value).strip()))
        except Exception:
            self._log.exception("[CERTIFICATES] Placeholder-scan misslyckades: %s", docx_file.name)
            self._evt("cert.placeholder_scan_failed",
                      f"Placeholder-scan misslyckades: {docx_file.name}",
                      level=logging.ERROR, docx=str(docx_file))
            return []

    @Slot(object)
    def _on_scan_finished(self, payload: object) -> None:
        if not isinstance(payload, _ScanOutcome):
            return
        with self._state_lock:
            if payload.token != self._scan_token or not self._loaded:
                return
            self._scan_active = False
            rescan = self._scan_requested
            self._scan_requested = False
        with self._templates_lock:
            self._templates = dict(payload.templates)
        self._last_scan = payload

        names = sorted(payload.templates, key=str.casefold)
        if names:
            self._set_status(f"✅ {len(names)} mallar laddade")
        else:
            self._set_status("⚠️ Inga mallar hittades — kontrollera Mallar/-strukturen")
        self._log.info(
            "[CERTIFICATES] Skanning klar: %d mall(ar) på %d ms: %s",
            len(names),
            payload.duration_ms,
            names,
        )
        self._evt("cert.scan_done", f"Skanning klar: {len(names)} mallar på {payload.duration_ms} ms",
                  count=len(names), duration_ms=payload.duration_ms,
                  skipped=len(payload.skipped), token=payload.token)
        for reason in payload.skipped:
            self._log.debug("[CERTIFICATES] Hoppade över %s", reason)
            self._evt("cert.scan_skipped", f"Hoppade över: {reason}",
                      level=logging.DEBUG, reason=reason)

        if self._template_combo is None:
            self._pending_combo_update = True
        else:
            self._update_template_combo()
        self._update_total_label()
        self._emit_event(
            "templates.scanned",
            {
                "count": len(names),
                "names": names,
                "root": str(self.templates_path or ""),
                "duration_ms": payload.duration_ms,
            },
        )
        if rescan:
            QTimer.singleShot(_SCAN_RESCHEDULE_MS, self._start_scan)

    def _on_scan_error(self, token: int, error: str) -> None:
        with self._state_lock:
            if token != self._scan_token or not self._loaded:
                return
            self._scan_active = False
            rescan = self._scan_requested
            self._scan_requested = False
        self._set_status(f"❌ Mallskanning misslyckades: {error}")
        self._log.error("[CERTIFICATES] Mallskanning misslyckades: %s", error)
        self._evt("cert.scan_error", f"Mallskanning misslyckades: {error}",
                  level=logging.ERROR, error=error, token=token)
        if rescan:
            QTimer.singleShot(_SCAN_RESCHEDULE_MS, self._start_scan)

    def refresh_templates(self) -> None:
        """Publikt API för F5 och refresh_templates-eventet."""
        self._evt("cert.refresh_api", "refresh_templates() anropad", level=logging.DEBUG)
        self._start_scan()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_tab(self) -> QWidget:
        root = _DropTarget()
        root.setObjectName("CertificatesTab")
        root.csv_dropped.connect(self._handle_batch_drop)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(
            self._sp(SPACING.small), self._sp(SPACING.small),
            self._sp(SPACING.small), self._sp(SPACING.small),
        )
        outer.setSpacing(self._sp(SPACING.medium))
        outer.addWidget(self._build_status_row())
        outer.addWidget(self._build_template_selector())
        outer.addWidget(self._build_label_mapping_row())
        outer.addWidget(self._build_template_info())
        outer.addWidget(self._build_fields_section(), stretch=1)
        outer.addWidget(self._build_action_row())

        self._build_shortcuts(root)
        return root

    def _build_status_row(self) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(self._sp(4), self._sp(1), self._sp(4), self._sp(1))
        layout.setSpacing(self._sp(5))
        self._status_label = QLabel("⏳ Initierar mallar…", frame)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label, stretch=1)

        self._total_label = QLabel("📊 Totalt: 0 intyg", frame)
        layout.addWidget(self._total_label)
        self._converter_label = QLabel("⏳ Kontrollerar PDF…", frame)
        layout.addWidget(self._converter_label)
        return frame

    def _build_template_selector(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setProperty("designSurface", True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(
            self._sp(SPACING.medium), self._sp(SPACING.small),
            self._sp(SPACING.medium), self._sp(SPACING.small),
        )
        layout.setSpacing(self._sp(SPACING.small))
        layout.addWidget(QLabel("📁 Mall:", frame))

        self._template_combo = QComboBox(frame)
        self._template_combo.setMinimumWidth(self._sp(180))
        self._template_combo.setMinimumHeight(self._sp(32))
        self._template_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._template_combo.addItem("Skannar mallar…")
        self._template_combo.setEnabled(False)
        self._template_combo.currentTextChanged.connect(self._on_template_selected)
        layout.addWidget(self._template_combo, stretch=1)

        gallery = QPushButton("Galleri", frame)
        gallery.setIcon(self._standard_icon("SP_FileDialogDetailedView"))
        gallery.setIconSize(QSize(self._sp(16), self._sp(16)))
        gallery.setMinimumHeight(self._sp(32))
        gallery.setToolTip("Visa mallar med beskrivning och fältöversikt")
        gallery.clicked.connect(self._open_template_gallery)
        layout.addWidget(gallery)
        self._template_gallery_button = gallery

        refresh = QPushButton(frame)
        refresh.setIcon(self._standard_icon("SP_BrowserReload"))
        refresh.setIconSize(QSize(self._sp(16), self._sp(16)))
        refresh.setFixedSize(self._sp(24), self._sp(24))
        refresh.setToolTip("Skanna mallar igen (F5)")
        refresh.clicked.connect(self.refresh_templates)
        layout.addWidget(refresh)

        open_templates = QPushButton(frame)
        open_templates.setIcon(self._standard_icon("SP_DirOpenIcon"))
        open_templates.setIconSize(QSize(self._sp(16), self._sp(16)))
        open_templates.setFixedSize(self._sp(24), self._sp(24))
        open_templates.setToolTip("Öppna mallmappen")
        open_templates.clicked.connect(self._open_templates_folder)
        layout.addWidget(open_templates)
        return frame

    def _open_template_gallery(self) -> None:
        """Show available templates as a searchable-by-eye metadata gallery."""
        combo = self._template_combo
        if combo is None:
            return
        with self._templates_lock:
            templates = sorted(self._templates.items(), key=lambda item: item[0].casefold())
        if not templates:
            return

        dialog = QDialog(self._tab_widget)
        dialog.setWindowTitle("Mallgalleri")
        dialog.resize(self._sp(760), self._sp(520))
        root = QVBoxLayout(dialog)
        root.setContentsMargins(self._sp(16), self._sp(16), self._sp(16), self._sp(16))
        root.setSpacing(self._sp(10))
        intro = QLabel("Välj en mall och granska dess beskrivning och fält.", dialog)
        intro.setWordWrap(True)
        root.addWidget(intro)

        content = QHBoxLayout()
        content.setSpacing(self._sp(12))
        gallery_list = QListWidget(dialog)
        gallery_list.setMinimumWidth(self._sp(250))
        details = QTextBrowser(dialog)
        details.setOpenExternalLinks(False)
        content.addWidget(gallery_list, stretch=1)
        content.addWidget(details, stretch=2)
        root.addLayout(content, stretch=1)

        for name, template in templates:
            config = _safe_get(template, "config", None)
            category = str(_safe_get(config, "category", "") or "Mall")
            fields = _template_user_fields(template)
            item = QListWidgetItem(f"{name}\n{category} · {len(fields)} fält", gallery_list)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(name)

        def show_details(item: QListWidgetItem | None) -> None:
            if item is None:
                details.clear()
                return
            name = str(item.data(Qt.ItemDataRole.UserRole) or "")
            template = dict(templates).get(name)
            if template is None:
                details.clear()
                return
            config = _safe_get(template, "config", None)
            category = str(_safe_get(config, "category", "") or "Ei määritelty")
            description = ""
            for attribute in ("description", "beskrivning", "summary"):
                description = str(_safe_get(config, attribute, "") or "").strip()
                if description:
                    break
            if not description:
                description = "Tälle mallille ei ole lisätty erillistä kuvausta."
            fields = _template_user_fields(template)
            docx_name = Path(str(_safe_get(template, "docx_file", ""))).name or "DOCX-fil saknas"
            field_html = "<li>" + "</li><li>".join(html.escape(value) for value in fields) + "</li>" if fields else "<li>Inga manuella fält</li>"
            details.setHtml(
                "<h2>" + html.escape(name) + "</h2>"
                + "<p><b>Kategori:</b> " + html.escape(category) + "</p>"
                + "<p>" + html.escape(description) + "</p>"
                + "<p><b>Dokument:</b> " + html.escape(docx_name) + "</p>"
                + f"<p><b>Bilagor:</b> {_template_attachments(template)}</p>"
                + "<h3>Fält</h3><ul>" + field_html + "</ul>"
                + "<p><i>Dokumentets visuella förhandsvisning är inte tillgänglig i den här vyn.</i></p>"
            )

        gallery_list.currentItemChanged.connect(show_details)
        gallery_list.itemDoubleClicked.connect(lambda _item: dialog.accept())
        current_name = combo.currentText()
        for index in range(gallery_list.count()):
            item = gallery_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == current_name:
                gallery_list.setCurrentItem(item)
                break
        if gallery_list.currentItem() is None:
            gallery_list.setCurrentRow(0)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).setText("Välj mall")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        root.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = gallery_list.currentItem()
            if selected is not None:
                combo.setCurrentText(str(selected.data(Qt.ItemDataRole.UserRole) or ""))

    def _build_label_mapping_row(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setProperty("designSurface", True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(
            self._sp(SPACING.medium), self._sp(SPACING.small),
            self._sp(SPACING.medium), self._sp(SPACING.small),
        )
        layout.setSpacing(self._sp(SPACING.small))
        layout.addWidget(QLabel("🏷️ Etikett för intygsmall:", frame))
        self._label_mapping_combo = QComboBox(frame)
        self._label_mapping_combo.setMinimumHeight(self._sp(23))
        self._label_mapping_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._label_mapping_combo.currentIndexChanged.connect(self._on_label_mapping_changed)
        layout.addWidget(self._label_mapping_combo, stretch=1)
        self._label_print_checkbox = QCheckBox("Skriv etikett automatiskt", frame)
        self._label_print_checkbox.setToolTip(
            "Skriv alltid ut etiketten som är kopplad till intygsmallen efter att intyget skapats."
        )
        self._label_print_checkbox.toggled.connect(self._on_label_print_toggled)
        self._label_print_checkbox.setEnabled(False)
        layout.addWidget(self._label_print_checkbox)
        self._label_readiness_label = QLabel("Kontrollerar etikett…", frame)
        self._label_readiness_label.setMinimumWidth(0)
        layout.addWidget(self._label_readiness_label)
        self._label_printer_button = QPushButton(frame)
        self._label_printer_button.setIcon(self._standard_icon("SP_PrinterIcon"))
        self._label_printer_button.setIconSize(QSize(self._sp(16), self._sp(16)))
        self._label_printer_button.clicked.connect(self._choose_label_printer)
        layout.addWidget(self._label_printer_button)
        self._label_printer_calibration_button = QPushButton("Kalibrera", frame)
        self._label_printer_calibration_button.setIcon(self._standard_icon("SP_DialogApplyButton"))
        self._label_printer_calibration_button.setIconSize(QSize(self._sp(15), self._sp(15)))
        self._label_printer_calibration_button.setToolTip(
            "Skriv ut ett kalibreringsmönster på vald etikettmall utan att skapa ett intyg."
        )
        self._label_printer_calibration_button.clicked.connect(self._calibrate_label_printer)
        layout.addWidget(self._label_printer_calibration_button)
        self._refresh_label_mapping_combo(self._last_selected_name)
        self._refresh_label_printer_button()
        return frame

    def _refresh_label_printer_button(self) -> None:
        button = self._label_printer_button
        if button is None:
            return
        selected = self._label_printer_name or "Windows standard"
        compact_name = selected if len(selected) <= 20 else f"{selected[:19]}…"
        button.setText(f"Skrivare: {compact_name}")
        button.setMaximumWidth(self._sp(220))
        button.setToolTip(
            f"Vald etikettskrivare: {selected}. Klicka för att välja en annan Windows-skrivare. "
            "Skrivaren och dess drivrutin måste först vara installerade i Windows."
        )

    def _choose_label_printer(self) -> None:
        printers = installed_printers()
        system_default = default_printer()
        if system_default and system_default.casefold() not in {name.casefold() for name in printers}:
            printers.append(system_default)
            printers.sort(key=str.casefold)
        if not printers and not self._label_printer_name:
            QMessageBox.information(
                self._tab_widget,
                "Etikettskrivare",
                "Inga skrivare hittades i Windows. Installera skrivaren och dess drivrutin "
                "i Windows först och välj sedan Skrivare igen.\n\n"
                "För Brother .lbx-etiketter krävs även Brother b-PAC.",
            )
            return

        choices: list[tuple[str, str]] = [
            (
                f"Windows standardskrivare ({system_default})"
                if system_default else "Windows standardskrivare",
                "",
            )
        ]
        choices.extend((name, name) for name in printers)
        if self._label_printer_name and self._label_printer_name.casefold() not in {
            name.casefold() for name in printers
        }:
            choices.append((f"{self._label_printer_name} (inte hittad)", self._label_printer_name))
        labels = [label for label, _name in choices]
        selected_index = next(
            (index for index, (_label, name) in enumerate(choices)
             if name.casefold() == self._label_printer_name.casefold()),
            0,
        )
        selected, accepted = QInputDialog.getItem(
            self._tab_widget,
            "Välj etikettskrivare",
            "Välj en skrivare installerad i Windows. Brother .lbx kräver Brother b-PAC:",
            labels,
            selected_index,
            False,
        )
        if not accepted:
            return
        printer_name = choices[labels.index(selected)][1]
        if self.label_printer_settings_path is not None:
            try:
                save_printer_name(self.label_printer_settings_path, printer_name)
            except OSError as exc:
                QMessageBox.warning(
                    self._tab_widget,
                    "Etikettskrivare",
                    f"Skriverval kunde inte sparas: {exc}",
                )
                self._evt(
                    "label.printer_save_failed",
                    f"Skriverval kunde inte sparas: {exc}",
                    level=logging.ERROR,
                    error=str(exc),
                )
                return
        self._label_printer_name = printer_name
        self._refresh_label_printer_button()
        selected_name = printer_name or "Windows standardskrivare"
        self._evt(
            "label.printer_selected",
            f"Etikettskrivare vald: {selected_name}",
            printer=selected_name,
        )
        self._update_label_print_option()

    def _calibrate_label_printer(self) -> None:
        inspection = inspect_printer(self._label_printer_name)
        if not inspection.ready:
            QMessageBox.warning(
                self._tab_widget,
                "Etikettkalibrering",
                f"Skrivaren är inte redo.\n\n{inspection.message}\n\n"
                f"Drivrutin: {inspection.driver or 'saknas'}",
            )
            self._evt(
                "label.calibration_prerequisite_failed",
                f"Kalibrering kan inte starta: {inspection.message}",
                level=logging.WARNING,
                printer=inspection.name,
                driver=inspection.driver,
            )
            return
        if not _labels.bpac_available():
            QMessageBox.warning(
                self._tab_widget,
                "Etikettkalibrering",
                "Windows hittar skrivaren, men Brother b-PAC saknas. Installera b-PAC "
                "för att skriva ut .lbx-etiketter.",
            )
            return
        template_name, _template = self._selected_template()
        mapped_label = self._mapped_label_path(template_name)
        if mapped_label is None:
            available_labels = _labels.find_labels(_labels.label_folder(self.templates_path))
            if not available_labels:
                QMessageBox.information(
                    self._tab_widget,
                    "Etikettkalibrering",
                    "Windows kan öppna skrivaren och drivrutinen är installerad, men ingen "
                    ".lbx-etikettmall hittades i mappen Etiketter/.",
                )
                return
            if len(available_labels) == 1:
                mapped_label = available_labels[0]
            else:
                display_names = [f"{path.stem} — {path.name}" for path in available_labels]
                selected, accepted = QInputDialog.getItem(
                    self._tab_widget,
                    "Välj kalibreringsetikett",
                    "Välj vilken .lbx-mall som ska kalibreras (inget intyg skapas):",
                    display_names,
                    0,
                    False,
                )
                if not accepted:
                    return
                mapped_label = available_labels[display_names.index(selected)]
        answer = QMessageBox.question(
            self._tab_widget,
            "Skriv ut kalibreringsmönster",
            f"Skriv ut ett kalibreringsmönster med mallen {mapped_label.name}?\n\n"
            "Alla redigerbara textfält ersätts tillfälligt med en numrerad testskala. "
            "Inga formulärvärden eller intyg används; statiskt innehåll i LBX-mallen "
            "lämnas kvar. En etikett förbrukas.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_label_print(mapped_label, 1, {}, calibration=True)

    def _refresh_label_mapping_combo(self, template_name: str) -> None:
        combo = self._label_mapping_combo
        if combo is None:
            return
        labels = _labels.find_labels(_labels.label_folder(self.templates_path))
        combo.blockSignals(True)
        combo.clear()
        if not labels:
            combo.addItem("Inga etikettmallar hittades i Etiketter/", "")
            combo.setEnabled(False)
            combo.setToolTip("Lägg dina Brother .lbx-filer i mappen Etiketter/.")
        elif not template_name:
            combo.addItem("Välj en intygsmall först", "")
            combo.setEnabled(False)
            combo.setToolTip("Välj en intygsmall för att koppla dess etikett.")
        else:
            combo.addItem("Välj etikettmall…", "")
            for label in labels:
                combo.addItem(label.stem, label.name)
            mapped_name = self._label_mappings.get(template_name, "")
            selected_index = combo.findData(mapped_name) if mapped_name else -1
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.setEnabled(True)
            if selected_index >= 0:
                combo.setToolTip(
                    f"{mapped_name} skrivs ut för intygsmallen {template_name}."
                )
            else:
                combo.setToolTip(
                    f"Välj vilken .lbx-etikett som hör till intygsmallen {template_name}. "
                    "Automatisk etikettutskrift är avstängd tills en mall har valts."
                )
        combo.blockSignals(False)
        self._update_label_print_option()

    def _mapped_label_path(self, template_name: str | None) -> Path | None:
        label_name = self._label_mappings.get(str(template_name or ""), "")
        if not label_name:
            return None
        return next(
            (path for path in _labels.find_labels(_labels.label_folder(self.templates_path))
             if path.name.casefold() == label_name.casefold()),
            None,
        )

    def _persist_label_mappings(self) -> bool:
        if self.label_mappings_path is None:
            return False
        try:
            save_label_mappings(self.label_mappings_path, self._label_mappings)
            return True
        except OSError as exc:
            self._log.exception("[CERTIFICATES] Etikettkopplingar kunde inte sparas")
            self._evt("label.mapping_save_failed", f"Etikettkopplingar kunde inte sparas: {exc}",
                      level=logging.ERROR, path=str(self.label_mappings_path), error=str(exc))
            self._set_status(f"⚠️ Etikettkopplingen kunde inte sparas: {exc}")
            return False

    @Slot(int)
    def _on_label_mapping_changed(self, _index: int = -1) -> None:
        combo = self._label_mapping_combo
        template_name, template = self._selected_template()
        if combo is None or template is None:
            return
        label_name = str(combo.currentData() or "")
        previous = dict(self._label_mappings)
        if label_name:
            self._label_mappings[template_name] = label_name
        else:
            self._label_mappings.pop(template_name, None)
        if not self._persist_label_mappings():
            self._label_mappings = previous
            self._refresh_label_mapping_combo(template_name)
            return
        self._evt("label.mapping_saved", f"Etikettkoppling sparad för {template_name}",
                  template=template_name, label=label_name or "")
        if label_name:
            self._set_status(f"🏷️ {label_name} kopplad till intygsmallen {template_name}")
        else:
            self._set_status(f"Ingen etikettmall vald för {template_name}")
        self._update_label_print_option()

    @Slot(bool)
    def _on_label_print_toggled(self, checked: bool) -> None:
        """Keep automatic label printing enabled whenever it can be performed."""
        if checked:
            return
        checkbox = self._label_print_checkbox
        if checkbox is None:
            return
        template_name, _template = self._selected_template()
        if (
            not self._mapped_label_path(template_name)
            or not _labels.bpac_available()
            or not inspect_printer(self._label_printer_name).ready
        ):
            return
        checkbox.blockSignals(True)
        try:
            checkbox.setChecked(True)
        finally:
            checkbox.blockSignals(False)

    def _update_label_print_option(self) -> None:
        checkbox = self._label_print_checkbox
        if checkbox is None:
            return
        template_name, _template = self._selected_template()
        mapped_label = self._mapped_label_path(template_name)
        bpac_ready = _labels.bpac_available()
        printer = inspect_printer(self._label_printer_name)
        can_print = bool(mapped_label and bpac_ready and printer.ready)
        checkbox.setEnabled(can_print)
        checkbox.setChecked(can_print)
        readiness = self._label_readiness_label
        if readiness is not None:
            if mapped_label is None:
                status = "Etikettmall saknas"
                detail = (
                    f"Välj en .lbx-etikett för intygsmallen {template_name}."
                    if template_name else "Välj en intygsmall och koppla en .lbx-etikett."
                )
            elif not bpac_ready:
                status = "b-PAC saknas"
                detail = "Installera Brother b-PAC för automatisk .lbx-utskrift."
            elif not printer.ready:
                status = "Skrivare ej redo"
                detail = printer.message
            else:
                status = "Redo"
                detail = f"{mapped_label.name}; drivrutin: {printer.driver}. {printer.message}"
            readiness.setText(status)
            readiness.setToolTip(detail)

        if mapped_label is None:
            checkbox.setToolTip(
                f"Välj en etikettmall för intygsmallen {template_name} först."
                if template_name else "Välj en intygsmall och koppla en etikettmall först."
            )
        elif not bpac_ready:
            checkbox.setToolTip("Automatisk utskrift kräver Windows och Brother b-PAC.")
        elif not printer.ready:
            checkbox.setToolTip(f"Skrivaren är inte redo: {printer.message}")
        else:
            checkbox.setToolTip(
                f"{mapped_label.name} skrivs automatiskt ut efter att intyget har skapats."
            )

    def _refresh_customer_combo(self, selected_id: str | None = None) -> None:
        combo = self._customer_combo
        if combo is None:
            return
        if selected_id is None and combo.currentData():
            selected_id = str(combo.currentData())
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("Välj kund…", None)
        for customer in self._customers:
            combo.addItem(customer.name, customer.customer_id)
        index = combo.findData(selected_id) if selected_id else -1
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    @Slot()
    def _manage_customers(self) -> None:
        actions = ["➕ Lägg till kund"]
        actions.extend(f"✏️ Redigera #{index + 1}: {customer.name}"
                       for index, customer in enumerate(self._customers))
        actions.extend(f"🗑️ Ta bort #{index + 1}: {customer.name}"
                       for index, customer in enumerate(self._customers))
        action, accepted = QInputDialog.getItem(
            self._tab_widget, "Kundregister", "Välj åtgärd:", actions, 0, False
        )
        if not accepted:
            return
        if action == actions[0]:
            self._edit_customer_dialog()
            return
        try:
            number = int(action.split("#", 1)[1].split(":", 1)[0]) - 1
        except (IndexError, ValueError):
            return
        if action.startswith("✏️") and 0 <= number < len(self._customers):
            self._edit_customer_dialog(self._customers[number])
        elif action.startswith("🗑️") and 0 <= number < len(self._customers):
            customer = self._customers[number]
            answer = QMessageBox.question(
                self._tab_widget, "Ta bort kund",
                f"Vill du ta bort {customer.name} från kundregistret?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                previous = self._customers
                self._customers = [item for item in previous
                                   if item.customer_id != customer.customer_id]
                if self._persist_customers():
                    self._refresh_customer_combo()
                else:
                    self._customers = previous

    def _edit_customer_dialog(self, customer: Customer | None = None) -> None:
        dialog = QDialog(self._tab_widget)
        dialog.setWindowTitle("Redigera kund" if customer else "Lägg till kund")
        dialog.setMinimumWidth(self._sp(390))
        form = QFormLayout(dialog)
        fields: dict[str, QLineEdit] = {}
        for key, title, value in (
            ("name", "Kundnamn *", customer.name if customer else ""),
            ("address", "Adress", customer.address if customer else ""),
            ("postal_code", "Postnummer", customer.postal_code if customer else ""),
            ("city", "Postort", customer.city if customer else ""),
            ("phone", "Telefon", customer.phone if customer else ""),
            ("email", "E-post", customer.email if customer else ""),
        ):
            entry = QLineEdit(value, dialog)
            entry.setClearButtonEnabled(True)
            fields[key] = entry
            form.addRow(title, entry)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=dialog,
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            saved = Customer.create(
                *(fields[key].text() for key in
                  ("name", "address", "postal_code", "city", "phone", "email")),
                customer_id=customer.customer_id if customer else None,
            )
        except ValueError as exc:
            QMessageBox.warning(self._tab_widget, "Kunduppgifter", str(exc))
            return
        previous = self._customers
        if customer:
            self._customers = [saved if item.customer_id == customer.customer_id else item
                               for item in previous]
        else:
            self._customers = [*previous, saved]
        self._customers.sort(key=lambda item: item.name.casefold())
        if self._persist_customers():
            self._refresh_customer_combo(saved.customer_id)
        else:
            self._customers = previous

    def _persist_customers(self) -> bool:
        if self.customers_path is None:
            return False
        try:
            save_customers(self.customers_path, self._customers)
        except OSError as exc:
            self._log.exception("[CERTIFICATES] Kundregistret kunde inte sparas")
            self._evt("customer.save_failed", f"Kundregistret kunde inte sparas: {exc}",
                      level=logging.ERROR, path=str(self.customers_path), error=str(exc))
            QMessageBox.critical(self._tab_widget, "Kundregister", f"Kunderna kunde inte sparas:\n{exc}")
            return False
        self._evt("customer.saved", f"Kundregister sparat ({len(self._customers)} kunder)",
                  count=len(self._customers))
        return True

    def _build_template_info(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setProperty("designSurface", True)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(
            self._sp(SPACING.medium), self._sp(SPACING.small),
            self._sp(SPACING.medium), self._sp(SPACING.small),
        )
        self._info_label = QLabel("Välj en mall för att se dess information.", frame)
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label, stretch=1)
        self._counter_label = QLabel(frame)
        self._counter_label.setMinimumWidth(self._sp(180))
        self._counter_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._counter_label)
        return frame

    def _build_fields_section(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setProperty("designSurface", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(
            self._sp(SPACING.medium), self._sp(SPACING.small),
            self._sp(SPACING.medium), self._sp(SPACING.small),
        )
        layout.setSpacing(self._sp(SPACING.small))
        title = QLabel("Uppgifter", frame)
        title.setProperty("uiRole", "sectionTitle")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        self._fields_title = title
        layout.addWidget(title)
        self._fields_area = FieldsScrollArea(frame)
        self._fields_area.layout_changed.connect(self._fit_fields_area)
        layout.addWidget(self._fields_area)
        return frame

    def _build_action_row(self) -> QWidget:
        """Keep every action reachable on narrow and high-DPI windows."""
        frame = QFrame()
        frame.setProperty("designSurface", True)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(
            self._sp(SPACING.medium), self._sp(SPACING.small),
            self._sp(SPACING.medium), self._sp(SPACING.small),
        )
        layout.setSpacing(self._sp(SPACING.small))

        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setHorizontalSpacing(self._sp(SPACING.small))
        buttons.setVerticalSpacing(self._sp(SPACING.xsmall))

        self._generate_button = QPushButton("Generera PDF", frame)
        self._generate_button.setProperty("uiRole", "primary")
        self._generate_button.setIcon(self._standard_icon("SP_DialogSaveButton"))
        self._generate_button.setIconSize(QSize(self._sp(16), self._sp(16)))
        self._generate_button.setMinimumHeight(self._sp(SHAPE.control_height))
        self._generate_button.setToolTip("Generera intyg (Ctrl+G)")
        self._generate_button.clicked.connect(self._on_generate)
        buttons.addWidget(self._generate_button, 0, 0)

        self._batch_button = QPushButton("Batch", frame)
        self._batch_button.setIcon(self._standard_icon("SP_FileDialogListView"))
        self._batch_button.setIconSize(QSize(self._sp(16), self._sp(16)))
        self._batch_button.setMinimumHeight(self._sp(SHAPE.control_height))
        self._batch_button.setToolTip("Batch-generering från CSV")
        self._batch_button.clicked.connect(self._on_batch_generate)
        buttons.addWidget(self._batch_button, 0, 1)

        output = QPushButton("Output", frame)
        output.setIcon(self._standard_icon("SP_DirOpenIcon"))
        output.setIconSize(QSize(self._sp(16), self._sp(16)))
        output.setMinimumHeight(self._sp(SHAPE.control_height))
        output.setToolTip("Öppna outputmapp (Ctrl+O)")
        output.clicked.connect(self._open_output_folder)
        buttons.addWidget(output, 0, 2)

        clear = QPushButton("Rensa", frame)
        clear.setIcon(self._standard_icon("SP_TrashIcon"))
        clear.setIconSize(QSize(self._sp(16), self._sp(16)))
        clear.setMinimumHeight(self._sp(SHAPE.control_height))
        clear.setToolTip("Rensa fält (Ctrl+L)")
        clear.clicked.connect(self._clear_fields)
        buttons.addWidget(clear, 0, 3)

        imported = QPushButton("Import", frame)
        imported.setIcon(self._standard_icon("SP_DialogOpenButton"))
        imported.setIconSize(QSize(self._sp(16), self._sp(16)))
        imported.setMinimumHeight(self._sp(SHAPE.control_height))
        imported.setToolTip("Importera befintliga PDF-intyg (Ctrl+I)")
        imported.clicked.connect(self._import_certificates)
        buttons.addWidget(imported, 0, 4)

        for column in range(5):
            buttons.setColumnStretch(column, 1)
        for button in (self._generate_button, self._batch_button, output, clear, imported):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addLayout(buttons)

        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(self._sp(6))
        self._progress_bar = QProgressBar(frame)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setFixedWidth(self._sp(170))
        self._progress_bar.setVisible(False)
        progress_layout.addWidget(self._progress_bar)
        self._progress_label = QLabel(frame)
        self._progress_label.setWordWrap(True)
        self._progress_label.setMinimumWidth(0)
        progress_layout.addWidget(self._progress_label, stretch=1)
        layout.addLayout(progress_layout)
        self._set_action_buttons_enabled(False)
        return frame

    def _build_shortcuts(self, parent: QWidget) -> None:
        for sequence, callback in (
            ("Ctrl+G", self._on_generate),
            ("Ctrl+L", self._clear_fields),
            ("Ctrl+O", self._open_output_folder),
            ("Ctrl+I", self._import_certificates),
            ("F5", self.refresh_templates),
        ):
            shortcut = QShortcut(QKeySequence(sequence), parent)
            shortcut.activated.connect(callback)

    # ── UI-uppdateringar ──────────────────────────────────────────────────

    def _set_status(self, text: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(text)

    def _update_template_combo(self) -> None:
        combo = self._template_combo
        if combo is None:
            self._pending_combo_update = True
            return
        with self._templates_lock:
            names = sorted(self._templates, key=str.casefold)
        current = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            if not names:
                combo.addItem("Inga mallar hittade")
                combo.setEnabled(False)
            else:
                combo.addItems(names)
                combo.setEnabled(True)
                combo.setCurrentText(current if current in names else names[0])
        finally:
            combo.blockSignals(False)
        self._pending_combo_update = False
        gallery_button = getattr(self, "_template_gallery_button", None)
        if gallery_button is not None:
            gallery_button.setEnabled(bool(names))
        self._evt("cert.combo_updated", f"Mallcombo uppdaterad: {len(names)} mallar",
                  level=logging.DEBUG, count=len(names))
        if names:
            self._select_template(combo.currentText(), force=True)
        else:
            self._last_selected_name = ""
            self._refresh_label_mapping_combo("")
            self._clear_template_view()
            self._set_action_buttons_enabled(False)

    def _update_converter_status(self) -> None:
        if self._converter_label is None:
            return
        with self._state_lock:
            active = self._converter_active
            available = self._converter_available
            converter = self._pdf_converter
        if active:
            self._converter_label.setText("⏳ Kontrollerar PDF…")
        elif available:
            name = str(_safe_get(converter, "method_name", "PDF-konverterare"))
            self._converter_label.setText(f"✅ {name}")
        else:
            self._converter_label.setText("❌ Ingen PDF-konverterare")
        self._restore_action_buttons()

    def _update_total_label(self) -> None:
        if self._total_label is None:
            return
        total = 0
        getter = _safe_get(self._counter_manager, "get_total", None)
        if callable(getter):
            try:
                total = int(getter())
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte läsa total räknare")
        self._total_label.setText(f"📊 Totalt: {total} intyg")

    def _update_counter_label(self, template: Any) -> None:
        if self._counter_label is None:
            return
        current_getter = _safe_get(self._counter_manager, "get_current", None)
        next_getter = _safe_get(self._counter_manager, "get_next", None)
        if not callable(current_getter) or not callable(next_getter):
            self._counter_label.clear()
            return
        name = str(_safe_get(template, "name", ""))
        config = _safe_get(template, "config", None)
        start = _safe_get(config, "counter_start", 1)
        try:
            current = int(current_getter(name))
            next_number = int(next_getter(name, start=max(1, int(start))))
        except Exception:
            self._log.exception("[CERTIFICATES] Kunde inte läsa mallräknare")
            self._counter_label.clear()
            return
        self._counter_label.setText(f"Genererade: {current}  │  Nästa: #{next_number}")

    def _set_action_buttons_enabled(self, enabled: bool) -> None:
        if self._generate_button is not None:
            self._generate_button.setEnabled(enabled)
        if self._batch_button is not None:
            self._batch_button.setEnabled(enabled)

    def _restore_action_buttons(self) -> None:
        name = self._template_combo.currentText() if self._template_combo is not None else ""
        self._set_action_buttons_enabled(
            bool(name and name != "Inga mallar hittade" and self._converter_available)
        )

    def _show_progress(self, visible: bool, message: str = "") -> None:
        if self._progress_bar is not None:
            self._progress_bar.setVisible(visible)
            if not visible:
                self._progress_bar.setValue(0)
        if self._progress_label is not None:
            self._progress_label.setText(message if visible else "")

    # ── Mallval, fält och preview ─────────────────────────────────────────

    @Slot(str)
    def _on_template_selected(self, name: str) -> None:
        self._select_template(name)

    def _select_template(self, name: str, *, force: bool = False) -> None:
        if not name or name == "Inga mallar hittade":
            return
        if not force and name == self._last_selected_name:
            return
        with self._templates_lock:
            template = self._templates.get(name)
        if template is None:
            return
        self._last_selected_name = name
        self._refresh_label_mapping_combo(name)
        self._evt("cert.template_selected", f"Mall vald: {name}", template=name)
        self._update_template_information(template)
        self._update_counter_label(template)
        self._create_input_fields(template)
        self._restore_action_buttons()
        self._emit_event("certificate.template_selected", {"template": name})

    def _update_template_information(self, template: Any) -> None:
        if self._info_label is None:
            return
        docx_path = Path(str(_safe_get(template, "docx_file", "")))
        config = _safe_get(template, "config", None)
        category = str(_safe_get(config, "category", "") or "")
        parts = [f"📄 {docx_path.name or 'okänd DOCX'}"]
        attachments = _template_attachments(template)
        if attachments:
            parts.append(f"📎 {attachments} bilagor")
        parts.append(f"✏️ {len(_template_user_fields(template))} fält")
        if category:
            parts.append(f"🏷️ {category}")
        self._info_label.setText("  │  ".join(parts))

    def _create_input_fields(self, template: Any) -> None:
        area = self._fields_area
        if area is None:
            return
        area.clear_widgets()
        self._field_widgets.clear()
        placeholders = [str(value) for value in _safe_get(template, "placeholders", []) or []]
        self._current_placeholders = list(dict.fromkeys(placeholders))
        fields = _template_user_fields(template)
        automatic_count = sum(1 for field_name in placeholders if _is_auto_field(field_name))
        if self._fields_title is not None:
            title = f"Uppgifter · {len(fields)} fält"
            if automatic_count:
                title += f" · {automatic_count} automatiska"
            self._fields_title.setText(title)

        if not fields:
            label = QLabel("✅ Inga manuella fält. Alla placeholders fylls automatiskt.", area.content)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            area.set_content(notices=[label])
            self._evt("cert.fields_built", "Inga manuella fält — allt automatiskt",
                      level=logging.DEBUG, placeholders=len(placeholders))
            self._fit_fields_area()
            return

        grouped_fields: dict[str, list[FieldWidget]] = {}
        for field_name in fields:
            upper = field_name.upper()
            kind = "vin" if upper in {"VIN", "VINNUMMER", "CHASSI", "CHASSINUMMER"} else "reg" if upper in {"REG", "REGNR", "REGISTRERINGSNUMMER"} else ""
            validator: Callable[[str], Any] | None = None
            tooltip = ""
            if kind == "vin":
                validator = _VINValidator.validate
                tooltip = "VIN: 17 tecken utan I, O eller Q."
            elif kind == "reg":
                validator = _RegValidator.validate
                tooltip = "Registreringsnummer, exempelvis ABC123 eller ABC12D."
            field = FieldWidget(
                field_name,
                _field_label(field_name),
                default_value=_template_default(template, field_name),
                validator=validator,
                field_kind=kind,
                tooltip=tooltip,
                compact=True,
                parent=area.content,
            )
            field.value_changed.connect(self._on_field_value_changed)
            self._field_widgets.append(field)
            section = field_group(field_name, _field_label(field_name))
            grouped_fields.setdefault(section, []).append(field)
        ordered_groups = [
            (section, grouped_fields[section])
            for section in FIELD_GROUP_ORDER
            if section in grouped_fields
        ]
        area.set_content(field_groups=ordered_groups)
        self._evt("cert.fields_built", f"{len(self._field_widgets)} fält byggda",
                  level=logging.DEBUG, fields=len(self._field_widgets),
                  auto=automatic_count, placeholders=len(placeholders))
        if self._field_widgets:
            self._field_widgets[0].focus_entry()
        self._fit_fields_area()

    def _fit_fields_area(self) -> None:
        """10.2.0: gör fältytan så hög att ALLA fält ryms utan scroll.

        Fälten ligger i en QScrollArea. Den kompakta grid-layoutens höjd styr
        minsta yta, begränsad till 62 % av flikens höjd; små fönster kan scrolla.
        Först vid mycket många fält på liten skärm behövs fortfarande scroll.
        """
        area = self._fields_area
        if area is None:
            return
        try:
            content_h = int(area.content.sizeHint().height())
        except Exception:
            content_h = 0
        if content_h <= 0:
            return
        try:
            host_height = int(self._tab_widget.height()) if self._tab_widget is not None else 0
        except Exception:
            host_height = 0
        if host_height <= 0:
            host_height = max(int(area.height()), 360)
        cap = max(120, int(host_height * 0.62))
        with suppress(Exception):
            area.setMinimumHeight(int(min(content_h + 8, cap)))
            area.updateGeometry()

    def _clear_template_view(self) -> None:
        if self._fields_title is not None:
            self._fields_title.setText("Uppgifter")
        if self._info_label is not None:
            self._info_label.setText("Ingen giltig mall är vald.")
        if self._counter_label is not None:
            self._counter_label.clear()
        if self._fields_area is not None:
            self._fields_area.clear_widgets()
        self._field_widgets.clear()
        self._current_placeholders.clear()

    @Slot(str, str)
    def _on_field_value_changed(self, field_name: str, value: str) -> None:
        self._evt(
            "cert.field_changed",
            f"Fält ändrat: {field_name}",
            level=logging.DEBUG,
            field=field_name,
            value_present=bool(value),
        )
        self._emit_event(
            "certificate.placeholder_updated",
            {"field": field_name, "value_present": bool(value)},
        )

    def _collect_values(self) -> dict[str, str]:
        return {field.field_name: field.get_value() for field in self._field_widgets}

    def _clear_after_generate_enabled(self) -> bool:
        """10.3.0: config `certificates.clear_after_generate` (standard True)."""
        manager = None
        if self._context is not None:
            manager = (
                getattr(self._context, "config_manager", None)
                or getattr(self._context, "settings", None)
                or getattr(self._context, "config", None)
            )
        getter = getattr(manager, "get", None)
        if not callable(getter):
            return True
        try:
            return bool(getter("certificates.clear_after_generate", True))
        except Exception:
            self._log.exception("[CERTIFICATES] Kunde inte läsa clear_after_generate")
            return True

    @Slot()
    def _clear_fields(self) -> None:
        self._evt("cert.fields_cleared", "Fält rensade", level=logging.DEBUG,
                  fields=len(self._field_widgets))
        for field in self._field_widgets:
            field.clear()
        if self._field_widgets:
            self._field_widgets[0].focus_entry()

    # ── Generering ────────────────────────────────────────────────────────

    def _certificate_history_records(self) -> list[Any]:
        """Use live statistics history where possible, then its persisted JSON."""
        if self._context is not None:
            getter = getattr(self._context, "get_plugin", None)
            if callable(getter):
                for plugin_name in ("Statistik", "Statistics"):
                    try:
                        statistics = getter(plugin_name)
                        engine = getattr(statistics, "_engine", None) if statistics else None
                        records = getattr(engine, "records", None) if engine else None
                        if isinstance(records, list) and records:
                            return records
                    except Exception:
                        self._log.debug("Kunde inte läsa statistikpluginets minne", exc_info=True)

        paths: list[Path] = []
        if self.config_path is not None:
            paths.append(self.config_path / "statistics.json")
            paths.append(self.config_path.parent / "statistics.json")
        for history_path in paths:
            try:
                if not history_path.is_file():
                    continue
                payload = json.loads(history_path.read_text(encoding="utf-8"))
                records = payload.get("records", []) if isinstance(payload, dict) else payload
                if isinstance(records, list):
                    return [record for record in records if isinstance(record, dict)]
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._log.warning("[CERTIFICATES] Intygshistorik kunde inte läsas: %s", exc)
        return []

    @staticmethod
    def _history_value(record: Any, key: str, default: Any = "") -> Any:
        if isinstance(record, dict):
            return record.get(key, default)
        return _result_value(record, key, default)

    def _confirm_no_duplicate_registration(
        self, template_name: str, values: dict[str, str]
    ) -> bool:
        registration = registration_from_values(values)
        vin = vin_from_values(values)
        if not (registration or vin):
            return True
        duplicates = find_duplicate_certificates(
            self._certificate_history_records(), template_name, registration, vin
        )
        if not duplicates:
            return True

        lines: list[str] = []
        for record in duplicates[:5]:
            number = self._history_value(
                record, "number", self._history_value(record, "id", "?")
            )
            created = self._history_value(
                record, "created", self._history_value(record, "created_at", "")
            )
            filename = str(self._history_value(record, "filename", "") or "")
            detail = f"• Intyg #{number}"
            if created:
                detail += f" — {created}"
            if filename:
                detail += f" — {filename}"
            lines.append(detail)
        if len(duplicates) > len(lines):
            lines.append(f"• och {len(duplicates) - len(lines)} ytterligare träff(ar)")
        answer = QMessageBox.question(
            self._tab_widget,
            "Intyg finns redan",
            f"Det finns redan {len(duplicates)} intyg i historiken med samma "
            f"registreringsnummer och/eller VIN/chassinummer för mallen {template_name}.\n\n"
            + "\n".join(lines)
            + "\n\nVill du skapa ett till intyg ändå?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self._evt(
            "cert.duplicate_vehicle",
            f"Dubblettvarning visad för mall {template_name}",
            template=template_name,
            matches=len(duplicates),
            identifiers_checked=sum(bool(value) for value in (registration, vin)),
            continued=answer == QMessageBox.StandardButton.Yes,
        )
        return answer == QMessageBox.StandardButton.Yes

    @Slot()
    def _on_generate(self) -> None:
        name, template = self._selected_template()
        if template is None:
            QMessageBox.warning(self._tab_widget, "Välj mall", "Välj en giltig mall först.")
            self._evt("cert.generate_blocked", "Generering blockerad: ingen mall vald",
                      level=logging.WARNING)
            return
        values = self._collect_values()
        blockers, missing = self._generation_preflight(template, values)
        if blockers:
            checklist = ["Kontroll före generering:"]
            checklist.extend(f"• {item}" for item in blockers)
            if missing:
                checklist.extend(["", "Tomma fält (valfritt att fylla i):"])
                checklist.extend(f"• {item}" for item in missing)
            QMessageBox.warning(
                self._tab_widget,
                "Kontroll före generering",
                "\n".join(checklist),
            )
            self._evt(
                "cert.preflight_blocked",
                "Generering blockerad av förkontrollen",
                level=logging.WARNING,
                template=name,
                blockers=len(blockers),
                missing=len(missing),
            )
            return
        if missing:
            answer = QMessageBox.question(
                self._tab_widget,
                "Kontroll före generering",
                "Följande fält är tomma:\n\n• "
                + "\n• ".join(missing)
                + "\n\nDu kan fylla i dem eller fortsätta ändå. Fortsätta nu?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._evt(
                    "cert.generate_cancelled",
                    "Generering avbruten efter förkontroll",
                    level=logging.DEBUG,
                    template=name,
                    missing=len(missing),
                )
                return
        if not self._confirm_no_duplicate_registration(name, values):
            self._evt("cert.generate_cancelled", "Generering avbruten efter dubblettvarning",
                      level=logging.INFO, template=name)
            return

        self._set_action_buttons_enabled(False)
        self._show_progress(True, "⏳ Förbereder generering…")
        self._evt("cert.generate_started", f"Generering startad: {name}",
                  template=name, fields=len(values), recovery_key="certificate.generation")
        self._emit_event("certificate.before_generate", {"template": name, "values": values})
        self._last_values = dict(values)  # tillgängliga som etikettfält
        self._pending_template_name = name
        self._pending_label_print = bool(
            self._label_print_checkbox and self._label_print_checkbox.isChecked()
        )
        generator = self._generator
        worker = _GenerationWorker(generator, template, values)
        worker.signals.progress.connect(self._on_generation_progress, Qt.ConnectionType.QueuedConnection)
        worker.signals.finished.connect(self._on_generation_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(self._on_generation_error, Qt.ConnectionType.QueuedConnection)
        self._start_worker(worker)

    def _selected_template(self) -> tuple[str, Any | None]:
        # 10.2.1: platshållare ("Skannar mallar…", "Inga mallar hittade") är
        # aldrig en mall → tomt namn, så att inga mappar skapas med statustext.
        name = self._template_combo.currentText() if self._template_combo is not None else ""
        with self._templates_lock:
            template = self._templates.get(name)
        if template is None:
            return "", None
        return name, template

    def _generation_preflight(
        self, template: Any, values: dict[str, str]
    ) -> tuple[list[str], list[str]]:
        """Collect blocking issues and optional blanks before showing one checklist."""
        blockers: list[str] = []
        missing = [
            _field_label(field.field_name)
            for field in self._field_widgets
            if not field.get_value().strip()
        ]

        template_valid = _safe_get(template, "is_valid", None)
        docx_file = _safe_get(template, "docx_file", None)
        if template_valid is False:
            blockers.append("Den valda mallfilen är trasig eller saknas.")
        elif docx_file is not None:
            try:
                path = Path(docx_file)
                if path.suffix.casefold() != ".docx" or not path.is_file():
                    blockers.append("Mallfilen kan inte hittas eller är inte en DOCX-fil.")
            except (TypeError, ValueError, OSError):
                blockers.append("Mallfilens sökväg är ogiltig.")

        if self._generator is None:
            blockers.append("Intygsgeneratorn är inte tillgänglig.")
        if not self._converter_available:
            blockers.append("PDF-konverteraren saknas (kontrollera LibreOffice eller docx2pdf).")
        blockers.extend(self._validate_before_generation(template, values))
        return blockers, missing

    def _validate_before_generation(self, template: Any, values: dict[str, str]) -> list[str]:
        config = _safe_get(template, "config", None)
        validate_vin = bool(_safe_get(config, "validate_vin", True))
        validate_reg = bool(_safe_get(config, "validate_reg", False))
        checks: tuple[tuple[set[str], Callable[[str], Any], str, bool], ...] = (
            ({"VIN", "VINNUMMER", "CHASSI", "CHASSINUMMER"}, _VINValidator.validate, "Ogiltigt VIN", validate_vin),
            ({"REG", "REGNR", "REGISTRERINGSNUMMER"}, _RegValidator.validate, "Ogiltigt registreringsnummer", validate_reg),
        )
        issues: list[str] = []
        for names, validator, title, enabled in checks:
            if not enabled:
                continue
            value = next(
                (
                    candidate
                    for key, candidate in values.items()
                    if "".join(character for character in key.upper() if character.isalnum()) in names
                    and candidate.strip()
                ),
                "",
            )
            if not value:
                continue
            try:
                result = validator(value)
                if bool(_safe_get(result, "is_error", False)):
                    message = str(_safe_get(result, "message", title) or title)
                    issues.append(f"{title}: {message}")
                    self._evt(
                        "cert.validation_failed",
                        f"Förkontrollen misslyckades: {title}",
                        level=logging.WARNING,
                        title=title,
                    )
            except Exception:
                self._log.exception("[CERTIFICATES] Validering misslyckades")
                issues.append(f"{title}: värdet kunde inte valideras.")
        return issues

    @Slot(str, int)
    def _on_generation_progress(self, message: str, percent: int) -> None:
        if self._progress_bar is not None:
            self._progress_bar.setValue(max(0, min(100, percent)))
        if self._progress_label is not None:
            self._progress_label.setText(f"⏳ {message}")

    @Slot(object)
    def _on_generation_finished(self, result: object) -> None:
        self._show_progress(False)
        self._restore_action_buttons()
        if not _result_is_success(result):
            self._pending_label_print = False
            self._pending_template_name = ""
            error = str(_result_value(result, "error", "Okänt fel"))
            QMessageBox.critical(self._tab_widget, "Genereringsfel", error)
            self._log.error(
                "[CERTIFICATES] Genereringsfel: %s", error,
                extra={"recovery_key": "certificate.generation"},
            )
            self._evt("cert.generate_failed", f"Genereringsfel: {error}",
                      level=logging.ERROR, error=error,
                      recovery_key="certificate.generation")
            self._emit_event("certificate.generation_failed", {"error": error})
            return

        name = str(_result_value(result, "template_name", self._last_selected_name))
        number = _result_value(result, "number", "?")
        output_path = _result_value(result, "output_path", None)
        filename = str(_result_value(result, "filename", ""))
        pages = _result_value(result, "page_count", 0)
        duration = float(_result_value(result, "duration", 0.0) or 0.0)
        now = datetime.now().isoformat(timespec="seconds")
        self._log.info(
            "[CERTIFICATES] Intyg #%s skapat (%s, %s sidor, %.1f s)",
            number, filename, pages, duration,
        )
        self._evt("cert.generated", f"Intyg #{number} skapat på {duration:.1f} s",
                  template=name, number=str(number), filename=filename,
                  pages=int(pages or 0), duration=round(duration, 2),
                  recovery_key="certificate.generation")
        self._update_total_label()
        _, template = self._selected_template()
        if template is not None:
            self._update_counter_label(template)
        if self._clear_after_generate_enabled():
            self._clear_fields()  # 10.3.0: rensa automatiskt efter lyckat intyg

        self._emit_event(
            "certificate.generated",
            {
                "template": name,
                "number": number,
                "path": str(output_path or ""),
                "filename": filename,
                "pages": pages,
                "duration": duration,
                "timestamp": now,
            },
        )
        self._emit_event(
            "statistics.certificate_generated",
            {"template": name, "number": number, "timestamp": now},
        )
        self._emit_event("statistics.updated", {"source": "certificates", "timestamp": now})
        self._emit_event(
            "tray.status_update",
            {"title": "Intyg skapat", "message": f"#{number} – {name}"},
        )

        template_name = self._pending_template_name or name
        mapped_label = self._mapped_label_path(template_name)
        automatic_label_print = bool(self._pending_label_print and mapped_label)
        self._pending_label_print = False
        self._pending_template_name = ""
        box = QMessageBox(self._tab_widget)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Intyg skapat")
        message = (
            f"✅ Intyg #{number} skapades.\n\n📄 {filename}\n📑 {pages} sidor\n⏱️ {duration:.1f}s"
        )
        if automatic_label_print and mapped_label is not None:
            message += f"\n\n🏷️ Etiketten {mapped_label.name} skrivs ut."
        box.setText(message)
        open_button = box.addButton("📄 Öppna PDF", QMessageBox.ButtonRole.AcceptRole)
        label_button = None
        if not automatic_label_print and _labels.find_labels(_labels.label_folder(self.templates_path)):
            label_button = box.addButton("🏷️ Skriv etikett", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Stäng", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_button and output_path:
            self._open_path(Path(str(output_path)))
        if automatic_label_print and mapped_label is not None:
            self._start_label_print(mapped_label, 1, dict(self._last_values))
        elif label_button is not None and clicked is label_button:
            self._print_label_dialog(template_name=template_name)

    def offer_bpac_setup_at_startup(self, parent: QWidget | None = None) -> None:
        """Offer an approved, signature-verified b-PAC install at application startup."""
        if self._bpac_setup_prompted:
            return
        self._bpac_setup_prompted = True
        if sys.platform != "win32":
            return
        label_folder = _labels.label_folder(self.templates_path)
        if not _labels.find_labels(label_folder):
            return

        available = _labels.bpac_available()
        installed_version = _bpac_setup.installed_bpac_version() if available else None
        version_comparison = (
            _bpac_setup.compare_versions(installed_version, _bpac_setup.BPAC_TARGET_VERSION)
            if installed_version
            else None
        )
        if available and version_comparison is not None and version_comparison >= 0:
            return

        parent = parent or self._tab_widget
        bits = _bpac_setup.application_bitness()
        missing = not available
        prompt = QMessageBox(parent)
        prompt.setIcon(
            QMessageBox.Icon.Information if missing else QMessageBox.Icon.Warning
        )
        prompt.setWindowTitle(
            "Brother b-PAC saknas" if missing else "Brother b-PAC-uppdatering behövs"
        )
        if missing:
            prompt.setText("Etikettmallar finns, men b-PAC Client Component är inte installerad.")
            reason = "Installera b-PAC för att aktivera etikettutskrift."
        elif installed_version:
            prompt.setText(
                f"Installerad b-PAC-version är {installed_version}; "
                f"rekommenderad version är {_bpac_setup.BPAC_TARGET_VERSION}."
            )
            reason = "En uppdatering rekommenderas för att etikettutskriften ska fungera korrekt."
        else:
            prompt.setText("b-PAC finns, men den installerade versionen kunde inte läsas.")
            reason = "Du kan kontrollera Brothers sida och installera den senaste signerade versionen."
        prompt.setInformativeText(
            f"{reason}\n\nAppen behöver Brother b-PAC {bits}-bitars för .lbx-etiketter. "
            "Vill du hämta och installera/uppdatera Brother-komponenten nu?"
        )
        install_button = prompt.addButton(
            "Hämta och installera…", QMessageBox.ButtonRole.AcceptRole
        )
        prompt.addButton("Inte nu", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(install_button)
        prompt.exec()
        if prompt.clickedButton() is not install_button:
            self._evt("label.bpac_setup_deferred", "b-PAC-installation sköts upp",
                      level=logging.INFO, bitness=bits)
            return

        if not QDesktopServices.openUrl(QUrl(_bpac_setup.BPAC_DOWNLOAD_URL)):
            QMessageBox.warning(
                parent,
                "Brother-sidan kunde inte öppnas",
                "Öppna Brothers officiella b-PAC-sida manuellt och ladda ner "
                "Client Component i rätt bitness.",
            )
            return
        info = QMessageBox(parent)
        info.setWindowTitle("Hämta b-PAC från Brother")
        info.setText("Godkänn Brothers licens och hämta b-PAC Client Component.")
        info.setInformativeText(
            f"Välj {bits}-bitars Client Component (inte utvecklar-SDK:t). "
            "När MSI-filen har hämtats väljer du den här. Appen kontrollerar "
            "Brother-signaturen och MSI-arkitekturen innan Windows-installationen startas."
        )
        info.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        if info.exec() != QMessageBox.StandardButton.Ok:
            return

        installer_path, _selected_filter = QFileDialog.getOpenFileName(
            parent,
            "Välj hämtad Brother b-PAC Client Component",
            str(Path.home() / "Downloads"),
            "Windows Installer (*.msi)",
        )
        if not installer_path:
            return
        verified, verification_message = _bpac_setup.verify_bpac_msi(installer_path)
        if not verified:
            self._evt("label.bpac_installer_rejected", "b-PAC-installationsfilen avvisades",
                      level=logging.WARNING, reason=verification_message)
            QMessageBox.warning(parent, "Installationsfilen avvisades", verification_message)
            return

        confirm = QMessageBox(parent)
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setWindowTitle("Bekräfta b-PAC-installation")
        confirm.setText("Brother-signaturen och appens bitness är verifierade.")
        confirm.setInformativeText(
            f"{verification_message}\n\nWindows kommer att fråga om "
            "administratörsgodkännande innan installationen startar."
        )
        start_button = confirm.addButton(
            "Starta installation", QMessageBox.ButtonRole.AcceptRole
        )
        confirm.addButton("Avbryt", QMessageBox.ButtonRole.RejectRole)
        confirm.setDefaultButton(start_button)
        confirm.exec()
        if confirm.clickedButton() is not start_button:
            return

        app_root = (
            self._context.base_path
            if self._context is not None
            else Path(__file__).resolve().parents[2]
        )
        launched, message = _bpac_setup.launch_bpac_msi_as_admin(
            installer_path,
            app_root=app_root,
            parent_pid=int(QApplication.applicationPid()),
        )
        if not launched:
            self._evt("label.bpac_installer_start_failed", "b-PAC-installationen startade inte",
                      level=logging.WARNING, reason=message)
            QMessageBox.warning(parent, "Installationen startade inte", message)
            return
        self._evt(
            "label.bpac_installer_started",
            "Brother b-PAC Client Component startades med UAC-godkännande",
            level=logging.INFO,
            installer=Path(installer_path).name,
            bitness=bits,
        )
        QMessageBox.information(
            parent,
            "b-PAC-installationen startad",
            "IntygsDrive stängs nu så att b-PAC kan uppdateras. Appen startas "
            "automatiskt igen när Windows-installationen är klar. Om du avbryter "
            "UAC startar appen ändå igen utan att b-PAC installeras.",
        )
        self._emit_event("app.quit", {"reason": "bpac_installation"})

    # ── Etikettutskrift (QL-500 via b-PAC) ────────────────────────────────

    def _print_label_dialog(
        self,
        template_name: str | None = None,
    ) -> None:
        """Use the selected template-to-label mapping, or choose a label once."""
        folder = _labels.label_folder(self.templates_path)
        labels = _labels.find_labels(folder)
        if not labels:
            QMessageBox.information(
                self._tab_widget, "Etikett",
                f"Inga etikettmallar (.lbx) hittades i:\n{folder}",
            )
            self._evt("label.none", "Inga .lbx-etiketter hittades", level=logging.WARNING,
                      path=str(folder))
            return
        if not _labels.bpac_available():
            QMessageBox.warning(
                self._tab_widget, "Brother b-PAC saknas",
                "För att skriva ut på QL-500 behövs Brother \"b-PAC Client Component\" "
                "(64-bitars, samma som Python).\n\nHämta den gratis här:\n"
                f"{_labels.BPAC_DOWNLOAD_URL}\n\nStarta om appen efter installationen.",
            )
            self._evt("label.bpac_missing", "b-PAC saknas — installation erbjuden",
                      level=logging.WARNING, url=_labels.BPAC_DOWNLOAD_URL)
            return

        selected_template = template_name or self._last_selected_name
        chosen = self._mapped_label_path(selected_template)
        if chosen is None:
            if len(labels) > 1:
                display_names = [f"{path.stem} — {path.name}" for path in labels]
                selected, ok = QInputDialog.getItem(
                    self._tab_widget,
                    "Etikett",
                    f"Välj etikett för intygsmallen {selected_template or '(okänd)'}:",
                    display_names,
                    0,
                    False,
                )
                if not ok:
                    self._evt("label.cancelled", "Etikettutskrift avbruten", level=logging.DEBUG)
                    return
                chosen = labels[display_names.index(selected)]
            else:
                chosen = labels[0]

        copies, ok = QInputDialog.getInt(
            self._tab_widget, "Etikett", f"Antal etiketter ({chosen.stem}):", 1, 1, 99,
        )
        if not ok:
            self._evt("label.cancelled", "Etikettutskrift avbruten", level=logging.DEBUG)
            return
        self._start_label_print(chosen, copies, dict(self._last_values))

    def _start_label_print(
        self,
        chosen: Path,
        copies: int,
        values: dict[str, str],
        *,
        calibration: bool = False,
    ) -> None:
        printer_name = self._label_printer_name
        printer_label = printer_name or "Windows standardskrivare"
        event = "label.calibration_started" if calibration else "label.print_started"
        message = (
            f"Kalibrerar {chosen.name} på {printer_label}"
            if calibration else f"Skriver ut {copies} × {chosen.name} på {printer_label}"
        )
        self._evt(
            event,
            message,
            label=chosen.name,
            copies=copies,
            printer=printer_label,
            recovery_key="label.calibration" if calibration else "label.print",
        )
        self._set_status(
            f"🧭 Skriver ut kalibreringsmönster på {printer_label}…"
            if calibration else f"🏷️ Skriver ut {copies} etikett(er) på {printer_label}…"
        )
        worker = _LabelWorker(chosen, copies, values, printer_name, calibration=calibration)
        worker.signals.finished.connect(
            lambda result, is_calibration=calibration: self._on_label_finished(
                result, is_calibration
            ),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.error.connect(
            lambda error, is_calibration=calibration: self._on_label_finished(
                _labels.LabelResult(False, chosen.name, copies, error=error),
                is_calibration,
            ),
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker(worker)

    @Slot(object)
    def _on_label_finished(self, result: object, calibration: bool = False) -> None:
        if not isinstance(result, _labels.LabelResult):
            return
        if result.ok:
            if calibration:
                self._set_status(f"✅ Kalibreringsmönster utskrivet: {result.label}")
                self._evt(
                    "label.calibration_printed",
                    f"Kalibreringsmönster utskrivet: {result.label}",
                    label=result.label,
                    printer=self._label_printer_name or "Windows standardskrivare",
                    fields_filled=len(result.filled),
                    duration=round(result.duration, 2),
                    recovery_key="label.calibration",
                )
                QMessageBox.information(
                    self._tab_widget,
                    "Kalibrering utskriven",
                    "Kontrollera att sifferföljden är tydlig och inte avklippt, och att "
                    "texten hamnar där den förväntas på etiketten. Vid förskjutning eller "
                    "klippning justerar du etikettformat, marginaler eller skalning i "
                    "Brother-drivrutinen/P-touch Editor och skriver sedan ut mönstret igen.\n\n"
                    "Kalibreringen använder tillfälliga testvärden; intyg och mallfil ändras inte.",
                )
            else:
                self._set_status(f"🏷️ {result.copies} etikett(er) utskrivna: {result.label}")
                self._evt(
                    "label.printed",
                    f"Etikett utskriven: {result.copies} × {result.label}",
                    label=result.label,
                    copies=result.copies,
                    filled=result.filled,
                    duration=round(result.duration, 2),
                    recovery_key="label.print",
                )
            return
        self._set_status(f"❌ Etikettutskrift misslyckades: {result.label}")
        event = "label.calibration_failed" if calibration else "label.print_failed"
        self._evt(event, f"Etikettutskrift misslyckades: {result.error}",
                  level=logging.ERROR, label=result.label, error=result.error,
                  recovery_key="label.calibration" if calibration else "label.print")
        QMessageBox.warning(self._tab_widget, "Etikett", result.error)

    @Slot(str)
    def _on_generation_error(self, error: str) -> None:
        self._pending_label_print = False
        self._pending_template_name = ""
        self._show_progress(False)
        self._restore_action_buttons()
        QMessageBox.critical(self._tab_widget, "Oväntat genereringsfel", error)
        self._log.error(
            "[CERTIFICATES] Oväntat genereringsfel: %s", error,
            extra={"recovery_key": "certificate.generation"},
        )
        self._evt("cert.generate_error", f"Oväntat genereringsfel: {error}",
                  level=logging.ERROR, error=error,
                  recovery_key="certificate.generation")
        self._emit_event("certificate.generation_failed", {"error": error})

    # ── Batch ─────────────────────────────────────────────────────────────

    @Slot()
    def _on_batch_generate(self) -> None:
        name, template = self._selected_template()
        if template is None:
            QMessageBox.warning(self._tab_widget, "Välj mall", "Välj en giltig mall först.")
            return
        csv_path, _ = QFileDialog.getOpenFileName(
            self._tab_widget,
            "Välj CSV-fil för batch",
            "",
            "CSV-filer (*.csv);;Alla filer (*.*)",
        )
        if csv_path:
            self._run_batch_from_csv(Path(csv_path), name, template)

    def _handle_batch_drop(self, path_text: str) -> None:
        name, template = self._selected_template()
        if template is None:
            QMessageBox.warning(self._tab_widget, "Välj mall", "Välj en mall före CSV-drop.")
            return
        self._evt("cert.csv_dropped", f"CSV släppt: {Path(path_text).name}",
                  level=logging.DEBUG, path=path_text)
        self._run_batch_from_csv(Path(path_text), name, template)

    def _run_batch_from_csv(self, csv_path: Path, name: str, template: Any) -> None:
        generator = self._generator
        if generator is None or not self._converter_available:
            QMessageBox.warning(self._tab_widget, "PDF-konverterare", "PDF-konverterare saknas.")
            return
        try:
            rows = self._read_csv_rows(csv_path)
        except Exception as exc:
            QMessageBox.critical(self._tab_widget, "CSV-fel", f"Kunde inte läsa CSV:\n\n{exc}")
            self._evt("cert.csv_read_failed", f"CSV-läsning misslyckades: {exc}",
                      level=logging.ERROR, path=str(csv_path), error=str(exc))
            return
        self._evt("cert.csv_read", f"CSV läst: {len(rows)} rader",
                  level=logging.DEBUG, path=str(csv_path), rows=len(rows))
        if not rows:
            QMessageBox.warning(self._tab_widget, "Tom CSV", "CSV-filen saknar användbara datarader.")
            return
        answer = QMessageBox.question(
            self._tab_widget,
            "Batch-generering",
            f"Generera {len(rows)} intyg med '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self._evt("cert.batch_cancelled", "Batch avbruten av användaren",
                      level=logging.DEBUG, rows=len(rows))
            return

        dialog = ProgressDialog(
            self._tab_widget,
            "Batch-generering",
            f"Genererar {len(rows)} intyg med '{name}'…",
        )
        self._set_action_buttons_enabled(False)
        self._evt("cert.batch_started", f"Batch startad: {len(rows)} rader med '{name}'",
                  template=name, rows=len(rows), recovery_key="certificates.batch")
        self._emit_event("certificates.batch.started", {"template": name, "count": len(rows)})
        worker = _BatchWorker(generator, template, rows)
        worker.signals.progress.connect(
            lambda message, percent: dialog.update_progress(percent, message),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.finished.connect(
            lambda result: self._on_batch_finished(result, dialog),
            Qt.ConnectionType.QueuedConnection,
        )
        worker.signals.error.connect(
            lambda error: self._on_batch_error(error, dialog),
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker(worker)
        dialog.exec()

    @staticmethod
    def _read_csv_rows(path: Path) -> list[dict[str, str]]:
        if not path.is_file():
            raise FileNotFoundError(path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            delimiter = ";" if sample.count(";") >= sample.count(",") else ","
            reader = csv.DictReader(handle, delimiter=delimiter)
            rows: list[dict[str, str]] = []
            for row in reader:
                cleaned = {
                    str(key).strip(): str(value).strip() if value is not None else ""
                    for key, value in (row or {}).items()
                    if key and str(key).strip()
                }
                if any(cleaned.values()):
                    rows.append(cleaned)
            return rows

    def _on_batch_finished(self, result: Any, dialog: ProgressDialog) -> None:
        dialog.accept()
        self._restore_action_buttons()
        succeeded = int(_result_value(result, "succeeded", 0) or 0)
        failed = int(_result_value(result, "failed", 0) or 0)
        total = int(_result_value(result, "total", succeeded + failed) or 0)
        duration = float(_result_value(result, "duration", 0.0) or 0.0)
        self._log.info(
            "[CERTIFICATES] Batch klar: %d/%d OK, %d fel, %.1f s",
            succeeded, total, failed, duration,
        )
        if failed:
            self._evt(
                "cert.batch_partial_failure",
                f"Batch klar med fel: {failed}/{total} misslyckades",
                level=logging.ERROR,
                succeeded=succeeded,
                failed=failed,
                total=total,
                recovery_key="certificates.batch",
            )
        self._evt("cert.batch_done", f"Batch klar: {succeeded}/{total} på {duration:.1f} s",
                  succeeded=succeeded, failed=failed, total=total,
                  duration=round(duration, 2),
                  recovery_key="certificates.batch" if failed == 0 else None)
        self._update_total_label()
        _, template = self._selected_template()
        if template is not None:
            self._update_counter_label(template)
        QMessageBox.information(
            self._tab_widget,
            "Batch klar",
            f"✅ {succeeded}/{total} skapade\n❌ {failed} misslyckades\n⏱️ {duration:.1f}s",
        )
        now = datetime.now().isoformat(timespec="seconds")
        self._emit_event(
            "certificates.batch.completed",
            {"total": total, "succeeded": succeeded, "failed": failed, "duration": duration},
        )
        self._emit_event("statistics.updated", {"source": "certificates_batch", "timestamp": now})
        self._emit_event(
            "tray.status_update",
            {"title": "Batch klar", "message": f"{succeeded}/{total} intyg skapade"},
        )

    def _on_batch_error(self, error: str, dialog: ProgressDialog) -> None:
        dialog.reject()
        self._restore_action_buttons()
        QMessageBox.critical(self._tab_widget, "Batch-fel", error)
        self._log.error(
            "[CERTIFICATES] Batch-fel: %s", error,
            extra={"recovery_key": "certificates.batch"},
        )
        self._evt("cert.batch_error", f"Batch-fel: {error}",
                  level=logging.ERROR, error=error,
                  recovery_key="certificates.batch")
        self._emit_event("certificates.batch.failed", {"error": error})

    # ── Import och filöppning ─────────────────────────────────────────────

    @Slot()
    def _import_certificates(self) -> None:
        name, _template = self._selected_template()
        if not name or name == "Inga mallar hittade" or self.output_path is None:
            QMessageBox.warning(self._tab_widget, "Välj mall", "Välj en giltig mall först.")
            return
        target = self.output_path / name
        files, _ = QFileDialog.getOpenFileNames(
            self._tab_widget,
            "Välj PDF-intyg att importera",
            str(target if target.exists() else self.output_path),
            "PDF-filer (*.pdf);;Alla filer (*.*)",
        )
        if not files:
            return
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self._tab_widget, "Importfel", str(exc))
            return

        imported = 0
        skipped = 0
        for file_name in files:
            source = Path(file_name)
            if not source.is_file() or source.suffix.casefold() != ".pdf":
                skipped += 1
                continue
            destination = self._unique_destination(target, source.name)
            try:
                if source.resolve() != destination.resolve():
                    shutil.copy2(source, destination)
                imported += 1
                increment = _safe_get(self._counter_manager, "increment", None)
                if callable(increment):
                    increment(name)
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte importera %s", source)
                skipped += 1
        self._save_counter()
        self._update_total_label()
        _, template = self._selected_template()
        if template is not None:
            self._update_counter_label(template)
        self._evt("cert.imported", f"Import klar: {imported} intyg, {skipped} hoppade",
                  template=name, imported=imported, skipped=skipped)
        QMessageBox.information(
            self._tab_widget,
            "Import klar",
            f"✅ Importerade {imported} intyg till '{name}'" + (f"\n⚠️ {skipped} hoppades över" if skipped else ""),
        )
        self._emit_event(
            "certificates.imported",
            {"template": name, "count": imported, "skipped": skipped},
        )

    @staticmethod
    def _unique_destination(folder: Path, filename: str) -> Path:
        source_name = Path(filename)
        destination = folder / source_name.name
        counter = 1
        while destination.exists():
            destination = folder / f"{source_name.stem}_{counter}{source_name.suffix}"
            counter += 1
        return destination

    @Slot()
    def _open_templates_folder(self) -> None:
        if self.templates_path is not None:
            self._open_folder(self.templates_path)

    @Slot()
    def _open_output_folder(self) -> None:
        if self.output_path is None:
            return
        name, _ = self._selected_template()
        self._open_folder(self.output_path / name if name and name != "Inga mallar hittade" else self.output_path)

    def _open_folder(self, folder: Path) -> None:
        self._evt("cert.open_folder", f"Öppnar mapp: {folder}", level=logging.DEBUG,
                  path=str(folder))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if not _PlatformHelper.open_folder(folder):
                raise RuntimeError("Operativsystemet kunde inte öppna mappen")
        except Exception as exc:
            self._log.exception("[CERTIFICATES] Kunde inte öppna mapp %s", folder)
            self._evt("cert.open_folder_failed", f"Kunde inte öppna mapp: {exc}",
                      level=logging.ERROR, path=str(folder), error=str(exc))
            QMessageBox.warning(self._tab_widget, "Kunde inte öppna mapp", str(exc))

    def _open_path(self, path: Path) -> None:
        self._evt("cert.open_file", f"Öppnar fil: {path.name}", level=logging.DEBUG,
                  path=str(path))
        try:
            if not _PlatformHelper.open_file(path):
                raise RuntimeError("Operativsystemet kunde inte öppna filen")
        except Exception as exc:
            self._log.exception("[CERTIFICATES] Kunde inte öppna fil %s", path)
            self._evt("cert.open_file_failed", f"Kunde inte öppna fil: {exc}",
                      level=logging.ERROR, path=str(path), error=str(exc))
            QMessageBox.warning(self._tab_widget, "Kunde inte öppna fil", str(exc))

    def _save_counter(self) -> None:
        saver = _safe_get(self._counter_manager, "save", None)
        if callable(saver):
            try:
                saver()
            except Exception:
                self._log.exception("[CERTIFICATES] Kunde inte spara räknare")

    # ── Publikt API ───────────────────────────────────────────────────────

    @property
    def templates(self) -> dict[str, Any]:
        with self._templates_lock:
            return dict(self._templates)

    def get_template(self, name: str) -> Any | None:
        with self._templates_lock:
            return self._templates.get(name)

    def get_statistics(self) -> dict[str, Any]:
        templates = self.templates
        current = _safe_get(self._counter_manager, "get_current", None)
        next_number = _safe_get(self._counter_manager, "get_next", None)
        total = _safe_get(self._counter_manager, "get_total", None)
        data: dict[str, Any] = {
            "total_certificates": int(total()) if callable(total) else 0,
            "template_count": len(templates),
            "templates_available": len(templates),
            "template_names": sorted(templates, key=str.casefold),
            "converter_available": bool(self._converter_available),
            "converter": str(_safe_get(self._pdf_converter, "method_name", "Ingen")),
            "engine_available": ENGINE_AVAILABLE,
            "templates": {},
        }
        for name, template in templates.items():
            config = _safe_get(template, "config", None)
            start = _safe_get(config, "counter_start", 1)
            try:
                start_number = max(1, int(start))
            except (TypeError, ValueError):
                start_number = 1
            data["templates"][name] = {
                "count": int(current(name)) if callable(current) else 0,
                "next": int(next_number(name, start=start_number)) if callable(next_number) else start_number,
                "valid": _template_is_valid(template),
                "fields": len(_template_user_fields(template)),
                "attachments": _template_attachments(template),
            }
        return data

    def diagnose(self) -> dict[str, Any]:
        """10.1.0: komplett tillstånd för AI-felsökning (en anropbar översikt)."""
        with self._state_lock:
            scan_state = {
                "token": self._scan_token,
                "active": self._scan_active,
                "requested": self._scan_requested,
            }
            converter_state = {
                "token": self._converter_token,
                "active": self._converter_active,
                "available": self._converter_available,
                "method": str(_safe_get(self._pdf_converter, "method_name", "Ingen")),
            }
        last = self._last_scan
        return {
            "version": self.version,
            "engine_available": ENGINE_AVAILABLE,
            "engine_error": ENGINE_ERROR,
            "loaded": self._loaded,
            "paths": {
                "templates": str(self.templates_path or ""),
                "output": str(self.output_path or ""),
                "config": str(self.config_path or ""),
            },
            "scan": scan_state,
            "last_scan": None if last is None else {
                "count": len(last.templates),
                "names": sorted(last.templates, key=str.casefold),
                "skipped": list(last.skipped),
                "duration_ms": last.duration_ms,
            },
            "converter": converter_state,
            "statistics": self.get_statistics(),
        }

    def generate_certificate(
        self,
        template_name: str,
        values: dict[str, str],
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> Any | None:
        template = self.get_template(template_name)
        generator = self._generator
        if template is None or generator is None or not self._converter_available:
            self._evt("cert.api_generate_blocked", "API-generering blockerad",
                      level=logging.WARNING, template=template_name)
            return None
        try:
            result = generator.generate(template, values, progress_callback=progress_callback)
            self._evt("cert.api_generated", f"API-generering klar: {template_name}",
                      template=template_name, success=_result_is_success(result))
            return result
        except Exception:
            self._log.exception("[CERTIFICATES] API-generering misslyckades")
            self._evt("cert.api_generate_failed", f"API-generering misslyckades: {template_name}",
                      level=logging.ERROR, template=template_name)
            return None

    def generate_batch(
        self,
        template_name: str,
        batch_values: list[dict[str, str]],
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> Any | None:
        template = self.get_template(template_name)
        generator = self._generator
        if template is None or generator is None or not self._converter_available:
            return None
        try:
            return generator.generate_batch(template, batch_values, progress_callback=progress_callback)
        except Exception:
            self._log.exception("[CERTIFICATES] API-batch misslyckades")
            return None
