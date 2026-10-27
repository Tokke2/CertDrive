# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Fil: plugins/template_designer/plugin.py
Sökväg: Intygsgenerator/plugins/template_designer/plugin.py
Projekt: Intygsgenerator
Version: 4.3.1 (uppgraderad från 4.3.0, 2026-09-21)
Ansvar: Mall-designer plugin — PySide6 UI för att skapa, redigera och hantera
        intygsmallar.
Beroenden: PySide6, .core (mjukt), core/__init__.py
© Rickard Längkvist 2025

Uppgradering 4.2.1 → 4.3.0 ("188888888% bättre", endast denna fil)
=================================================================
1. ADAPTIV UI: alla fasta pixelmått skalas med fontmetrik via `_sp()`
   (DPI/teckenstorlek, §17/§30); radbrytning där text kan klippas.
2. MINNE + SNABBHET:
   • SearchableListbox byter bara listinnehåll vid faktisk ändring
     (inga onödiga QListWidgetItem-allokeringar per scan/refresh).
   • Combopåfyllning i _refresh_list_ui sker endast vid ändrad uppsättning
     OCH under blockSignals — ingen rekursiv signalstorm (fix från 4.2.2).
   • Skanning sammanfogas: högst en pågående scan; nya önskemål ger exakt
     en uppföljning (fix från 4.2.2) — ingen dubbelskanning.
   • ScrollableText renderar om endast vid ändrad text (minne/CPU).
3. LOGGNING: td.*-events på alla operationer (scan med ms, mallval, skapa/
   ta bort/byt namn/duplicera, fält-spar, bilagor, import/export, öppna
   fil/mapp, retry) — synligt i centralloggens JSONL för AI-felsökning.
4. All övrig funktionalitet oförändrad (MUST-005).
"""

import logging
import os
import subprocess
import threading
import time
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    Qt,
    QObject,
    QRunnable,
    QThreadPool,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ══════════════════════════════════════════════════════════════════════════════
# MODUL-METADATA
# ══════════════════════════════════════════════════════════════════════════════

__version__: str = "4.3.1"
__all__: list[str] = [
    "Plugin",
    "OSPlatform",
    "UIConstants",
    "UI",
    "SearchableListbox",
    "DesignerStatusBar",
    "ProgressDialog",
    "ScrollableText",
]

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)


def _sp(base: int) -> int:
    """Skala pixelmått efter fontmetrik (DPI/teckenstorlek) — §17/§30.

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


# ══════════════════════════════════════════════════════════════════════════════
# PLATTFORM
# ══════════════════════════════════════════════════════════════════════════════

class OSPlatform:
    """Plattformsdetektering och fil-öppning."""

    import platform as _platform

    SYSTEM: str = _platform.system()
    IS_WINDOWS: bool = SYSTEM == "Windows"
    IS_MACOS: bool = SYSTEM == "Darwin"
    IS_LINUX: bool = SYSTEM == "Linux"

    @classmethod
    def get_modifier_symbol(cls) -> str:
        return "⌘" if cls.IS_MACOS else "Ctrl"

    @classmethod
    def open_path(cls, path: Path) -> None:
        with suppress(Exception):
            if cls.IS_WINDOWS:
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif cls.IS_MACOS:
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)

# ══════════════════════════════════════════════════════════════════════════════
# UI-KONSTANTER (COLORS-001: dessa är semantiska logg-/status-färger)
# ══════════════════════════════════════════════════════════════════════════════

class UIConstants:
    """UI-konstanter för mall-designer."""

    LEFT_PANEL_WIDTH: int = 280
    PAD_SMALL: int = 4
    PAD_MEDIUM: int = 8
    PAD_LARGE: int = 16
    COLOR_SUCCESS: str = "#107C10"
    COLOR_WARNING: str = "#FFB900"
    COLOR_ERROR: str = "#D13438"
    COLOR_INFO: str = "#0078D4"
    COLOR_MUTED: str = "#666666"

UI = UIConstants()

# ══════════════════════════════════════════════════════════════════════════════
# MJUK IMPORT FRÅN CORE
# ══════════════════════════════════════════════════════════════════════════════

_CORE_OK: bool = False
TemplateManager = None
TemplateConfig = None
TemplateInfo = None
PlaceholderExtractor = None
FieldType = None
ValidationLevel = None
RESERVED_NAMES: frozenset[str] = frozenset()
DEFAULT_CATEGORIES: list[str] = [
    "25kW",
    "35kW",
    "Passagerare",
    "Demontering",
    "Övriga",
]
AUTO_FIELDS: frozenset[str] = frozenset({"DATUM", "NUMMER", "DATE", "NUMBER"})
PLACEHOLDER_PATTERNS: list[Any] = []

try:
    from .core import (
        AUTO_FIELDS,
        DEFAULT_CATEGORIES,
        FieldType,
        PlaceholderExtractor,
        PLACEHOLDER_PATTERNS,
        RESERVED_NAMES,
        TemplateConfig,
        TemplateInfo,
        TemplateManager,
        ValidationLevel,
    )
    _CORE_OK = True
except Exception:
    pass

# ══════════════════════════════════════════════════════════════════════════════
# TRÅDSÄKRA HJÄLPKLASSER
# ══════════════════════════════════════════════════════════════════════════════

class _WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(str)


class _ScanWorker(QRunnable):
    """Bakgrundsskanning av mallar."""

    def __init__(self, manager: Any, token: int) -> None:
        super().__init__()
        self.manager = manager
        self.token = token
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.manager.scan_templates()
            templates = self.manager.get_all_templates()
            self.signals.finished.emit((self.token, templates))
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _ExportWorker(QRunnable):
    """Bakgrundsexport av mall."""

    def __init__(self, manager: Any, name: str, dest: Path) -> None:
        super().__init__()
        self.manager = manager
        self.name = name
        self.dest = dest
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            ok = self.manager.export_template(self.name, self.dest)
            self.signals.finished.emit(ok)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _ImportWorker(QRunnable):
    """Bakgrundsimport av mall."""

    def __init__(self, manager: Any, path: Path) -> None:
        super().__init__()
        self.manager = manager
        self.path = path
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            info = self.manager.import_template(self.path)
            self.signals.finished.emit(info)
        except Exception as exc:
            self.signals.error.emit(str(exc))

# ══════════════════════════════════════════════════════════════════════════════
# ÅTERANVÄNDBARA UI-KOMPONENTER
# ══════════════════════════════════════════════════════════════════════════════

