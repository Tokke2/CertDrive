from __future__ import annotations

# plugins/__init__.py
# Intygsgenerator — Centralt plugin-paket.
# Sökväg: Intygsgenerator/plugins/__init__.py
# Discovery, prioritet, event-konstanter, OS-hjälpare,
# UI-komponenter (PySide6), ScrollableWidget, MouseWheelMixin,
# TabManager, WindowManager, PluginDiscovery, PluginUtils.
#
# Version: 5.1.0
# © Rickard Längkvist 2025–2026
#
# Beroenden: Python 3.12+, PySide6
# UTAN Tkinter — alla UI-komponenter använder PySide6.

import importlib
import importlib.util
import inspect
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from functools import lru_cache, wraps
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Final,
    FrozenSet,
    Iterator,
)

if TYPE_CHECKING:
    from core import AppContext, EventBus

# ═════════════════════════════════════════════════════════════
# VERSION & COPYRIGHT
# ═════════════════════════════════════════════════════════════

__version__: Final[str] = "5.2.0"
__version_info__: Final[tuple[int, int, int]] = (5, 1, 0)
__plugin_api__: Final[str] = "3.0"
__author__: Final[str] = "Rickard Längkvist"
__min_python__: Final[tuple[int, int]] = (3, 12)

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

logger = logging.getLogger(__name__)


def get_dynamic_year() -> int:
    """Returnerar aktuellt år."""
    return datetime.now().year


def get_copyright() -> str:
    """Genererar dynamisk copyright-sträng."""
    year = datetime.now().year
    return (
        f"© Rickard Längkvist {_START_YEAR}–{year}"
        if year > _START_YEAR
        else f"© Rickard Längkvist {_START_YEAR}"
    )


# ════════════════════════════════════════════════════════════
# PYTHON VERSION CHECK
# ════════════════════════════════════════════════════════════

if sys.version_info < __min_python__:
    raise RuntimeError(
        f"plugins/__init__.py kräver Python "
        f"{__min_python__[0]}.{__min_python__[1]}+, "
        f"du har {sys.version_info.major}.{sys.version_info.minor}"
    )


# ═════════════════════════════════════════════════════════════
# KONSTANTER
# ═════════════════════════════════════════════════════════════

PLUGIN_DIR: Final[Path] = Path(__file__).parent

IGNORE_FILES: Final[FrozenSet[str]] = frozenset({
    "__init__.py",
    "__pycache__",
    ".DS_Store",
    "thumbs.db",
    ".gitkeep",
    ".gitignore",
})

FOLDER_TEMPLATES: Final[str] = "Mallar"
FOLDER_OUTPUT: Final[str] = "Out"
FOLDER_CONFIG: Final[str] = "js"
FOLDER_ICON: Final[str] = "Icon"
FOLDER_LOGS: Final[str] = "logs"
FOLDER_BACKUP: Final[str] = "Backup"
FOLDER_SENT: Final[str] = "Skickade"


# ═════════════════════════════════════════════════════════════
# PLUGIN PRIORITY
# ═════════════════════════════════════════════════════════════

class PluginPriority:
    """
    Standard plugin-prioriteter.
    Lägre värde = laddas tidigare.
    """

    SYSTEM:            Final[int] = 1
    LOGGING:           Final[int] = 2
    TRAY:              Final[int] = 5
    CORE:              Final[int] = 10
    GENERATOR:         Final[int] = 50
    STATISTICS:        Final[int] = 60
    SETTINGS:          Final[int] = 90
    CERTIFICATES:      Final[int] = 100
    TEMPLATE_DESIGNER: Final[int] = 50
    NORMAL:            Final[int] = 50
    LOW:               Final[int] = 80
    UTILITY:           Final[int] = 90

    @classmethod
    def get_name(cls, priority: int) -> str:
        """Returnerar etikett för prioritetsnivå."""
        for name, value in vars(cls).items():
            if not name.startswith("_") and isinstance(value, int):
                if value == priority:
                    return name
        if priority <= 10:
            return "SYSTEM"
        if priority <= 30:
            return "SETTINGS"
        if priority <= 70:
            return "NORMAL"
        return "LOW"


# ═════════════════════════════════════════════════════════════
# PLUGIN EVENTS
# ═════════════════════════════════════════════════════════════

