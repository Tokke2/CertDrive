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

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QInputDialog,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from . import labels as _labels
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

    def __init__(self, label_path: Path, copies: int, values: dict[str, str]) -> None:
        super().__init__()
        self._label_path = label_path
        self._copies = copies
        self._values = values
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(_labels.print_label(self._label_path, self._copies, self._values))
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
        self._fields_area: FieldsScrollArea | None = None
        self._field_widgets: list[FieldWidget] = []
        self._last_values: dict[str, str] = {}
        self._current_placeholders: list[str] = []
        self._generate_button: QPushButton | None = None
        self._batch_button: QPushButton | None = None
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
                    return max(base, int(round(base * height / 16.0)))
        except Exception:
            pass
        return base

    def _evt(self, event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
        """Strukturerad loggpost (cert.*) — får aldrig krascha appen."""
        try:
            self._log.log(level, msg, extra={"event": event, "ctx": ctx or None})
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
        self.templates_path = self._diagnose_templates_path(base, self.templates_path)

        for path in (self.templates_path, self.output_path, self.config_path):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self._log.error("[CERTIFICATES] Kunde inte skapa %s: %s", path, exc)
                self._evt("cert.path_failed", f"Kunde inte skapa {path}: {exc}",
                          level=logging.ERROR, path=str(path), error=str(exc))

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
        outer.setContentsMargins(self._sp(10), self._sp(10), self._sp(10), self._sp(10))
        outer.setSpacing(self._sp(8))
        outer.addWidget(self._build_status_row())
        outer.addWidget(self._build_template_selector())
        outer.addWidget(self._build_template_info())
        outer.addWidget(self._build_fields_section(), stretch=1)
        outer.addWidget(self._build_action_row())

        self._build_shortcuts(root)
        return root

    def _build_status_row(self) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(self._sp(6), self._sp(3), self._sp(6), self._sp(3))
        layout.setSpacing(self._sp(8))
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
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(self._sp(10), self._sp(8), self._sp(10), self._sp(8))
        layout.setSpacing(self._sp(7))
        layout.addWidget(QLabel("📁 Mall:", frame))

        self._template_combo = QComboBox(frame)
        self._template_combo.setMinimumWidth(self._sp(280))
        self._template_combo.setMinimumHeight(self._sp(34))
        self._template_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._template_combo.addItem("Skannar mallar…")
        self._template_combo.setEnabled(False)
        self._template_combo.currentTextChanged.connect(self._on_template_selected)
        layout.addWidget(self._template_combo, stretch=1)

        refresh = QPushButton("🔄", frame)
        refresh.setFixedSize(self._sp(34), self._sp(34))
        refresh.setToolTip("Skanna mallar igen (F5)")
        refresh.clicked.connect(self.refresh_templates)
        layout.addWidget(refresh)

        open_templates = QPushButton("📂", frame)
        open_templates.setFixedSize(self._sp(34), self._sp(34))
        open_templates.setToolTip("Öppna mallmappen")
        open_templates.clicked.connect(self._open_templates_folder)
        layout.addWidget(open_templates)
        return frame

    def _build_template_info(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(self._sp(10), self._sp(6), self._sp(10), self._sp(6))
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
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(self._sp(6), self._sp(6), self._sp(6), self._sp(6))
        layout.setSpacing(self._sp(4))
        title = QLabel("✏️ Uppgifter", frame)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        self._fields_area = FieldsScrollArea(frame)
        layout.addWidget(self._fields_area)
        return frame

    def _build_action_row(self) -> QWidget:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, self._sp(4), 0, 0)
        layout.setSpacing(self._sp(6))

        self._generate_button = QPushButton("🚀 Generera PDF", frame)
        self._generate_button.setMinimumHeight(self._sp(36))
        self._generate_button.setToolTip("Generera intyg (Ctrl+G)")
        self._generate_button.clicked.connect(self._on_generate)
        layout.addWidget(self._generate_button)

        self._batch_button = QPushButton("📦 Batch", frame)
        self._batch_button.setMinimumHeight(self._sp(36))
        self._batch_button.setToolTip("Batch-generering från CSV")
        self._batch_button.clicked.connect(self._on_batch_generate)
        layout.addWidget(self._batch_button)

        output = QPushButton("📂 Output", frame)
        output.setMinimumHeight(self._sp(36))
        output.setToolTip("Öppna outputmapp (Ctrl+O)")
        output.clicked.connect(self._open_output_folder)
        layout.addWidget(output)

        clear = QPushButton("🗑️ Rensa", frame)
        clear.setMinimumHeight(self._sp(36))
        clear.setToolTip("Rensa fält (Ctrl+L)")
        clear.clicked.connect(self._clear_fields)
        layout.addWidget(clear)

        imported = QPushButton("📥 Import", frame)
        imported.setMinimumHeight(self._sp(36))
        imported.setToolTip("Importera befintliga PDF-intyg (Ctrl+I)")
        imported.clicked.connect(self._import_certificates)
        layout.addWidget(imported)

        layout.addSpacerItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        self._progress_bar = QProgressBar(frame)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setFixedWidth(self._sp(170))
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)
        self._progress_label = QLabel(frame)
        self._progress_label.setMinimumWidth(self._sp(150))
        layout.addWidget(self._progress_label)
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
        self._evt("cert.combo_updated", f"Mallcombo uppdaterad: {len(names)} mallar",
                  level=logging.DEBUG, count=len(names))
        if names:
            self._select_template(combo.currentText(), force=True)
        else:
            self._last_selected_name = ""
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

        if not fields:
            label = QLabel("✅ Inga manuella fält. Alla placeholders fylls automatiskt.", area.content)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            area.content_layout.addWidget(label)
            area.add_stretch()
            self._evt("cert.fields_built", "Inga manuella fält — allt automatiskt",
                      level=logging.DEBUG, placeholders=len(placeholders))
            self._fit_fields_area()
            return

        automatic_count = sum(1 for field_name in placeholders if _is_auto_field(field_name))
        if automatic_count:
            notice = QLabel(
                f"ℹ️ {automatic_count} fält fylls i automatiskt (nummer, datum och tid).",
                area.content,
            )
            notice.setWordWrap(True)
            area.content_layout.addWidget(notice)

        for field_name in fields:
            upper = field_name.upper()
            kind = "vin" if upper in {"VIN", "CHASSI", "CHASSINUMMER"} else "reg" if upper in {"REG", "REGNR", "REGISTRERINGSNUMMER"} else ""
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
                parent=area.content,
            )
            field.value_changed.connect(self._on_field_value_changed)
            area.content_layout.addWidget(field)
            self._field_widgets.append(field)
        area.add_stretch()
        area.scroll_to_top()
        self._evt("cert.fields_built", f"{len(self._field_widgets)} fält byggda",
                  level=logging.DEBUG, fields=len(self._field_widgets),
                  auto=automatic_count, placeholders=len(placeholders))
        if self._field_widgets:
            self._field_widgets[0].focus_entry()
        self._fit_fields_area()

    def _fit_fields_area(self) -> None:
        """10.2.0: gör fältytan så hög att ALLA fält ryms utan scroll.

        Fälten ligger i en QScrollArea; genom att sätta minimihöjden till
        innehållets höjd (tak: 55 % av fönstret) ryms samtliga fält direkt.
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
            cap = max(220, int(self.height() * 0.55))
        except Exception:
            cap = 520
        with suppress(Exception):
            area.setMinimumHeight(int(min(content_h + 12, cap)))
            area.updateGeometry()

    def _clear_template_view(self) -> None:
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
        self._evt("cert.field_changed", f"Fält ändrat: {field_name}",
                  level=logging.DEBUG, field=field_name, value=str(value)[:60])
        self._emit_event(
            "certificate.placeholder_updated",
            {"field": field_name, "value": value},
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

    @Slot()
    def _on_generate(self) -> None:
        name, template = self._selected_template()
        if template is None:
            QMessageBox.warning(self._tab_widget, "Välj mall", "Välj en giltig mall först.")
            self._evt("cert.generate_blocked", "Generering blockerad: ingen mall vald",
                      level=logging.WARNING)
            return
        generator = self._generator
        if generator is None or not self._converter_available:
            QMessageBox.warning(
                self._tab_widget,
                "PDF-konverterare",
                "PDF-konverteraren är inte tillgänglig ännu. Kontrollera LibreOffice eller docx2pdf.",
            )
            self._evt("cert.generate_blocked", "Generering blockerad: konverterare saknas",
                      level=logging.WARNING, template=name)
            return
        values = self._collect_values()
        if not self._validate_before_generation(template, values):
            return
        missing = [_field_label(field.field_name) for field in self._field_widgets if not field.get_value()]
        if missing:
            answer = QMessageBox.question(
                self._tab_widget,
                "Tomma fält",
                "Följande fält är tomma:\n\n• " + "\n• ".join(missing) + "\n\nFortsätta ändå?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._evt("cert.generate_cancelled", "Generering avbruten (tomma fält)",
                          level=logging.DEBUG, template=name, missing=len(missing))
                return

        self._set_action_buttons_enabled(False)
        self._show_progress(True, "⏳ Förbereder generering…")
        self._evt("cert.generate_started", f"Generering startad: {name}",
                  template=name, fields=len(values))
        self._emit_event("certificate.before_generate", {"template": name, "values": values})
        self._last_values = dict(values)  # 10.4.0: till etikettfält
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

    def _validate_before_generation(self, template: Any, values: dict[str, str]) -> bool:
        config = _safe_get(template, "config", None)
        validate_vin = bool(_safe_get(config, "validate_vin", True))
        validate_reg = bool(_safe_get(config, "validate_reg", False))
        checks: tuple[tuple[set[str], Callable[[str], Any], str, bool], ...] = (
            ({"VIN", "CHASSI", "CHASSINUMMER"}, _VINValidator.validate, "Ogiltigt VIN", validate_vin),
            ({"REG", "REGNR", "REGISTRERINGSNUMMER"}, _RegValidator.validate, "Ogiltigt registreringsnummer", validate_reg),
        )
        for names, validator, title, enabled in checks:
            if not enabled:
                continue
            value = next((value for key, value in values.items() if key.upper() in names and value.strip()), "")
            if not value:
                continue
            try:
                result = validator(value)
                if bool(_safe_get(result, "is_error", False)):
                    QMessageBox.critical(self._tab_widget, title, str(_safe_get(result, "message", title)))
                    self._evt("cert.validation_failed", f"Validering misslyckades: {title}",
                              level=logging.WARNING, title=title, value=str(value)[:20])
                    return False
            except Exception:
                self._log.exception("[CERTIFICATES] Validering misslyckades")
                return False
        return True

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
            error = str(_result_value(result, "error", "Okänt fel"))
            QMessageBox.critical(self._tab_widget, "Genereringsfel", error)
            self._log.error("[CERTIFICATES] Genereringsfel: %s", error)
            self._evt("cert.generate_failed", f"Genereringsfel: {error}",
                      level=logging.ERROR, error=error)
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
                  pages=int(pages or 0), duration=round(duration, 2))
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

        # 10.4.0: rutan erbjuder även postetikett (QL-500) om Etiketter/ har .lbx
        box = QMessageBox(self._tab_widget)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Intyg skapat")
        box.setText(
            f"✅ Intyg #{number} skapades.\n\n📄 {filename}\n📑 {pages} sidor\n⏱️ {duration:.1f}s"
        )
        open_button = box.addButton("📄 Öppna PDF", QMessageBox.ButtonRole.AcceptRole)
        label_button = None
        if _labels.find_labels(_labels.label_folder(self.templates_path)):
            label_button = box.addButton("🏷️ Skriv postetikett", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Stäng", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is open_button and output_path:
            self._open_path(Path(str(output_path)))
        elif label_button is not None and clicked is label_button:
            self._print_label_dialog()

    # ── Postetiketter (QL-500 via b-PAC) ─────────────────────────────────

    def _print_label_dialog(self) -> None:
        """Välj .lbx-etikett + antal och skriv ut i bakgrunden (10.4.0)."""
        folder = _labels.label_folder(self.templates_path)
        labels = _labels.find_labels(folder)
        if not labels:
            QMessageBox.information(
                self._tab_widget, "Postetikett",
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
        chosen = labels[0]
        if len(labels) > 1:
            names = [path.stem for path in labels]
            name, ok = QInputDialog.getItem(
                self._tab_widget, "Postetikett", "Välj etikett:", names, 0, False,
            )
            if not ok:
                self._evt("label.cancelled", "Etikettutskrift avbruten", level=logging.DEBUG)
                return
            chosen = labels[names.index(name)]
        copies, ok = QInputDialog.getInt(
            self._tab_widget, "Postetikett", f"Antal etiketter ({chosen.stem}):", 1, 1, 99,
        )
        if not ok:
            self._evt("label.cancelled", "Etikettutskrift avbruten", level=logging.DEBUG)
            return
        self._evt("label.print_started", f"Skriver ut {copies} × {chosen.name}",
                  label=chosen.name, copies=copies)
        self._set_status(f"🏷️ Skriver ut {copies} etikett(er)…")
        worker = _LabelWorker(chosen, copies, dict(self._last_values))
        worker.signals.finished.connect(self._on_label_finished, Qt.ConnectionType.QueuedConnection)
        worker.signals.error.connect(
            lambda error: self._on_label_finished(_labels.LabelResult(False, chosen.name, copies, error=error)),
            Qt.ConnectionType.QueuedConnection,
        )
        self._start_worker(worker)

    @Slot(object)
    def _on_label_finished(self, result: object) -> None:
        if not isinstance(result, _labels.LabelResult):
            return
        if result.ok:
            self._set_status(f"🏷️ {result.copies} etikett(er) utskrivna: {result.label}")
            self._evt("label.printed", f"Etikett utskriven: {result.copies} × {result.label}",
                      label=result.label, copies=result.copies, filled=result.filled,
                      duration=round(result.duration, 2))
            return
        self._set_status(f"❌ Etikettutskrift misslyckades: {result.label}")
        self._evt("label.print_failed", f"Etikettutskrift misslyckades: {result.error}",
                  level=logging.ERROR, label=result.label, error=result.error)
        QMessageBox.warning(self._tab_widget, "Postetikett", result.error)

    @Slot(str)
    def _on_generation_error(self, error: str) -> None:
        self._show_progress(False)
        self._restore_action_buttons()
        QMessageBox.critical(self._tab_widget, "Oväntat genereringsfel", error)
        self._log.error("[CERTIFICATES] Oväntat genereringsfel: %s", error)
        self._evt("cert.generate_error", f"Oväntat genereringsfel: {error}",
                  level=logging.ERROR, error=error)
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
                  template=name, rows=len(rows))
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
        self._evt("cert.batch_done", f"Batch klar: {succeeded}/{total} på {duration:.1f} s",
                  succeeded=succeeded, failed=failed, total=total,
                  duration=round(duration, 2))
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
        self._log.error("[CERTIFICATES] Batch-fel: %s", error)
        self._evt("cert.batch_error", f"Batch-fel: {error}", level=logging.ERROR, error=error)
        self._emit_event("certificate.generation_failed", {"error": error})

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