class SearchableListbox(QWidget):
    """Listbox med sökfält (PySide6).

    4.3.0: listan byggs om endast när innehållet eller sökningen faktiskt
    ändrats — sparar allokeringar vid upprepade scan/refresh.
    """

    item_selected = Signal(str)
    item_activated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[tuple[str, str]] = []
        self._last_query: str | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(UI.PAD_SMALL)

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("🔍 Sök mall...")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search)
        clear_btn = QPushButton("✕")
        clear_btn.setFixedWidth(_sp(28))
        clear_btn.clicked.connect(self._search.clear)
        search_row.addWidget(clear_btn)
        layout.addLayout(search_row)

        self._list = QListWidget()
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._list.currentTextChanged.connect(self._on_select)
        self._list.itemActivated.connect(self._on_activate)
        layout.addWidget(self._list)

    def _filter(self, query: str) -> None:
        self._last_query = query
        query = query.lower().strip()
        self._list.clear()
        for display, key in self._items:
            if not query or query in display.lower() or query in key.lower():
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, key)
                self._list.addItem(item)

    def _on_select(self, _text: str) -> None:
        key = self._get_current_key()
        if key:
            self.item_selected.emit(key)

    def _on_activate(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self.item_activated.emit(key)

    def _get_current_key(self) -> str | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        return None

    def set_items(self, items: list[tuple[str, str]]) -> None:
        new_items = list(items)
        query = self._search.text()
        if new_items == self._items and query == self._last_query:
            return  # 4.3.0: oförändrat innehåll → ingen ombyggning
        self._items = new_items
        self._filter(query)

    def get_selected_key(self) -> str | None:
        return self._get_current_key()

    def get_selected_keys(self) -> list[str]:
        return [
            item.data(Qt.ItemDataRole.UserRole)
            for item in self._list.selectedItems()
            if item.data(Qt.ItemDataRole.UserRole)
        ]

    def select_by_key(self, key: str) -> bool:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == key:
                self._list.setCurrentItem(item)
                self._list.scrollToItem(item)
                return True
        return False

    def focus_search(self) -> None:
        self._search.setFocus()
        self._search.selectAll()


class DesignerStatusBar(QWidget):
    """Statusrad med meddelanden och statistik."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_sp(4), _sp(2), _sp(4), _sp(2))
        self._label = QLabel("Redo")
        self._label.setWordWrap(True)
        self._stats = QLabel("")
        self._stats.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._label, 1)
        layout.addStretch()
        layout.addWidget(self._stats)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reset)

    def _set(self, text: str, color: str, duration: int) -> None:
        self._timer.stop()
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color};" if color else "")
        if duration > 0:
            self._timer.start(duration)

    def _reset(self) -> None:
        self._label.setText("Redo")
        self._label.setStyleSheet("")

    def set_message(self, msg: str, duration: int = 0) -> None:
        self._set(msg, "", duration)

    def set_success(self, msg: str, duration: int = 3000) -> None:
        self._set(f"✅ {msg}", UI.COLOR_SUCCESS, duration)

    def set_error(self, msg: str, duration: int = 5000) -> None:
        self._set(f"❌ {msg}", UI.COLOR_ERROR, duration)

    def set_warning(self, msg: str, duration: int = 4000) -> None:
        self._set(f"⚠️ {msg}", UI.COLOR_WARNING, duration)

    def set_info(self, msg: str, duration: int = 3000) -> None:
        self._set(f"ℹ️ {msg}", UI.COLOR_INFO, duration)

    def set_stats(self, stats: str) -> None:
        self._stats.setText(stats)


class ScrollableText(QScrollArea):
    """Skrivskyddat eller redigerbart textfält med scroll.

    4.3.0: set_text hoppar över identisk text (ingen onödig reparse).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        readonly: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self._last_text: str | None = None
        self._edit = QPlainTextEdit()
        self._edit.setReadOnly(readonly)
        font = QFont(
            "Consolas" if OSPlatform.IS_WINDOWS else "Monospace", 10
        )
        self._edit.setFont(font)
        self.setWidget(self._edit)

    def set_text(self, text: str) -> None:
        if text == self._last_text:
            return
        self._last_text = text
        self._edit.setPlainText(text)

    def get_text(self) -> str:
        return self._edit.toPlainText()

    def clear(self) -> None:
        self._last_text = None
        self._edit.clear()


# Alias för bakåtkompatibilitet med __init__.py
ProgressDialog = QProgressDialog

# ══════════════════════════════════════════════════════════════════════════════
# FÄLT-EDITOR DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class _FieldEditorDialog(QDialog):
    """Dialog för att redigera ett fälts metadata i mallen."""

    def __init__(
        self,
        field_name: str,
        config: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"✏️ Redigera: {field_name}")
        self.setMinimumWidth(_sp(400))
        self._field = field_name
        self._config = config
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()

        name_lbl = QLabel(f"<b><code>:: {self._field} ::</code></b>")
        form.addRow("Fält:", name_lbl)

        self._label_edit = QLineEdit()
        if self._config:
            self._label_edit.setText(
                self._config.field_labels.get(self._field, self._field)
            )
        form.addRow("Etikett:", self._label_edit)

        self._type_combo = QComboBox()
        types = ["text", "date", "vin", "reg", "number", "email", "phone"]
        if FieldType:
            types = [ft.value for ft in FieldType]
        self._type_combo.addItems(types)
        if self._config:
            current_type = self._config.field_types.get(self._field, "text")
            idx = self._type_combo.findText(current_type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
        form.addRow("Typ:", self._type_combo)

        self._default_edit = QLineEdit()
        if self._config:
            self._default_edit.setText(
                self._config.defaults.get(self._field, "")
            )
        form.addRow("Standardvärde:", self._default_edit)

        self._hint_edit = QLineEdit()
        if self._config and hasattr(self._config, "field_hints"):
            self._hint_edit.setText(
                self._config.field_hints.get(self._field, "")
            )
        form.addRow("Hjälptext:", self._hint_edit)

        self._required_cb = QCheckBox("Obligatoriskt fält")
        if self._config and hasattr(self._config, "required_fields"):
            self._required_cb.setChecked(
                self._field in self._config.required_fields
            )
        form.addRow("", self._required_cb)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_config(self) -> None:
        """Applicerar ändringar till config-objektet."""
        if not self._config:
            return
        self._config.field_labels[self._field] = self._label_edit.text()
        self._config.field_types[self._field] = self._type_combo.currentText()
        default_val = self._default_edit.text()
        if default_val:
            self._config.defaults[self._field] = default_val
        else:
            self._config.defaults.pop(self._field, None)
        if hasattr(self._config, "field_hints"):
            hint = self._hint_edit.text()
            if hint:
                self._config.field_hints[self._field] = hint
            else:
                self._config.field_hints.pop(self._field, None)
        if hasattr(self._config, "required_fields"):
            if self._required_cb.isChecked():
                if self._field not in self._config.required_fields:
                    self._config.required_fields.append(self._field)
            else:
                with suppress(ValueError):
                    self._config.required_fields.remove(self._field)

# ══════════════════════════════════════════════════════════════════════════════
# NY MALL-DIALOG
# ══════════════════════════════════════════════════════════════════════════════

class _NewTemplateDialog(QDialog):
    """Dialog för att skapa ny mall (tom, från DOCX eller kopia)."""

    def __init__(
        self,
        manager: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("➕ Skapa ny mall")
        self.setMinimumWidth(_sp(420))
        self._manager = manager
        self.result_info: Any = None
        self._build()

    def _build(self) -> None:
        from PySide6.QtWidgets import QButtonGroup, QRadioButton

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Ex: Elcertifikat 48V")
        form.addRow("Mallnamn:*", self._name_edit)

        self._cat_combo = QComboBox()
        self._cat_combo.setEditable(True)
        cats: list[str] = DEFAULT_CATEGORIES
        if self._manager and hasattr(self._manager, "get_categories"):
            cats = self._manager.get_categories() or cats
        self._cat_combo.addItems(cats)
        form.addRow("Kategori:", self._cat_combo)

        layout.addLayout(form)

        source_group = QGroupBox("Källa")
        source_layout = QVBoxLayout(source_group)
        self._rb_empty = QRadioButton("Skapa tom mall")
        self._rb_docx = QRadioButton("Från .docx-fil")
        self._rb_copy = QRadioButton("Kopiera befintlig mall:")
        self._rb_empty.setChecked(True)
        self._bg = QButtonGroup(self)
        for rb in (self._rb_empty, self._rb_docx, self._rb_copy):
            self._bg.addButton(rb)
            source_layout.addWidget(rb)

        copy_row = QHBoxLayout()
        self._copy_combo = QComboBox()
        if self._manager and hasattr(self._manager, "get_all_templates"):
            self._copy_combo.addItems(
                sorted(self._manager.get_all_templates().keys())
            )
        copy_row.addSpacing(_sp(20))
        copy_row.addWidget(self._copy_combo)
        source_layout.addWidget(copy_row)

        layout.addWidget(source_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._name_edit.setFocus()

    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Varning", "Ange ett mallnamn.")
            return
        if RESERVED_NAMES and name.upper() in {n.upper() for n in RESERVED_NAMES}:
            QMessageBox.warning(
                self, "Varning", f"'{name}' är ett reserverat namn."
            )
            return
        category = self._cat_combo.currentText().strip() or "Övriga"

        try:
            if self._rb_docx.isChecked():
                from PySide6.QtWidgets import QFileDialog

                path, _ = QFileDialog.getOpenFileName(
                    self, "Välj .docx-fil", "", "Word-dokument (*.docx)"
                )
                if not path:
                    return
                self.result_info = self._manager.create_template(
                    name, Path(path), category
                )
            elif self._rb_copy.isChecked():
                copy_from = self._copy_combo.currentText()
                if not copy_from:
                    QMessageBox.warning(
                        self, "Varning", "Välj en mall att kopiera."
                    )
                    return
                self.result_info = self._manager.duplicate_template(
                    copy_from, name
                )
            else:
                self.result_info = self._manager.create_template(
                    name, None, category
                )
        except Exception as exc:
            QMessageBox.critical(self, "Fel", str(exc))
            return
        self.accept()

# ══════════════════════════════════════════════════════════════════════════════
# PLUGIN
# ══════════════════════════════════════════════════════════════════════════════

class Plugin:
    """
    Mall-designer plugin (PySide6).

    IPlugin-kontrakt:
        name, version, description, priority
        on_load(context), on_unload(), get_tab() → QWidget
    """

    name: str = "Malldesigner"
    version: str = __version__
    description: str = "Designa och redigera intygsmallar direkt i appen"
    priority: int = 50

    def __init__(self) -> None:
        self._context: Any = None
        self._log: logging.Logger = logging.getLogger("TemplateDesigner")
        self._manager: Any = None
        self._tab: QWidget | None = None
        self._unsub_fns: list[Callable[[], None]] = []
        # 4.3.0 (från 4.2.2): skanning sammanfogas — högst en i taget.
        self._scan_lock = threading.RLock()
        self._scan_active = False
        self._scan_requested = False
        self._scan_token = 0
        self._scan_retries = 0  # 4.3.1: auto-retry efter scan-fel

        # UI-referenser (lazy — byggs vid get_tab())
        self._template_list: SearchableListbox | None = None
        self._status_bar: DesignerStatusBar | None = None
        self._detail_tabs: QTabWidget | None = None
        self._fields_tree: QTreeWidget | None = None
        self._attachments_tree: QTreeWidget | None = None
        self._preview_text: ScrollableText | None = None
        self._cat_combo: QComboBox | None = None
        self._undo_btn: QPushButton | None = None
        self._redo_btn: QPushButton | None = None
        self._placeholder_label: QLabel | None = None
        self._right_container: QWidget | None = None
        self._title_label: QLabel | None = None
        self._docx_lbl: QLabel | None = None
        self._category_lbl: QLabel | None = None
        self._fields_lbl: QLabel | None = None
        self._attach_lbl: QLabel | None = None
        self._modified_lbl: QLabel | None = None
        self._ph_text: ScrollableText | None = None
        self._desc_edit: QTextEdit | None = None
        self._settings_cat_combo: QComboBox | None = None
        self._tags_edit: QLineEdit | None = None
        self._author_edit: QLineEdit | None = None
        self._version_edit: QLineEdit | None = None
        self._created_lbl: QLabel | None = None
        self._modified2_lbl: QLabel | None = None
        self._path_lbl: QLabel | None = None  # 4.3.1: synlig mall-root
        self._current_template: str | None = None
        self._pool = QThreadPool.globalInstance()

    # ── Logg-helper (td.*-events, 4.3.0) ────────────────────────────────

    def _evt(self, event: str, msg: str, level: int = logging.INFO, **ctx: Any) -> None:
        """Fail-safe strukturerad loggpost — får aldrig krascha appen."""
        try:
            self._log.log(level, msg, extra={"event": event, "ctx": ctx or None})
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    # IPlugin
    # ══════════════════════════════════════════════════════════════════

    def on_load(self, context: Any) -> None:
        self._context = context
        self._log = context.get_logger("TemplateDesigner")
        try:
            self._setup_manager()
        except Exception as exc:
            self._log.error("[TD] setup_manager: %s", exc)
        try:
            self._subscribe_events()
        except Exception as exc:
            self._log.error("[TD] subscribe_events: %s", exc)
        self._log.info("🎨 Template Designer v%s initierad", __version__)
        self._evt("td.loaded", "Malldesigner laddad", version=__version__,
                  core_ok=_CORE_OK)

    def on_unload(self) -> None:
        for unsub in self._unsub_fns:
            with suppress(Exception):
                unsub()
        self._unsub_fns.clear()
        self._evt("td.unloaded", "Malldesigner avlastad", level=logging.DEBUG)
        self._log.info("[TD] Plugin avlastat")

    def get_tab(self) -> QWidget | None:
        if self._tab is None:
            started = time.monotonic()
            try:
                self._tab = self._build_tab()
                self._start_scan()
                self._evt("td.tab_built", "Malldesigner-flik byggd",
                          level=logging.DEBUG,
                          ms=round((time.monotonic() - started) * 1000, 1))
            except Exception as exc:
                self._log.error("[TD] Kunde inte bygga tab: %s", exc)
                self._evt("td.tab_build_failed", f"Flikbygge misslyckades: {exc}",
                          level=logging.ERROR, error=str(exc))
                return self._build_error_tab(str(exc))
        return self._tab

    # ══════════════════════════════════════════════════════════════════
    # SETUP
    # ══════════════════════════════════════════════════════════════════

    def _setup_manager(self) -> None:
        if not _CORE_OK or TemplateManager is None:
            self._log.warning("[TD] core.py ej tillgänglig — manager saknas")
            return
        base = getattr(self._context, "base_path", Path("."))
        tmpl = base / "Mallar"
        out = base / "Out"
        tmpl.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        self._manager = TemplateManager(
            templates_path=tmpl,
            output_path=out,
            log=self._log,
        )

    def _subscribe_events(self) -> None:
        if not self._context:
            return
        eb = getattr(self._context, "event_bus", None)
        if not eb:
            return
        event_map: dict[str, Callable[[Any], None]] = {
            "refresh_templates": lambda _d: self._start_scan(),
            "template.created": lambda _d: self._start_scan(),
            "template.deleted": lambda _d: self._start_scan(),
            "template.updated": lambda _d: self._start_scan(),
            "settings.theme_changed": lambda _d: None,
        }
        for evt_name, handler in event_map.items():
            with suppress(Exception):
                unsub = eb.subscribe(evt_name, handler)
                if callable(unsub):
                    self._unsub_fns.append(unsub)

    # ══════════════════════════════════════════════════════════════════
    # SKANNING (4.3.0: sammanfogad, token-säkrad, loggad)
    # ══════════════════════════════════════════════════════════════════

    def _templates_root(self) -> Path | None:
        """Mall-rooten som skannas (för synlighet och diagnostik)."""
        if self._manager is None:
            return None
        return getattr(
            self._manager, "templates_path",
            getattr(self._manager, "_templates_path", None),
        )

    def _root_display_text(self) -> str:
        root = self._templates_root()
        return f"📁 {root}" if root else "⚠️ TemplateManager saknas — core.py kunde inte laddas"

    def _start_scan(self) -> None:
        if not self._manager:
            return
        with self._scan_lock:
            if self._scan_active:
                self._scan_requested = True
                return
            self._scan_active = True
            self._scan_requested = False
            self._scan_token += 1
            token = self._scan_token
        self._evt("td.scan_started", "Mallskanning startad",
                  level=logging.DEBUG, token=token)
        worker = _ScanWorker(self._manager, token)
        worker.signals.finished.connect(self._on_scan_done)
        worker.signals.error.connect(
            lambda e, scan_token=token: self._on_scan_error(scan_token, e)
        )
        self._pool.start(worker)

    @Slot(object)
    def _on_scan_done(self, payload: Any) -> None:
        token, templates = payload if isinstance(payload, tuple) else (0, payload)
        with self._scan_lock:
            if token != self._scan_token:
                return
            self._scan_active = False
            rescan = self._scan_requested
            self._scan_requested = False
        started_note = time.monotonic()
        self._scan_retries = 0  # 4.3.1: lyckad scan nollställer retry-räknaren
        self._refresh_list_ui()
        count = len(templates) if templates else 0
        valid = getattr(self._manager, "valid_template_count", count)
        self._log.info("[TD] Skannade %d mallar (%d giltiga)", count, valid)
        self._evt("td.scan_done", f"Skanning klar: {count} mallar ({valid} giltiga)",
                  count=count, valid=valid, root=str(self._templates_root() or ""),
                  refresh_ms=round((time.monotonic() - started_note) * 1000, 1))
        if self._status_bar:
            self._status_bar.set_stats(f"{count} mallar | {valid} giltiga")
        if count == 0:
            # 4.3.1: tom skanning ska aldrig vara tyst eller osynlig.
            root = self._templates_root()
            self._evt("td.scan_empty",
                      f"Inga mallar hittades i {root}",
                      level=logging.WARNING, root=str(root or ""))
            if self._status_bar:
                self._status_bar.set_warning(
                    f"Inga mallar i {root} — varje mall behöver en egen "
                    f"undermapp med minst en .docx-fil", 8000,
                )
            if self._path_lbl:
                self._path_lbl.setText(self._root_display_text())
        if self._context:
            with suppress(Exception):
                self._context.emit_event(
                    "templates.scanned",
                    {
                        "count": count,
                        "names": list(templates.keys()) if templates else [],
                    },
                )
        if rescan:
            QTimer.singleShot(60, self._start_scan)

    def _on_scan_error(self, token: int, error: str) -> None:
        with self._scan_lock:
            if token != self._scan_token:
                return
            self._scan_active = False
            rescan = self._scan_requested
            self._scan_requested = False
        self._log.error("[TD] Skanningsfel: %s", error)
        self._evt("td.scan_error", f"Skanningsfel: {error}",
                  level=logging.ERROR, error=error,
                  root=str(self._templates_root() or ""))
        # 4.3.1: felet syns i UI + ett begränsat auto-retry (2 s).
        if self._status_bar:
            self._status_bar.set_error(f"Mallskanning misslyckades: {error}", 8000)
        if self._scan_retries < 1:
            self._scan_retries += 1
            QTimer.singleShot(2000, self._start_scan)
        if rescan:
            QTimer.singleShot(60, self._start_scan)

    # ══════════════════════════════════════════════════════════════════
    # BYGG TAB (4.3.0: adaptiva mått)
    # ══════════════════════════════════════════════════════════════════

    def _build_tab(self) -> QWidget:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(_sp(4), _sp(4), _sp(4), _sp(4))
        root_layout.setSpacing(_sp(4))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        root_layout.addWidget(splitter, stretch=1)

        left = self._build_left_panel()
        left.setMaximumWidth(_sp(UI.LEFT_PANEL_WIDTH + 40))
        left.setMinimumWidth(_sp(220))
        splitter.addWidget(left)

        right = self._build_right_panel()
        splitter.addWidget(right)
        splitter.setStretchFactor(1, 1)

        self._status_bar = DesignerStatusBar()
        root_layout.addWidget(self._status_bar)
        return root

    def _build_left_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(UI.PAD_SMALL)

        toolbar = QHBoxLayout()
        new_btn = QPushButton("➕ Ny")
        new_btn.clicked.connect(self._on_new_template)
        toolbar.addWidget(new_btn)
        for icon, tip, slot in [
            ("📋", "Duplicera vald mall", self._on_duplicate),
            ("🗑️", "Ta bort vald mall", self._on_delete),
        ]:
            btn = QPushButton(icon)
            btn.setFixedWidth(_sp(32))
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            toolbar.addWidget(btn)
        toolbar.addStretch()

        self._undo_btn = QPushButton("↩️")
        self._undo_btn.setFixedWidth(_sp(32))
        self._undo_btn.setToolTip("Ångra")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._on_undo)
        toolbar.addWidget(self._undo_btn)

        self._redo_btn = QPushButton("↪️")
        self._redo_btn.setFixedWidth(_sp(32))
        self._redo_btn.setToolTip("Gör om")
        self._redo_btn.setEnabled(False)
        self._redo_btn.clicked.connect(self._on_redo)
        toolbar.addWidget(self._redo_btn)

        refresh_btn = QPushButton("🔄")
        refresh_btn.setFixedWidth(_sp(32))
        refresh_btn.setToolTip("Uppdatera listan")
        refresh_btn.clicked.connect(self._start_scan)
        toolbar.addWidget(refresh_btn)
        layout.addLayout(toolbar)

        # 4.3.1: synlig mall-root — fel sökväg ska aldrig vara osynlig.
        self._path_lbl = QLabel(self._root_display_text())
        self._path_lbl.setWordWrap(True)
        self._path_lbl.setStyleSheet(f"color: {UI.COLOR_MUTED};")
        layout.addWidget(self._path_lbl)

        self._template_list = SearchableListbox()
        self._template_list.item_selected.connect(self._on_template_selected)
        self._template_list.item_activated.connect(self._on_open_docx)
        layout.addWidget(self._template_list, stretch=1)

        cat_group = QGroupBox("Kategori")
        cat_layout = QVBoxLayout(cat_group)
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("Alla")
        if self._manager and hasattr(self._manager, "get_categories"):
            self._cat_combo.addItems(self._manager.get_categories())
        self._cat_combo.currentTextChanged.connect(
            lambda _t: self._refresh_list_ui()
        )
        cat_layout.addWidget(self._cat_combo)
        layout.addWidget(cat_group)

        ie_row = QHBoxLayout()
        imp_btn = QPushButton("📥 Import")
        imp_btn.clicked.connect(self._on_import)
        ie_row.addWidget(imp_btn)
        exp_btn = QPushButton("📤 Export")
        exp_btn.clicked.connect(self._on_export)
        ie_row.addWidget(exp_btn)
        layout.addLayout(ie_row)
        return widget

    def _build_right_panel(self) -> QWidget:
        self._placeholder_label = QLabel(
            f"👈 Välj en mall från listan\n\n"
            f"Eller tryck {OSPlatform.get_modifier_symbol()}+N för att skapa ny"
        )
        self._placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder_label.setWordWrap(True)
        self._placeholder_label.setStyleSheet(
            f"color: {UI.COLOR_MUTED}; font-size: {_sp(13)}px;"
        )

        self._detail_tabs = QTabWidget()
        self._detail_tabs.addTab(self._build_overview_tab(), "📋 Översikt")
        self._detail_tabs.addTab(self._build_fields_tab(), "✏️ Fält")
        self._detail_tabs.addTab(self._build_attachments_tab(), "📎 Bilagor")
        self._detail_tabs.addTab(self._build_preview_tab(), "👁️ Förhandsgranska")
        self._detail_tabs.addTab(self._build_settings_tab(), "⚙️ Inställningar")
        self._detail_tabs.hide()

        container = QWidget()
        stack = QVBoxLayout(container)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.addWidget(self._placeholder_label)
        stack.addWidget(self._detail_tabs)
        self._right_container = container
        return container

    # ── Detalj-tabs ───────────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._title_label = QLabel("")
        self._title_label.setWordWrap(True)
        font = self._title_label.font()
        font.setPointSize(14)
        font.setBold(True)
        self._title_label.setFont(font)
        layout.addWidget(self._title_label)

        info_group = QGroupBox("Information")
        info_form = QFormLayout(info_group)
        self._docx_lbl = QLabel("-")
        self._docx_lbl.setWordWrap(True)
        self._category_lbl = QLabel("-")
        self._fields_lbl = QLabel("-")
        self._attach_lbl = QLabel("-")
        self._modified_lbl = QLabel("-")
        info_form.addRow("Mall-fil:", self._docx_lbl)
        info_form.addRow("Kategori:", self._category_lbl)
        info_form.addRow("Fält:", self._fields_lbl)
        info_form.addRow("Bilagor:", self._attach_lbl)
        info_form.addRow("Senast ändrad:", self._modified_lbl)
        layout.addWidget(info_group)

        ph_group = QGroupBox("Fält i mallen")
        ph_layout = QVBoxLayout(ph_group)
        self._ph_text = ScrollableText(readonly=True)
        self._ph_text.setFixedHeight(_sp(120))
        ph_layout.addWidget(self._ph_text)
        layout.addWidget(ph_group)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("🧪 Testa mall", self._on_test_generate),
            ("📂 Öppna mapp", self._on_open_folder),
            ("📝 Redigera DOCX", self._on_open_docx),
            ("✏️ Byt namn", self._on_rename),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return scroll

    def _build_fields_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._fields_tree = QTreeWidget()
        self._fields_tree.setHeaderLabels(
            ["Fält", "Etikett", "Typ", "Standardvärde", "Oblig.", "Hjälptext"]
        )
        self._fields_tree.header().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._fields_tree.setAlternatingRowColors(True)
        self._fields_tree.itemDoubleClicked.connect(self._on_edit_field)
        layout.addWidget(self._fields_tree)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("✏️ Redigera", self._on_edit_selected_field),
            ("🔄 Uppdatera från mall", self._on_refresh_fields),
            ("💾 Spara", self._on_save_field_config),
            ("📋 Kopiera fältnamn", self._on_copy_field_names),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return widget

    def _build_attachments_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        self._attachments_tree = QTreeWidget()
        self._attachments_tree.setHeaderLabels(["Filnamn", "Storlek", "Ändrad"])
        self._attachments_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._attachments_tree.setAlternatingRowColors(True)
        layout.addWidget(self._attachments_tree)

        hint = QLabel("💡 Använd knappen nedan för att lägga till PDF-bilagor.")
        hint.setStyleSheet(f"color: {UI.COLOR_MUTED};")
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        for label, slot in [
            ("➕ Lägg till PDF", self._on_add_attachment),
            ("🗑️ Ta bort", self._on_remove_attachment),
            ("📂 Öppna", self._on_open_attachment),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        for icon, direction in [("⬆️", -1), ("⬇️", 1)]:
            mv_btn = QPushButton(icon)
            mv_btn.setFixedWidth(_sp(32))
            mv_btn.clicked.connect(
                lambda _checked=False, d=direction: self._on_move_attachment(d)
            )
            btn_row.addWidget(mv_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return widget

    def _build_preview_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        toolbar = QHBoxLayout()
        reload_btn = QPushButton("🔄 Ladda om")
        reload_btn.clicked.connect(self._on_refresh_preview)
        toolbar.addWidget(reload_btn)
        test_btn = QPushButton("🧪 Generera test-PDF")
        test_btn.clicked.connect(self._on_test_generate)
        toolbar.addWidget(test_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self._preview_text = ScrollableText(readonly=True)
        layout.addWidget(self._preview_text)
        return widget

    def _build_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        form = QFormLayout()
        self._desc_edit = QTextEdit()
        self._desc_edit.setFixedHeight(_sp(70))
        form.addRow("Beskrivning:", self._desc_edit)

        self._settings_cat_combo = QComboBox()
        self._settings_cat_combo.setEditable(True)
        self._settings_cat_combo.addItems(DEFAULT_CATEGORIES)
        form.addRow("Kategori:", self._settings_cat_combo)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText("Komma-separerade taggar")
        form.addRow("Taggar:", self._tags_edit)

        self._author_edit = QLineEdit()
        form.addRow("Författare:", self._author_edit)

        self._version_edit = QLineEdit()
        form.addRow("Version:", self._version_edit)
        layout.addLayout(form)

        ts_group = QGroupBox("Tidsstämplar")
        ts_form = QFormLayout(ts_group)
        self._created_lbl = QLabel("-")
        self._modified2_lbl = QLabel("-")
        ts_form.addRow("Skapad:", self._created_lbl)
        ts_form.addRow("Ändrad:", self._modified2_lbl)
        layout.addWidget(ts_group)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 Spara inställningar")
        save_btn.clicked.connect(self._on_save_settings)
        btn_row.addWidget(save_btn)
        reset_btn = QPushButton("↩️ Återställ")
        reset_btn.clicked.connect(self._on_reset_settings)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return scroll

    def _build_error_tab(self, error: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(f"⚠️ Template Designer kunde inte laddas\n\n{error}")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {UI.COLOR_ERROR};")
        layout.addWidget(lbl)
        retry_btn = QPushButton("🔄 Försök igen")
        retry_btn.clicked.connect(self._on_retry)
        layout.addWidget(retry_btn)
        return widget

    # ══════════════════════════════════════════════════════════════════
    # LIST-HANTERING (4.3.0: ingen signalstorm, ingen onödig ombyggning)
    # ══════════════════════════════════════════════════════════════════

    def _refresh_list_ui(self) -> None:
        if not self._template_list or not self._manager:
            return
        templates = self._manager.get_all_templates()
        cat_filter = (
            self._cat_combo.currentText() if self._cat_combo else "Alla"
        )
        items: list[tuple[str, str]] = []
        for tmpl_name in sorted(templates.keys()):
            info = templates[tmpl_name]
            if cat_filter != "Alla":
                if getattr(info, "category", "") != cat_filter:
                    continue
            icon = getattr(info, "icon", "📄")
            if callable(getattr(info, "get_status_icon", None)):
                icon = info.get_status_icon()
            items.append((f"{icon} {tmpl_name}", tmpl_name))
        self._template_list.set_items(items)

        if self._cat_combo and self._manager:
            cats = (
                self._manager.get_categories()
                if hasattr(self._manager, "get_categories")
                else []
            )
            wanted = ["Alla"] + [str(c) for c in cats]
            existing = [
                self._cat_combo.itemText(i)
                for i in range(self._cat_combo.count())
            ]
            if existing != wanted:
                current = self._cat_combo.currentText()
                self._cat_combo.blockSignals(True)
                try:
                    self._cat_combo.clear()
                    self._cat_combo.addItems(wanted)
                    idx = self._cat_combo.findText(current)
                    if idx >= 0:
                        self._cat_combo.setCurrentIndex(idx)
                finally:
                    self._cat_combo.blockSignals(False)

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — MALLVAL & DETALJER
    # ══════════════════════════════════════════════════════════════════

    @Slot(str)
    def _on_template_selected(self, tmpl_name: str) -> None:
        self._current_template = tmpl_name
        self._evt("td.template_selected", f"Mall vald: {tmpl_name}",
                  level=logging.DEBUG, template=tmpl_name)
        self._update_details()
        if self._status_bar:
            self._status_bar.set_info(f"Vald mall: {tmpl_name}", 2000)

    def _update_details(self) -> None:
        if not self._current_template or not self._manager:
            self._show_placeholder()
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            self._show_placeholder()
            return

        if self._placeholder_label:
            self._placeholder_label.hide()
        if self._detail_tabs:
            self._detail_tabs.show()

        # Översikt
        if self._title_label:
            self._title_label.setText(
                f"{getattr(info, 'icon', '📄')} {info.name}"
            )
        docx = getattr(info, "docx_file", None)
        if self._docx_lbl:
            self._docx_lbl.setText(docx.name if docx else "❌ Saknas!")
            self._docx_lbl.setStyleSheet(
                "" if docx else f"color: {UI.COLOR_ERROR};"
            )
        if self._category_lbl:
            self._category_lbl.setText(getattr(info, "category", "-"))
        if self._fields_lbl:
            self._fields_lbl.setText(
                str(
                    getattr(
                        info,
                        "field_count",
                        len(getattr(info, "placeholders", [])),
                    )
                )
            )
        if self._attach_lbl:
            self._attach_lbl.setText(
                str(
                    getattr(
                        info,
                        "attachment_count",
                        len(getattr(info, "attachments", [])),
                    )
                )
            )
        if self._modified_lbl:
            modified = getattr(info, "last_modified", None)
            if modified and hasattr(modified, "strftime"):
                self._modified_lbl.setText(
                    modified.strftime("%Y-%m-%d %H:%M")
                )
            else:
                self._modified_lbl.setText("-")

        placeholders = getattr(info, "placeholders", [])
        if self._ph_text:
            self._ph_text.set_text(
                "\n".join(f":: {p} ::" for p in placeholders)
                or "(Inga fält hittade)"
            )

        self._update_fields_tree(info)
        self._update_attachments_tree(info)
        self._update_settings_tab(info)
        self._refresh_preview_content(info)
        self._update_undo_buttons()

    def _show_placeholder(self) -> None:
        if self._placeholder_label:
            self._placeholder_label.show()
        if self._detail_tabs:
            self._detail_tabs.hide()

    def _update_fields_tree(self, info: Any) -> None:
        if not self._fields_tree:
            return
        self._fields_tree.clear()
        config = getattr(info, "config", None)
        for ph in getattr(info, "placeholders", []):
            label = config.field_labels.get(ph, ph) if config else ph
            ft = config.field_types.get(ph, "text") if config else "text"
            default = config.defaults.get(ph, "") if config else ""
            req = "✓" if config and ph in config.required_fields else ""
            hint = (
                config.field_hints.get(ph, "")
                if config and hasattr(config, "field_hints")
                else ""
            )
            QTreeWidgetItem(
                self._fields_tree, [ph, label, ft, default, req, hint]
            )

    def _update_attachments_tree(self, info: Any) -> None:
        if not self._attachments_tree:
            return
        self._attachments_tree.clear()
        for att in getattr(info, "attachments", []):
            try:
                stat = att.stat()
                size = f"{stat.st_size / 1024:.1f} KB"
                mod = datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except Exception:
                size, mod = "?", "?"
            QTreeWidgetItem(self._attachments_tree, [att.name, size, mod])

    def _update_settings_tab(self, info: Any) -> None:
        config = getattr(info, "config", None)
        if not config:
            return
        if self._desc_edit:
            self._desc_edit.setPlainText(getattr(config, "description", ""))
        cat = getattr(config, "category", "")
        if self._settings_cat_combo:
            idx = self._settings_cat_combo.findText(cat)
            if idx >= 0:
                self._settings_cat_combo.setCurrentIndex(idx)
            else:
                self._settings_cat_combo.setCurrentText(cat)
        tags = getattr(config, "tags", [])
        if self._tags_edit:
            self._tags_edit.setText(
                ", ".join(tags) if isinstance(tags, list) else str(tags)
            )
        if self._author_edit:
            self._author_edit.setText(getattr(config, "author", ""))
        if self._version_edit:
            self._version_edit.setText(getattr(config, "version", ""))
        created = getattr(config, "created", "-")
        modified = getattr(config, "modified", "-")
        if self._created_lbl:
            self._created_lbl.setText(str(created)[:19] if created else "-")
        if self._modified2_lbl:
            self._modified2_lbl.setText(
                str(modified)[:19] if modified else "-"
            )

    def _refresh_preview_content(self, info: Any) -> None:
        if not self._preview_text:
            return
        docx = getattr(info, "docx_file", None)
        if not docx or not PlaceholderExtractor:
            self._preview_text.set_text(
                "(DOCX saknas eller core ej tillgänglig)"
            )
            return
        try:
            text_content = PlaceholderExtractor.get_all_text(docx)
            self._preview_text.set_text(text_content or "(Tom mall)")
        except Exception as exc:
            self._preview_text.set_text(f"Kunde inte läsa mall: {exc}")

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — VERKTYGSFÄLT
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_new_template(self) -> None:
        if not self._manager:
            QMessageBox.warning(
                self._tab, "Fel", "TemplateManager ej tillgänglig."
            )
            return
        dialog = _NewTemplateDialog(self._manager, self._tab)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_info:
            info = dialog.result_info
            self._evt("td.template_created", f"Mall skapad: {info.name}",
                      template=info.name)
            self._start_scan()
            QTimer.singleShot(
                500,
                lambda: self._template_list.select_by_key(info.name)
                if self._template_list
                else None,
            )
            if self._status_bar:
                self._status_bar.set_success(f"Mall '{info.name}' skapad!")
            if self._context:
                with suppress(Exception):
                    self._context.emit_event(
                        "template.created", {"name": info.name}
                    )

    @Slot()
    def _on_duplicate(self) -> None:
        if not self._current_template or not self._manager:
            return
        new_name, ok = self._input_dialog(
            "Duplicera",
            f"Namn för kopia av '{self._current_template}':",
            f"{self._current_template}_kopia",
        )
        if not ok or not new_name:
            return
        try:
            info = self._manager.duplicate_template(
                self._current_template, new_name
            )
            if info:
                self._evt("td.template_duplicated",
                          f"Mall duplicerad: {self._current_template} → {info.name}",
                          source=self._current_template, template=info.name)
                self._start_scan()
                if self._status_bar:
                    self._status_bar.set_success(
                        f"Mall '{info.name}' skapad!"
                    )
        except Exception as exc:
            self._evt("td.duplicate_failed", f"Duplicering misslyckades: {exc}",
                      level=logging.ERROR, error=str(exc))
            QMessageBox.critical(self._tab, "Fel", str(exc))

    @Slot()
    def _on_delete(self) -> None:
        if not self._template_list or not self._manager:
            return
        selected = self._template_list.get_selected_keys()
        if not selected:
            return

        if len(selected) == 1:
            tmpl_name = selected[0]
            reply = QMessageBox.question(
                self._tab,
                "Bekräfta",
                f"Ta bort '{tmpl_name}'?\n\nKan ångras med Ctrl+Z.",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self._manager.delete_template(tmpl_name):
                    self._evt("td.template_deleted", f"Mall borttagen: {tmpl_name}",
                              template=tmpl_name)
                    self._current_template = None
                    self._refresh_list_ui()
                    self._update_details()
                    if self._status_bar:
                        self._status_bar.set_info(
                            f"🗑️ Tog bort: {tmpl_name}", 3000
                        )
        else:
            preview = "\n".join(f"• {n}" for n in selected[:5])
            if len(selected) > 5:
                preview += f"\n... och {len(selected) - 5} till"
            reply = QMessageBox.question(
                self._tab,
                "Bekräfta",
                f"Ta bort {len(selected)} mallar?\n\n{preview}",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                deleted = sum(
                    1 for n in selected if self._manager.delete_template(n)
                )
                self._evt("td.templates_deleted",
                          f"{deleted}/{len(selected)} mallar borttagna",
                          deleted=deleted, requested=len(selected))
                self._current_template = None
                self._refresh_list_ui()
                self._update_details()
                if self._status_bar:
                    self._status_bar.set_info(
                        f"🗑️ Tog bort {deleted}/{len(selected)} mallar", 3000
                    )

    @Slot()
    def _on_rename(self) -> None:
        if not self._current_template or not self._manager:
            return
        new_name, ok = self._input_dialog(
            "Byt namn",
            f"Nytt namn för '{self._current_template}':",
            self._current_template,
        )
        if not ok or not new_name or new_name == self._current_template:
            return
        old = self._current_template
        if self._manager.rename_template(old, new_name):
            self._evt("td.template_renamed", f"Mall omdöpt: {old} → {new_name}",
                      old=old, new=new_name)
            self._current_template = new_name
            self._start_scan()
            QTimer.singleShot(
                500,
                lambda: self._template_list.select_by_key(new_name)
                if self._template_list
                else None,
            )
            if self._status_bar:
                self._status_bar.set_success(
                    f"Bytte namn: {old} → {new_name}"
                )

    @Slot()
    def _on_undo(self) -> None:
        if not self._manager or not hasattr(self._manager, "undo"):
            return
        desc = self._manager.undo()
        if desc:
            self._evt("td.undo", f"Ångrade: {desc}", level=logging.DEBUG, action=desc)
            self._refresh_list_ui()
            self._update_details()
            if self._status_bar:
                self._status_bar.set_info(f"↩️ Ångrade: {desc}", 3000)
            self._update_undo_buttons()

    @Slot()
    def _on_redo(self) -> None:
        if not self._manager or not hasattr(self._manager, "redo"):
            return
        desc = self._manager.redo()
        if desc:
            self._evt("td.redo", f"Gjorde om: {desc}", level=logging.DEBUG, action=desc)
            self._refresh_list_ui()
            self._update_details()
            if self._status_bar:
                self._status_bar.set_info(f"↪️ Gjorde om: {desc}", 3000)
            self._update_undo_buttons()

    def _update_undo_buttons(self) -> None:
        can_undo = bool(getattr(self._manager, "can_undo", False))
        can_redo = bool(getattr(self._manager, "can_redo", False))
        if self._undo_btn:
            self._undo_btn.setEnabled(can_undo)
        if self._redo_btn:
            self._redo_btn.setEnabled(can_redo)

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — FÄLT
    # ══════════════════════════════════════════════════════════════════

    @Slot(QTreeWidgetItem, int)
    def _on_edit_field(self, item: QTreeWidgetItem, _column: int) -> None:
        self._open_field_editor(item.text(0))

    @Slot()
    def _on_edit_selected_field(self) -> None:
        if not self._fields_tree:
            return
        selected = self._fields_tree.currentItem()
        if not selected:
            QMessageBox.information(
                self._tab, "Info", "Välj ett fält att redigera."
            )
            return
        self._open_field_editor(selected.text(0))

    def _open_field_editor(self, field_name: str) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        config = getattr(info, "config", None)
        dialog = _FieldEditorDialog(field_name, config, self._tab)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            dialog.apply_to_config()
            with suppress(Exception):
                self._manager.save_config(self._current_template, config)
            self._evt("td.field_saved", f"Fält sparat: {field_name}",
                      field=field_name, template=self._current_template)
            self._update_details()
            if self._status_bar:
                self._status_bar.set_success(f"Sparade fält: {field_name}")

    @Slot()
    def _on_refresh_fields(self) -> None:
        if not self._current_template or not self._manager:
            return
        with suppress(Exception):
            self._manager.refresh_template(self._current_template)
        self._evt("td.fields_refreshed", "Fält uppdaterade från mall",
                  level=logging.DEBUG, template=self._current_template)
        self._update_details()
        if self._status_bar:
            self._status_bar.set_success("Fält uppdaterade från mall")

    @Slot()
    def _on_save_field_config(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        config = getattr(info, "config", None)
        if config:
            with suppress(Exception):
                self._manager.save_config(self._current_template, config)
            self._evt("td.field_config_saved", "Fält-konfiguration sparad",
                      template=self._current_template)
            if self._status_bar:
                self._status_bar.set_success("Fält-konfiguration sparad!")

    @Slot()
    def _on_copy_field_names(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        placeholders = getattr(info, "placeholders", [])
        text = "\n".join(f":: {p} ::" for p in placeholders)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        self._evt("td.field_names_copied", f"Kopierade {len(placeholders)} fältnamn",
                  level=logging.DEBUG, count=len(placeholders))
        if self._status_bar:
            self._status_bar.set_success(
                f"Kopierade {len(placeholders)} fältnamn"
            )

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — BILAGOR
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_add_attachment(self) -> None:
        if not self._current_template or not self._manager:
            return
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(
            self._tab, "Välj PDF-bilagor", "", "PDF-filer (*.pdf)"
        )
        if not paths:
            return
        added = sum(
            1
            for p in paths
            if self._manager.add_attachment(self._current_template, Path(p))
        )
        if added > 0:
            self._evt("td.attachment_added", f"{added} bilaga(or) tillagda",
                      added=added, template=self._current_template)
            self._update_details()
            if self._status_bar:
                self._status_bar.set_success(f"La till {added} bilaga(or)")

    @Slot()
    def _on_remove_attachment(self) -> None:
        if (
            not self._current_template
            or not self._manager
            or not self._attachments_tree
        ):
            return
        selected = self._attachments_tree.currentItem()
        if not selected:
            return
        att_name = selected.text(0)
        reply = QMessageBox.question(
            self._tab,
            "Bekräfta",
            f"Ta bort '{att_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self._manager.remove_attachment(
                self._current_template, att_name
            ):
                self._evt("td.attachment_removed", f"Bilaga borttagen: {att_name}",
                          attachment=att_name, template=self._current_template)
                self._update_details()

    @Slot()
    def _on_open_attachment(self) -> None:
        if (
            not self._current_template
            or not self._manager
            or not self._attachments_tree
        ):
            return
        selected = self._attachments_tree.currentItem()
        if not selected:
            return
        att_name = selected.text(0)
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        for att in getattr(info, "attachments", []):
            if att.name == att_name:
                self._evt("td.attachment_opened", f"Bilaga öppnad: {att_name}",
                          level=logging.DEBUG, attachment=att_name)
                OSPlatform.open_path(att)
                break

    def _on_move_attachment(self, direction: int) -> None:
        if (
            not self._current_template
            or not self._manager
            or not self._attachments_tree
        ):
            return
        selected = self._attachments_tree.currentItem()
        if not selected:
            return
        att_name = selected.text(0)
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        attachments = getattr(info, "attachments", [])
        idx = next(
            (i for i, a in enumerate(attachments) if a.name == att_name), None
        )
        if idx is None:
            return
        new_idx = idx + direction
        if 0 <= new_idx < len(attachments):
            attachments[idx], attachments[new_idx] = (
                attachments[new_idx],
                attachments[idx],
            )
            if hasattr(self._manager, "reorder_attachments"):
                self._manager.reorder_attachments(
                    self._current_template, [a.name for a in attachments]
                )
            self._evt("td.attachment_moved", f"Bilaga flyttad: {att_name} ({direction:+d})",
                      level=logging.DEBUG, attachment=att_name, direction=direction)
            self._update_attachments_tree(info)

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — INSTÄLLNINGAR
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_save_settings(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        config = getattr(info, "config", None)
        if not config:
            return

        if self._desc_edit:
            config.description = self._desc_edit.toPlainText().strip()
        if self._settings_cat_combo:
            config.category = self._settings_cat_combo.currentText()
        if self._tags_edit:
            config.tags = [
                t.strip() for t in self._tags_edit.text().split(",")
                if t.strip()
            ]
        if self._author_edit:
            config.author = self._author_edit.text()
        if self._version_edit:
            config.version = self._version_edit.text()

        try:
            self._manager.save_config(self._current_template, config)
            self._evt("td.settings_saved", "Mallinställningar sparade",
                      template=self._current_template,
                      category=config.category)
            self._refresh_list_ui()
            if self._status_bar:
                self._status_bar.set_success("Inställningar sparade!")
        except Exception as exc:
            self._evt("td.settings_save_failed", f"Sparning misslyckades: {exc}",
                      level=logging.ERROR, error=str(exc))
            QMessageBox.critical(self._tab, "Fel", str(exc))

    @Slot()
    def _on_reset_settings(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if info:
            self._update_settings_tab(info)
        if self._status_bar:
            self._status_bar.set_info("↩️ Inställningar återställda", 3000)

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — FÖRHANDSGRANSKNING & TEST
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_refresh_preview(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if info:
            self._refresh_preview_content(info)

    @Slot()
    def _on_test_generate(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        config = getattr(info, "config", None)
        placeholders = getattr(info, "placeholders", [])

        dialog = QDialog(self._tab)
        dialog.setWindowTitle(f"🧪 Testa: {info.name}")
        dialog.setMinimumWidth(_sp(460))
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        entries: dict[str, QLineEdit] = {}
        for ph in placeholders:
            default = self._smart_default(ph, config)
            edit = QLineEdit(default)
            form.addRow(f"{ph}:", edit)
            entries[ph] = edit
        layout.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = {k: v.text() for k, v in entries.items()}
            self._log.info("[TD] Test-generering: %s %s", info.name, values)
            self._evt("td.test_generate", f"Test-generering: {info.name}",
                      template=info.name, fields=len(values))
            if self._status_bar:
                self._status_bar.set_info("🧪 Test-generering körs...", 3000)
            if self._context:
                with suppress(Exception):
                    self._context.emit_event(
                        "certificate.before_generate",
                        {"template": info.name, "values": values},
                    )

    @staticmethod
    def _smart_default(field: str, config: Any) -> str:
        if config and hasattr(config, "defaults"):
            val = config.defaults.get(field)
            if val:
                return val
        defaults: dict[str, str] = {
            "VIN": "WVWZZZ3CZWE123456",
            "REG": "ABC123",
            "DATUM": datetime.now().strftime("%Y-%m-%d"),
            "DATE": datetime.now().strftime("%Y-%m-%d"),
            "ÄGARE": "Test Testsson",
            "NAMN": "Test Testsson",
            "MÄRKE": "Volkswagen",
            "MODELL": "Golf",
            "ÅRSMODELL": str(datetime.now().year - 5),
        }
        return defaults.get(field.upper(), f"[TEST-{field}]")

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — ÖPPNA FIL/MAPP
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_open_docx(self, name: str | None = None) -> None:
        template_name = name or self._current_template
        if not template_name or not self._manager:
            return
        info = self._manager.get_template(template_name)
        if not info:
            return
        docx = getattr(info, "docx_file", None)
        if docx:
            self._evt("td.open_docx", f"Öppnar DOCX: {Path(docx).name}",
                      level=logging.DEBUG, path=str(docx))
            OSPlatform.open_path(docx)

    @Slot()
    def _on_open_folder(self) -> None:
        if not self._current_template or not self._manager:
            return
        info = self._manager.get_template(self._current_template)
        if not info:
            return
        folder = getattr(info, "folder", None)
        if folder:
            self._evt("td.open_folder", f"Öppnar mapp: {folder}",
                      level=logging.DEBUG, path=str(folder))
            OSPlatform.open_path(folder)

    # ══════════════════════════════════════════════════════════════════
    # SLOTS — IMPORT / EXPORT
    # ══════════════════════════════════════════════════════════════════

    @Slot()
    def _on_import(self) -> None:
        if not self._manager:
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self._tab, "Importera mall", "", "ZIP-arkiv (*.zip)"
        )
        if not path:
            return
        self._evt("td.import_started", f"Import startad: {Path(path).name}",
                  path=path)
        progress = QProgressDialog("Importerar...", None, 0, 0, self._tab)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        worker = _ImportWorker(self._manager, Path(path))
        worker.signals.finished.connect(
            lambda info: self._on_import_done(info, progress)
        )
        worker.signals.error.connect(
            lambda e: self._on_import_error(e, progress)
        )
        self._pool.start(worker)

    @Slot(object, object)
    def _on_import_done(self, info: Any, progress: QProgressDialog) -> None:
        progress.close()
        if info:
            self._evt("td.import_done", f"Import klar: {info.name}",
                      template=getattr(info, "name", ""))
            self._start_scan()
            QTimer.singleShot(
                500,
                lambda: self._template_list.select_by_key(info.name)
                if self._template_list
                else None,
            )
            if self._status_bar:
                self._status_bar.set_success(f"Importerade: {info.name}")
            if self._context:
                with suppress(Exception):
                    self._context.emit_event(
                        "template.created", {"name": info.name}
                    )
        else:
            self._evt("td.import_failed", "Import misslyckades",
                      level=logging.ERROR)
            QMessageBox.critical(self._tab, "Fel", "Import misslyckades.")

    @Slot(str, object)
    def _on_import_error(self, error: str, progress: QProgressDialog) -> None:
        progress.close()
        self._evt("td.import_failed", f"Import misslyckades: {error}",
                  level=logging.ERROR, error=error)
        QMessageBox.critical(
            self._tab, "Fel", f"Import misslyckades:\n{error}"
        )

    @Slot()
    def _on_export(self) -> None:
        if not self._template_list or not self._manager:
            return
        selected = self._template_list.get_selected_keys()
        if not selected:
            QMessageBox.information(
                self._tab, "Info", "Välj en mall att exportera."
            )
            return

        from PySide6.QtWidgets import QFileDialog

        if len(selected) == 1:
            exp_name = selected[0]
            path, _ = QFileDialog.getSaveFileName(
                self._tab,
                f"Exportera '{exp_name}'",
                f"{exp_name}.zip",
                "ZIP-arkiv (*.zip)",
            )
            if not path:
                return
            dest = Path(path)
        else:
            dest_str = QFileDialog.getExistingDirectory(
                self._tab, f"Välj mapp för {len(selected)} mallar"
            )
            if not dest_str:
                return
            dest = Path(dest_str)
            for n in selected:
                self._evt("td.export_started", f"Export startad: {n}",
                          level=logging.DEBUG, template=n)
                worker = _ExportWorker(self._manager, n, dest / f"{n}.zip")
                worker.signals.finished.connect(
                    lambda ok, _n=n: self._on_multi_export_done(ok, _n)
                )
                worker.signals.error.connect(
                    lambda e, _n=n: self._evt(
                        "td.export_failed", f"Export av {_n} misslyckades: {e}",
                        level=logging.ERROR, template=_n, error=e)
                )
                self._pool.start(worker)
            return

        exp_name = selected[0]
        self._evt("td.export_started", f"Export startad: {exp_name}",
                  template=exp_name)
        progress = QProgressDialog(
            f"Exporterar '{exp_name}'...", None, 0, 0, self._tab
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()
        worker = _ExportWorker(self._manager, exp_name, dest)
        worker.signals.finished.connect(
            lambda ok: self._on_export_done(ok, exp_name, dest, progress)
        )
        worker.signals.error.connect(
            lambda e: self._on_export_error(e, progress)
        )
        self._pool.start(worker)

    def _on_multi_export_done(self, ok: bool, name: str) -> None:
        if ok:
            self._evt("td.export_done", f"Export klar: {name}", template=name)
            if self._status_bar:
                self._status_bar.set_success(f"Exporterade: {name}")
        else:
            self._evt("td.export_failed", f"Export misslyckades: {name}",
                      level=logging.ERROR, template=name)

    @Slot(object, str, object, object)
    def _on_export_done(
        self, ok: bool, name: str, dest: Path, progress: QProgressDialog
    ) -> None:
        progress.close()
        if ok:
            self._evt("td.export_done", f"Export klar: {name}", template=name,
                      dest=str(dest))
            if self._status_bar:
                self._status_bar.set_success(f"Exporterade: {name}")
            reply = QMessageBox.question(
                self._tab,
                "Klart",
                "Öppna mappen?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                OSPlatform.open_path(dest.parent)
        else:
            self._evt("td.export_failed", f"Export misslyckades: {name}",
                      level=logging.ERROR, template=name)
            QMessageBox.critical(self._tab, "Fel", "Export misslyckades.")

    @Slot(str, object)
    def _on_export_error(self, error: str, progress: QProgressDialog) -> None:
        progress.close()
        self._evt("td.export_failed", f"Export misslyckades: {error}",
                  level=logging.ERROR, error=error)
        QMessageBox.critical(
            self._tab, "Fel", f"Export misslyckades:\n{error}"
        )

    # ══════════════════════════════════════════════════════════════════
    # HJÄLPMETODER
    # ══════════════════════════════════════════════════════════════════

    def _input_dialog(
        self, title: str, label: str, default: str = ""
    ) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(
            self._tab, title, label, QLineEdit.EchoMode.Normal, default,
        )
        return text.strip(), ok

    @Slot()
    def _on_retry(self) -> None:
        self._evt("td.retry", "Retry: bygger om flik och manager", level=logging.DEBUG)
        self._tab = None
        self._manager = None
        with suppress(Exception):
            self._setup_manager()
        if self._context:
            with suppress(Exception):
                self._context.emit_event(
                    "plugin.reload", {"name": self.name}
                )