class PluginEvents:
    """
    Standard event-namn för plugin-kommunikation.
    Använder domain.action-format (snake_case.punkt) per spec.
    """

    # App
    APP_READY:             Final[str] = "app.ready"
    APP_CLOSING:           Final[str] = "app.closing"
    APP_SHUTDOWN:          Final[str] = "app.shutdown"
    APP_MINIMIZE_TO_TRAY:  Final[str] = "tray.minimize"
    APP_RESTORE_FROM_TRAY: Final[str] = "tray.restore"
    APP_THEME_CHANGED:     Final[str] = "settings.theme_changed"

    # Certificates
    CERTIFICATE_GENERATED:      Final[str] = "certificate.generated"
    CERTIFICATE_FAILED:         Final[str] = "certificate.generation_failed"
    CERTIFICATE_BEFORE:         Final[str] = "certificate.before_generate"
    CERTIFICATE_SELECTED:       Final[str] = "certificate.template_selected"
    CERTIFICATES_IMPORTED:      Final[str] = "certificates.imported"
    BATCH_STARTED:              Final[str] = "certificates.batch.started"
    BATCH_COMPLETED:            Final[str] = "certificates.batch.completed"

    # Templates
    TEMPLATE_CREATED:   Final[str] = "template.created"
    TEMPLATE_UPDATED:   Final[str] = "template.updated"
    TEMPLATE_DELETED:   Final[str] = "template.deleted"
    TEMPLATES_SCANNED:  Final[str] = "templates.scanned"
    TEMPLATES_REFRESH:  Final[str] = "refresh_templates"

    # Settings
    SETTINGS_CHANGED:   Final[str] = "settings.changed"
    SETTINGS_SAVED:     Final[str] = "settings.saved"
    PROFILE_LOADED:     Final[str] = "settings.profile_loaded"

    # UI
    TAB_SHOWN:      Final[str] = "tab.shown"
    TAB_HIDDEN:     Final[str] = "tab.hidden"
    TAB_SELECTED:   Final[str] = "tab.selected"
    WINDOW_SHOW:    Final[str] = "window.show"
    WINDOW_HIDE:    Final[str] = "window.hide"

    # Tray
    TRAY_STATUS_UPDATE: Final[str] = "tray.status_update"

    # Statistics
    STATS_GENERATED:  Final[str] = "statistics.certificate_generated"
    STATS_UPDATED:    Final[str] = "statistics.updated"
    STATS_IMPORTED:   Final[str] = "statistics.imported"

    # Generator
    GENERATOR_READY:   Final[str] = "generator.ready"
    GENERATOR_CHANGED: Final[str] = "generator.settings_changed"

    # Plugin Manager
    PLUGIN_MANAGER_READY: Final[str] = "plugin_manager.ready"
    PLUGIN_LOADED:        Final[str] = "plugin.loaded"
    PLUGIN_UNLOADED:      Final[str] = "plugin.unloaded"
    PLUGIN_FAILED:        Final[str] = "plugin.failed"

    # Logging
    LOGGING_SESSION_STARTED: Final[str] = "logging.session_started"
    LOGGING_ERROR:           Final[str] = "logging.error_logged"
    LOGGING_CRASH:           Final[str] = "logging.crash_detected"

    # Bilrutan (FUTURE)
    BILRUTAN_CASE_CREATED:  Final[str] = "bilrutan.case_created"
    BILRUTAN_CASE_SAVED:    Final[str] = "bilrutan.case_saved"
    BILRUTAN_CASE_ARCHIVED: Final[str] = "bilrutan.case_archived"
    BILRUTAN_CASE_SEARCHED: Final[str] = "bilrutan.case_searched"
    BILRUTAN_PDF_GENERATED: Final[str] = "bilrutan.pdf_generated"
    BILRUTAN_PDF_OPENED:    Final[str] = "bilrutan.pdf_opened"
    BILRUTAN_EMAIL_SENT:    Final[str] = "bilrutan.email_sent"

    @classmethod
    def get_all_events(cls) -> list[str]:
        """Returnerar alla definierade events."""
        return [
            v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)
        ]

    @classmethod
    def get_category(cls, event: str) -> str:
        """Returnerar kategori för ett event."""
        return event.split(".")[0] if "." in event else "unknown"


# ═════════════════════════════════════════════════════════════
# OS PLATFORM
# ═════════════════════════════════════════════════════════════

class OSPlatform(Enum):
    """Operativsystemtyper som stöds."""

    WINDOWS = auto()
    MACOS   = auto()
    LINUX   = auto()
    UNKNOWN = auto()

    @classmethod
    @lru_cache(maxsize=1)
    def current(cls) -> OSPlatform:
        """Returnerar aktuellt OS (cachad)."""
        system = platform.system().lower()
        return {
            "windows": cls.WINDOWS,
            "darwin":  cls.MACOS,
            "linux":   cls.LINUX,
        }.get(system, cls.UNKNOWN)

    @property
    def display_name(self) -> str:
        return {
            OSPlatform.WINDOWS: "Windows",
            OSPlatform.MACOS:   "macOS",
            OSPlatform.LINUX:   "Linux",
            OSPlatform.UNKNOWN: "Okänt",
        }.get(self, "Okänt")

    @property
    def emoji(self) -> str:
        return {
            OSPlatform.WINDOWS: "🪟",
            OSPlatform.MACOS:   "",
            OSPlatform.LINUX:   "🐧",
            OSPlatform.UNKNOWN: "",
        }.get(self, "❓")

    @property
    def modifier_symbol(self) -> str:
        return "⌘" if self == OSPlatform.MACOS else "Ctrl"

    @property
    def alt_symbol(self) -> str:
        return "⌥" if self == OSPlatform.MACOS else "Alt"


# ═════════════════════════════════════════════════════════════
# OS HELPER
# ═════════════════════════════════════════════════════════════

class OSHelper:
    """Cross-platform hjälpfunktioner — ingen Tkinter."""

    @staticmethod
    def is_windows() -> bool:
        return OSPlatform.current() == OSPlatform.WINDOWS

    @staticmethod
    def is_macos() -> bool:
        return OSPlatform.current() == OSPlatform.MACOS

    @staticmethod
    def is_linux() -> bool:
        return OSPlatform.current() == OSPlatform.LINUX

    @staticmethod
    def get_platform() -> OSPlatform:
        return OSPlatform.current()

    @classmethod
    def get_config_dir(cls, app_name: str = "Intygsgenerator") -> Path:
        if cls.is_windows():
            base = Path(os.environ.get("APPDATA", str(Path.home())))
        elif cls.is_macos():
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(
                os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
            )
        return base / app_name

    @classmethod
    def open_file(cls, path: str | Path) -> bool:
        """Öppnar fil med standardprogram."""
        p = Path(path)
        if not p.exists():
            return False
        try:
            if cls.is_windows():
                os.startfile(str(p))  # type: ignore[attr-defined]
            elif cls.is_macos():
                subprocess.run(["open", str(p)], check=False)
            else:
                subprocess.run(["xdg-open", str(p)], check=False)
            return True
        except Exception as exc:
            logger.warning("Kunde inte öppna fil: %s", exc)
            return False

    @classmethod
    def open_folder(cls, path: str | Path) -> bool:
        """Öppnar mapp i filhanterare."""
        p = Path(path)
        if p.is_file():
            p = p.parent
        return cls.open_file(p)

    @classmethod
    def get_platform_info(cls) -> dict[str, str]:
        return {
            "system":         platform.system(),
            "release":        platform.release(),
            "version":        platform.version(),
            "machine":        platform.machine(),
            "python_version": platform.python_version(),
            "os_type":        OSPlatform.current().name,
        }


# ═════════════════════════════════════════════════════════════
# PLUGIN DATACLASSES
# ═════════════════════════════════════════════════════════════

@dataclass
class PluginMetadata:
    """Metadata för ett plugin."""

    name:         str
    version:      str        = "1.0.0"
    description:  str        = ""
    author:       str        = ""
    priority:     int        = PluginPriority.NORMAL
    dependencies: list[str]  = field(default_factory=list)
    has_tab:      bool       = True
    tab_visible:  bool       = True
    tab_icon:     str        = "📦"
    tab_title:    str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.tab_icon} {self.tab_title or self.name}"

    @classmethod
    def from_plugin(cls, plugin: Any) -> PluginMetadata:
        return cls(
            name=getattr(plugin, "name", plugin.__class__.__name__),
            version=getattr(plugin, "version", "1.0.0"),
            description=getattr(plugin, "description", ""),
            author=getattr(plugin, "author", __author__),
            priority=getattr(plugin, "priority", PluginPriority.NORMAL),
            dependencies=getattr(plugin, "dependencies", []),
            has_tab=getattr(plugin, "has_tab", True),
            tab_visible=getattr(plugin, "tab_visible", True),
            tab_icon=getattr(plugin, "tab_icon", "📦"),
            tab_title=getattr(plugin, "tab_title", None),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginMetadata:
        return cls(
            name=data.get("name", "Unknown"),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            priority=data.get("priority", PluginPriority.NORMAL),
            dependencies=data.get("dependencies", []),
            has_tab=data.get("has_tab", True),
            tab_visible=data.get("tab_visible", True),
            tab_icon=data.get("tab_icon", "📦"),
            tab_title=data.get("tab_title"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":         self.name,
            "version":      self.version,
            "description":  self.description,
            "author":       self.author,
            "priority":     self.priority,
            "dependencies": self.dependencies,
            "has_tab":      self.has_tab,
            "tab_visible":  self.tab_visible,
            "tab_icon":     self.tab_icon,
            "tab_title":    self.tab_title,
        }


@dataclass
class PluginInfo:
    """Information om ett upptäckt plugin."""

    name:         str
    path:         Path
    module_name:  str
    is_package:   bool                  = False
    metadata:     PluginMetadata | None = None
    load_error:   str | None            = None
    load_time_ms: float                 = 0.0

    @property
    def is_valid(self) -> bool:
        return self.load_error is None

    @property
    def display_name(self) -> str:
        if self.metadata:
            return self.metadata.display_name
        return f"📦 {self.name}"

    @property
    def status_icon(self) -> str:
        return "✅" if self.is_valid else "❌"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":         self.name,
            "path":         str(self.path),
            "module":       self.module_name,
            "is_package":   self.is_package,
            "is_valid":     self.is_valid,
            "error":        self.load_error,
            "load_time_ms": self.load_time_ms,
            "metadata":     self.metadata.to_dict() if self.metadata else None,
        }


@dataclass
class PluginLoadResult:
    """Resultat av plugin-laddning."""

    success:      bool
    plugin:       Any | None          = None
    info:         PluginInfo | None   = None
    error:        str | None          = None
    load_time_ms: float               = 0.0

    @property
    def status_icon(self) -> str:
        return "✅" if self.success else "❌"


# ═════════════════════════════════════════════════════════════
# UI COMPONENTS (PySide6)
# ═════════════════════════════════════════════════════════════

class ScrollableWidget:
    """
    Skapar en scrollbar QScrollArea med mushjul-stöd.
    Wrappas runt valfri QWidget.

    Användning:
        scroll = ScrollableWidget(parent)
        scroll.area.setWidget(my_widget)
        scroll.area.setWidgetResizable(True)
    """

    def __init__(self, parent: Any = None) -> None:
        try:
            from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
            from PySide6.QtCore import Qt

            self._container = QWidget(parent)
            layout = QVBoxLayout(self._container)
            layout.setContentsMargins(0, 0, 0, 0)

            self.area = QScrollArea(self._container)
            self.area.setWidgetResizable(True)
            self.area.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )
            self.area.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )

            layout.addWidget(self.area)
        except ImportError:
            self._container = None
            self.area = None

    @property
    def widget(self) -> Any:
        return self._container


class MouseWheelMixin:
    """
    Mixin för explicit mushjul-stöd i QScrollArea.
    Förhindrar att mushjul triggar zoom i stället för scroll.
    """

    def install_wheel_filter(self, scroll_area: Any) -> None:
        """
        Installerar ett event-filter på scroll_area som
        säkerställer att mushjulet alltid scrollar (aldrig zoomar).

        Args:
            scroll_area: QScrollArea-instans
        """
        try:
            from PySide6.QtCore import QObject, QEvent
            from PySide6.QtWidgets import QScrollArea

            class _WheelFilter(QObject):
                def eventFilter(self, obj: Any, event: Any) -> bool:
                    if event.type() == QEvent.Type.Wheel:
                        delta = event.angleDelta().y()
                        bar = scroll_area.verticalScrollBar()
                        bar.setValue(bar.value() - delta // 3)
                        return True
                    return False

            self._wheel_filter = _WheelFilter(scroll_area)
            scroll_area.viewport().installEventFilter(self._wheel_filter)
        except Exception as exc:
            logger.debug("MouseWheelMixin: %s", exc)


# ═════════════════════════════════════════════════════════════
# TAB MANAGER (PySide6)
# ═════════════════════════════════════════════════════════════

class TabManager:
    """
    Hanterar dynamisk visning/döljning av QTabWidget-flikar.

    Features:
    - Visa/dölj flikar via Visa-meny (QMenu)
    - Högerklick på flik → dölja (context menu)
    - Spara/återställ synlighetsstatus
    - Tab-ordning bevaras vid visa/dölja
    - Kompatibel med core/__init__.py TabManager-API
    """

    def __init__(self, notebook: Any) -> None:
        """
        Skapar TabManager.

        Args:
            notebook: QTabWidget-instans
        """
        self._notebook = notebook
        self._tabs: dict[str, dict[str, Any]] = {}
        self._hidden_tabs: dict[str, tuple[Any, int]] = {}
        self._menu_actions: dict[str, Any] = {}
        self._original_order: list[str] = []

        # Bind högerklick på tab-bar
        try:
            from PySide6.QtCore import Qt
            tab_bar = self._notebook.tabBar()
            tab_bar.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu
            )
            tab_bar.customContextMenuRequested.connect(
                self._on_tab_right_click
            )
        except Exception as exc:
            logger.debug("TabManager: kunde inte binda högerklick: %s", exc)

    # ── Registrering ──────────────────────────────────────

    def add_tab(self, name: str, widget: Any, label: str = "") -> None:
        """
        Registrerar och lägger till en flik.

        Args:
            name:   Unikt flik-ID (internt)
            widget: QWidget för fliken
            label:  Visningstext (default = name)
        """
        display = label or name
        self._tabs[name] = {
            "widget":  widget,
            "label":   display,
            "visible": True,
            "can_hide": True,
        }
        self._original_order.append(name)
        self._notebook.addTab(widget, display)

    def register_tab(
        self,
        name: str,
        widget: Any,
        display_name: str,
        icon: str = "📦",
        visible: bool = True,
        can_hide: bool = True,
    ) -> None:
        """Fullständig registrering med metadata."""
        label = f"{icon} {display_name}" if icon else display_name
        self._tabs[name] = {
            "widget":   widget,
            "label":    label,
            "visible":  visible,
            "can_hide": can_hide,
            "icon":     icon,
            "display":  display_name,
        }
        self._original_order.append(name)
        if visible:
            self._notebook.addTab(widget, label)

    # ── Visa / Dölj ───────────────────────────────────────

    def show_tab(self, name: str) -> bool:
        """Visar en dold flik vid ursprunglig position."""
        if name not in self._hidden_tabs:
            return False

        widget, original_index = self._hidden_tabs.pop(name)
        info = self._tabs.get(name, {})
        count = self._notebook.count()
        insert_at = min(original_index, count)

        self._notebook.insertTab(insert_at, widget, info.get("label", name))
        self._tabs[name]["visible"] = True

        if name in self._menu_actions:
            self._menu_actions[name].setChecked(True)

        return True

    def hide_tab(self, name: str) -> bool:
        """Döljer en flik utan att förstöra widget."""
        if name in self._hidden_tabs:
            return False
        info = self._tabs.get(name)
        if not info or not info.get("can_hide", True):
            return False

        widget = info["widget"]
        for i in range(self._notebook.count()):
            if self._notebook.widget(i) is widget:
                self._hidden_tabs[name] = (widget, i)
                self._notebook.removeTab(i)
                self._tabs[name]["visible"] = False

                if name in self._menu_actions:
                    self._menu_actions[name].setChecked(False)
                return True

        return False

    def toggle_tab(self, name: str) -> bool:
        """Togglar synlighet. Returnerar True om nu synlig."""
        if self.is_visible(name):
            self.hide_tab(name)
            return False
        self.show_tab(name)
        return True

    def select_tab(self, name: str) -> bool:
        """Väljer en synlig flik."""
        info = self._tabs.get(name)
        if not info or not info.get("visible"):
            return False
        widget = info["widget"]
        for i in range(self._notebook.count()):
            if self._notebook.widget(i) is widget:
                self._notebook.setCurrentIndex(i)
                return True
        return False

    # ── Visa-meny (QMenu) ─────────────────────────────────

    def build_view_menu(self, parent_menu: Any) -> Any:
        """
        Bygger en QMenu för Visa-menyn med checkboxar per flik.

        Args:
            parent_menu: QMenu-förälder

        Returns:
            QMenu-objekt
        """
        try:
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction

            view_menu = QMenu("Visa flikar", parent_menu)

            for name, info in self._tabs.items():
                action = QAction(info.get("label", name), view_menu)
                action.setCheckable(True)
                action.setChecked(info.get("visible", True))
                action.setEnabled(info.get("can_hide", True))

                def _toggle(checked: bool, n: str = name) -> None:
                    if checked:
                        self.show_tab(n)
                    else:
                        self.hide_tab(n)

                action.toggled.connect(_toggle)
                view_menu.addAction(action)
                self._menu_actions[name] = action

            view_menu.addSeparator()

            show_all = QAction("Visa alla", view_menu)
            show_all.triggered.connect(self.show_all_tabs)
            view_menu.addAction(show_all)

            reset_action = QAction("Återställ ordning", view_menu)
            reset_action.triggered.connect(self.reset_tab_order)
            view_menu.addAction(reset_action)

            return view_menu
        except Exception as exc:
            logger.warning("build_view_menu: %s", exc)
            return None

    # ── Högerklick (context menu) ─────────────────────────

    def _on_tab_right_click(self, pos: Any) -> None:
        """Visar context menu vid högerklick på tab-bar."""
        try:
            from PySide6.QtWidgets import QMenu
            from PySide6.QtGui import QAction

            tab_bar = self._notebook.tabBar()
            clicked_index = tab_bar.tabAt(pos)
            if clicked_index < 0:
                return

            clicked_widget = self._notebook.widget(clicked_index)
            tab_name: str | None = None
            for name, info in self._tabs.items():
                if info["widget"] is clicked_widget:
                    tab_name = name
                    break

            if tab_name is None:
                return

            menu = QMenu(self._notebook)

            info = self._tabs[tab_name]
            if info.get("can_hide", True):
                hide_action = QAction(
                    f"Dölj '{info.get('display', tab_name)}'", menu
                )
                hide_action.triggered.connect(
                    lambda: self.hide_tab(tab_name)
                )
                menu.addAction(hide_action)

            if self._hidden_tabs:
                menu.addSeparator()
                for hname in list(self._hidden_tabs):
                    hinfo = self._tabs.get(hname, {})
                    act = QAction(
                        f"Visa '{hinfo.get('display', hname)}'", menu
                    )
                    act.triggered.connect(
                        lambda _=False, n=hname: self.show_tab(n)
                    )
                    menu.addAction(act)

            menu.exec(tab_bar.mapToGlobal(pos))
        except Exception as exc:
            logger.debug("Tab context menu: %s", exc)

    # ── Bulk-operationer ──────────────────────────────────

    def show_all_tabs(self) -> None:
        """Visar alla dolda flikar."""
        for name in list(self._hidden_tabs):
            self.show_tab(name)

    def reset_tab_order(self) -> None:
        """Återställer flikar i ursprunglig ordning."""
        visible = [n for n in self._tabs if self._tabs[n]["visible"]]
        for name in visible:
            self.hide_tab(name)
        for name in self._original_order:
            if name in self._hidden_tabs:
                self.show_tab(name)

    # ── Frågor ────────────────────────────────────────────

    def is_visible(self, name: str) -> bool:
        if name not in self._tabs:
            return False
        return self._tabs[name].get("visible", False)

    def get_all_names(self) -> list[str]:
        return list(self._tabs)

    def get_visible_names(self) -> list[str]:
        return [n for n in self._tabs if self._tabs[n].get("visible")]

    def get_hidden_names(self) -> list[str]:
        return list(self._hidden_tabs)

    def get_tab_count(self) -> int:
        return len(self._tabs)

    def get_visible_count(self) -> int:
        return len(self.get_visible_names())

    # ── Spara/återställ status ────────────────────────────

    def save_state(self) -> dict[str, bool]:
        return {n: i.get("visible", True) for n, i in self._tabs.items()}

    def restore_state(self, state: dict[str, bool]) -> None:
        for name, visible in state.items():
            if visible and name in self._hidden_tabs:
                self.show_tab(name)
            elif not visible and name not in self._hidden_tabs:
                self.hide_tab(name)


# ═════════════════════════════════════════════════════════════
# WINDOW MANAGER (PySide6)
# ═════════════════════════════════════════════════════════════

class WindowManager:
    """
    Hanterar fönsterstorlek, position och tillstånd.
    PySide6-version utan Tkinter.

    Features:
    - Optimal storlek baserat på skärm (QScreen)
    - HiDPI / DPI-aware
    - Spara/återställ geometri
    - Minimera till tray
    - Centrera på skärm
    """

    def __init__(self, window: Any, config: Any = None) -> None:
        """
        Skapar WindowManager.

        Args:
            window: QMainWindow-instans
            config: ConfigManager (valfri, för persistens)
        """
        self.window = window
        self._config = config
        self._is_maximized = False
        self._save_pending: str | None = None

    # ─ Setup ──────────────────────────────────────────────

    def setup_window(
        self,
        title: str = "Intygsgenerator",
        start_minimized: bool = False,
    ) -> None:
        """
        Konfigurerar fönstret vid start.

        Args:
            title:           Fönstertitel
            start_minimized: Starta dolt (tray)
        """
        self.window.setWindowTitle(title)
        self.center_window()
        self.restore_geometry()

        if start_minimized:
            self.window.hide()
        else:
            self.window.show()

    def center_window(self) -> None:
        """Centrerar fönstret på primärskärmen."""
        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            win_geo = self.window.frameGeometry()
            win_geo.moveCenter(geo.center())
            self.window.move(win_geo.topLeft())
        except Exception as exc:
            logger.debug("center_window: %s", exc)

    # ── Geometri ───────────────────────────────────────────

    def save_geometry(self) -> None:
        """Sparar fönstergeometri till config."""
        if not self._config:
            return
        try:
            geo = self.window.saveGeometry()
            self._config.set(
                "window.geometry",
                geo.toBase64().data().decode(),
            )
            self._config.set(
                "window.maximized", self._is_maximized
            )
        except Exception as exc:
            logger.debug("save_geometry: %s", exc)

    def restore_geometry(self) -> None:
        """Återställer fönstergeometri från config."""
        if not self._config:
            return
        try:
            from PySide6.QtCore import QByteArray
            geo_str = self._config.get("window.geometry", "")
            if geo_str:
                geo = QByteArray.fromBase64(geo_str.encode())
                self.window.restoreGeometry(geo)
            if self._config.get("window.maximized", False):
                self.window.showMaximized()
        except Exception as exc:
            logger.debug("restore_geometry: %s", exc)

    # ── Synlighet ──────────────────────────────────────────

    def minimize_to_tray(self) -> None:
        """Döljer fönstret (till systray)."""
        self.window.hide()

    def restore_from_tray(self) -> None:
        """Visar fönstret igen från tray."""
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def toggle_visibility(self) -> bool:
        """Togglar fönstrets synlighet. Returnerar True om nu synligt."""
        if self.window.isVisible():
            self.minimize_to_tray()
            return False
        self.restore_from_tray()
        return True

    def is_visible(self) -> bool:
        return self.window.isVisible()

    def is_maximized(self) -> bool:
        return self._is_maximized

    # ── DPI ───────────────────────────────────────────────

    @staticmethod
    def get_scale_factor() -> float:
        """Returnerar DPI-skalfaktor för primärskärmen."""
        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                return screen.devicePixelRatio()
        except Exception:
            pass
        return 1.0

    @staticmethod
    def get_optimal_size(
        max_ratio: float = 0.85,
        min_width: int = 1024,
        min_height: int = 700,
    ) -> tuple[int, int]:
        """
        Beräknar optimal fönsterstorlek.

        Args:
            max_ratio:  Max andel av skärmen
            min_width:  Minsta bredd
            min_height: Minsta höjd

        Returns:
            (width, height)
        """
        try:
            from PySide6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                w = max(min_width, int(geo.width() * max_ratio))
                h = max(min_height, int(geo.height() * max_ratio))
                return w, h
        except Exception:
            pass
        return min_width, min_height

    @staticmethod
    def scale_value(value: int) -> int:
        """Skalar ett pixel-värde med DPI-faktor."""
        return int(value * WindowManager.get_scale_factor())


# ═════════════════════════════════════════════════════════════
# PLUGIN DISCOVERY
# ═════════════════════════════════════════════════════════════

class PluginDiscovery:
    """
    Upptäcker och validerar plugins i plugin-katalogen.
    Skannar efter paket (mappar med __init__.py) och
    enstaka .py-filer med Plugin-klass.
    """

    def __init__(self, plugin_dir: Path = PLUGIN_DIR) -> None:
        self._plugin_dir = plugin_dir
        self._log = logging.getLogger(f"{__name__}.PluginDiscovery")

    def discover_all(self) -> list[PluginInfo]:
        """Returnerar lista med alla hittade plugins."""
        result: list[PluginInfo] = []
        if not self._plugin_dir.exists():
            self._log.warning(
                "Plugin-katalog saknas: %s", self._plugin_dir
            )
            return result

        # Paket (mappar med __init__.py)
        for folder in sorted(self._plugin_dir.iterdir()):
            if not folder.is_dir():
                continue
            if folder.name.startswith("_"):
                continue
            if folder.name.lower() in IGNORE_FILES:
                continue
            if (folder / "__init__.py").exists():
                result.append(PluginInfo(
                    name=folder.name,
                    path=folder,
                    module_name=f"plugins.{folder.name}",
                    is_package=True,
                ))

        # Enstaka .py-filer
        for py in sorted(self._plugin_dir.glob("*.py")):
            if py.name.lower() in IGNORE_FILES:
                continue
            if py.name.startswith("_"):
                continue
            result.append(PluginInfo(
                name=py.stem,
                path=py,
                module_name=f"plugins.{py.stem}",
                is_package=False,
            ))

        self._log.debug(
            "Hittade %d plugin-kandidater", len(result)
        )
        return result

    def validate_plugin(self, info: PluginInfo) -> PluginInfo:
        """Validerar att ett plugin är importerbart och har Plugin-klass."""
        t0 = time.time()
        try:
            spec = importlib.util.find_spec(info.module_name)
            if spec is None:
                info.load_error = f"Modul '{info.module_name}' saknas"
                return info

            module = importlib.import_module(info.module_name)

            if not hasattr(module, "Plugin"):
                info.load_error = "Ingen 'Plugin'-klass hittades"
                return info

            plugin_cls = getattr(module, "Plugin")
            if not inspect.isclass(plugin_cls):
                info.load_error = "'Plugin' är inte en klass"
                return info

            if hasattr(module, "get_plugin_info"):
                try:
                    pinfo = module.get_plugin_info()
                    info.metadata = PluginMetadata.from_dict(pinfo)
                except Exception:
                    info.metadata = PluginMetadata(name=info.name)
            else:
                info.metadata = PluginMetadata.from_plugin(plugin_cls)

        except Exception as exc:
            info.load_error = str(exc)
            self._log.warning(
                "Validering misslyckades för %s: %s", info.name, exc
            )
        finally:
            info.load_time_ms = (time.time() - t0) * 1000
        return info

    def get_load_order(self, plugins: list[PluginInfo]) -> list[PluginInfo]:
        """Sorterar plugins efter prioritet (lägst = laddas först)."""
        def _prio(p: PluginInfo) -> int:
            if p.metadata:
                return p.metadata.priority
            return PluginPriority.NORMAL
        return sorted(plugins, key=_prio)

    def discover_and_validate(self) -> list[PluginInfo]:
        """Kombinerad discovery + validering + sortering.

        5.2.0: strukturerade pi.*-events (discovery_done/plugin_invalid)
        så att hela plugin-laddningen är AI-felsökbar i centralloggen.
        """
        import time as _t

        started = _t.perf_counter()
        all_plugins = self.discover_all()
        validated = [self.validate_plugin(p) for p in all_plugins]
        failed = [p.name for p in validated if p.load_error]
        logger.info(
            "Discovery klar: %d plugins, %d med fel",
            len(validated), len(failed),
            extra={"event": "pi.discovery_done",
                   "ctx": {"found": len(validated), "failed": failed,
                           "ms": round((_t.perf_counter() - started) * 1000, 1)}},
        )
        for plugin_info in validated:
            if plugin_info.load_error:
                logger.warning(
                    "Plugin ogiltig: %s: %s",
                    plugin_info.name, plugin_info.load_error,
                    extra={"event": "pi.plugin_invalid",
                           "ctx": {"plugin": plugin_info.name,
                                   "error": str(plugin_info.load_error)}},
                )
        return self.get_load_order(validated)


# ════════════════════════════════════════════════════════════
# PLUGIN UTILITIES
# ════════════════════════════════════════════════════════════

class PluginUtils:
    """Hjälpfunktioner för plugin-utveckling."""

    @staticmethod
    def safe_emit(
        context: Any, event: str, data: Any = None
    ) -> bool:
        """Skickar event säkert via context."""
        try:
            if hasattr(context, "emit_event"):
                context.emit_event(event, data)
                return True
            if hasattr(context, "event_bus"):
                context.event_bus.emit(event, data)
                return True
        except Exception as exc:
            logger.debug("safe_emit '%s': %s", event, exc)
        return False

    @staticmethod
    def safe_subscribe(
        context: Any,
        event: str,
        callback: Callable[..., Any],
    ) -> bool:
        """Prenumererar på event säkert."""
        try:
            if hasattr(context, "subscribe"):
                context.subscribe(event, callback)
                return True
            if hasattr(context, "event_bus"):
                context.event_bus.subscribe(event, callback)
                return True
        except Exception as exc:
            logger.debug("safe_subscribe '%s': %s", event, exc)
        return False

    @staticmethod
    def safe_unsubscribe(
        context: Any,
        event: str,
        callback: Callable[..., Any],
    ) -> bool:
        """Avprenumererar från event säkert."""
        try:
            if hasattr(context, "event_bus"):
                context.event_bus.unsubscribe(event, callback)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def get_plugin(
        context: Any, name: str
    ) -> Any | None:
        """Hämtar ett plugin säkert."""
        try:
            if hasattr(context, "get_plugin"):
                return context.get_plugin(name)
            if hasattr(context, "_plugin_manager"):
                pm = context._plugin_manager
                if pm and hasattr(pm, "get_plugin"):
                    return pm.get_plugin(name)
        except Exception:
            pass
        return None

    @staticmethod
    def debounce(wait_ms: int) -> Callable[..., Any]:
        """Debounce-decorator (begränsar anrop, thread-safe)."""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            lock = threading.Lock()
            timer: list[threading.Timer | None] = [None]

            @wraps(func)
            def debounced(*args: Any, **kwargs: Any) -> None:
                with lock:
                    if timer[0] is not None:
                        timer[0].cancel()
                    t = threading.Timer(
                        wait_ms / 1000,
                        lambda: func(*args, **kwargs),
                    )
                    timer[0] = t
                    t.start()

            return debounced
        return decorator

    @staticmethod
    def throttle(wait_ms: int) -> Callable[..., Any]:
        """Throttle-decorator (begränsar frekvens, thread-safe)."""
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            lock = threading.Lock()
            last_call: list[float] = [0.0]

            @wraps(func)
            def throttled(*args: Any, **kwargs: Any) -> Any:
                with lock:
                    now = time.time()
                    if (now - last_call[0]) * 1000 >= wait_ms:
                        last_call[0] = now
                        return func(*args, **kwargs)
                return None

            return throttled
        return decorator


# ═════════════════════════════════════════════════════════════
# PUBLIKA FUNKTIONER
# ═════════════════════════════════════════════════════════════

def get_available_plugins() -> list[str]:
    """Returnerar namn på alla tillgängliga plugins."""
    return [p.name for p in PluginDiscovery().discover_all()]


def get_plugin_info_list() -> list[dict[str, Any]]:
    """Returnerar detaljerad info om alla plugins."""
    return [p.to_dict() for p in PluginDiscovery().discover_and_validate()]


def discover_plugins() -> list[PluginInfo]:
    """Returnerar sorterad lista med PluginInfo."""
    return PluginDiscovery().discover_and_validate()


def get_os_info() -> dict[str, Any]:
    """Returnerar OS-information."""
    current = OSPlatform.current()
    return {
        "platform":       current.name,
        "display_name":   current.display_name,
        "emoji":          current.emoji,
        "modifier":       current.modifier_symbol,
        "is_windows":     OSHelper.is_windows(),
        "is_macos":       OSHelper.is_macos(),
        "is_linux":       OSHelper.is_linux(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}"
            f".{sys.version_info.micro}"
        ),
        "platform_info":  OSHelper.get_platform_info(),
    }


def get_copyright_info() -> dict[str, Any]:
    """Returnerar copyright-information."""
    return {
        "copyright":  COPYRIGHT,
        "author":     __author__,
        "base_year":  _START_YEAR,
        "cur_year":   _CURRENT_YEAR,
        "version":    __version__,
        "plugin_api": __plugin_api__,
    }


# ═════════════════════════════════════════════════════════════
# EXPORTS
# ═════════════════════════════════════════════════════════════

__all__: list[str] = [
    # Metadata
    "__version__",
    "__version_info__",
    "__plugin_api__",
    "__author__",
    "COPYRIGHT",
    "get_copyright",
    "get_dynamic_year",
    "get_copyright_info",
    # Konstanter
    "PLUGIN_DIR",
    "IGNORE_FILES",
    "FOLDER_TEMPLATES",
    "FOLDER_OUTPUT",
    "FOLDER_CONFIG",
    "FOLDER_ICON",
    "FOLDER_LOGS",
    "FOLDER_BACKUP",
    "FOLDER_SENT",
    # Prioritet & Events
    "PluginPriority",
    "PluginEvents",
    # OS
    "OSPlatform",
    "OSHelper",
    # Dataclasses
    "PluginMetadata",
    "PluginInfo",
    "PluginLoadResult",
    # UI
    "MouseWheelMixin",
    "ScrollableWidget",
    "TabManager",
    "WindowManager",
    # Discovery
    "PluginDiscovery",
    # Utilities
    "PluginUtils",
    # Funktioner
    "get_available_plugins",
    "get_plugin_info_list",
    "discover_plugins",
    "get_os_info",
]


# ═════════════════════════════════════════════════════════════
# MODUL-INIT
# ═════════════════════════════════════════════════════════════

logger.debug(
    "plugins/__init__.py v%s laddad (%s, Python %s.%s)",
    __version__,
    OSPlatform.current().display_name,
    sys.version_info.major,
    sys.version_info.minor,
)
