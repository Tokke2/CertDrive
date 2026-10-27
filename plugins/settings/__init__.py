from __future__ import annotations

"""
Fil:    plugins/settings/__init__.py
Sökväg: Intygsgenerator/plugins/settings/__init__.py

Intygsgenerator — paketgränssnitt för Settings-pluginet.

Exporterar den Plugin-klass som PluginManager förväntar sig och behåller ett
synligt, fail-safe degraded mode om settings/core.py eller settings/plugin.py
inte kan laddas.
"""

import logging
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

__version__: str = "9.0.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

PLUGIN_NAME: str = "Inställningar"
PLUGIN_PRIORITY: int = 90

_logger: logging.Logger = logging.getLogger(__name__)
_load_lock = threading.RLock()

CORE_LOADED: bool = False
PLUGIN_LOADED: bool = False
CORE_ERROR: str = ""
PLUGIN_ERROR: str = ""

try:
    from .core import (
        AutostartManager,
        COPYRIGHT as _CORE_COPYRIGHT,
        HealthCheck,
        HealthStatus,
        Language,
        PlatformHelper,
        Profile,
        SettingDefinition,
        SettingSection,
        Shortcut,
        ShortcutManager,
        TabInfo,
        Theme,
        ThemeManager,
    )

    COPYRIGHT = _CORE_COPYRIGHT
    CORE_LOADED = True
except Exception as exc:
    CORE_ERROR = f"{type(exc).__name__}: {exc}"
    _logger.critical(
        "[SETTINGS] core.py kunde inte laddas; degraded mode aktiveras: %s",
        exc,
        exc_info=True,
    )

try:
    from .plugin import Plugin

    PLUGIN_LOADED = True
except Exception as exc:
    PLUGIN_ERROR = f"{type(exc).__name__}: {exc}"
    _logger.critical(
        "[SETTINGS] plugin.py kunde inte laddas; fallback-plugin används: %s",
        exc,
        exc_info=True,
    )

    class Plugin:
        """Synlig fallback när Settings UI inte kan importeras."""

        name: str = PLUGIN_NAME
        version: str = __version__
        description: str = "Inställningar är tillfälligt otillgängliga"
        priority: int = PLUGIN_PRIORITY

        def __init__(self) -> None:
            self._context: Any | None = None
            self._tab: QWidget | None = None
            self._lock = threading.RLock()

        def on_load(self, context: Any) -> None:
            with self._lock:
                self._context = context
            _logger.error("[SETTINGS] Fallback-plugin aktiv: %s", PLUGIN_ERROR)
            emitter = getattr(context, "emit_event", None)
            if callable(emitter):
                try:
                    emitter(
                        "error.occurred",
                        {
                            "source": "settings",
                            "message": f"Settings-plugin kunde inte laddas: {PLUGIN_ERROR}",
                            "level": "error",
                        },
                    )
                except Exception:
                    _logger.exception("[SETTINGS] Kunde inte emittera fallback-fel")

        def setup(self, context: Any) -> None:
            self.on_load(context)

        def on_unload(self) -> None:
            with self._lock:
                self._context = None
                self._tab = None

        def on_close(self) -> None:
            self.on_unload()

        def get_tab(self) -> QWidget | None:
            with self._lock:
                if self._tab is not None:
                    return self._tab
            try:
                from PySide6.QtCore import Qt
                from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

                widget = QWidget()
                layout = QVBoxLayout(widget)
                layout.setContentsMargins(32, 32, 32, 32)
                layout.setSpacing(12)
                layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                title = QLabel("⚠️ Inställningar kunde inte laddas", widget)
                title_font = title.font()
                title_font.setBold(True)
                title_font.setPointSize(max(14, title_font.pointSize() + 3))
                title.setFont(title_font)
                title.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(title)
                details = QLabel(f"Detalj: {PLUGIN_ERROR}", widget)
                details.setWordWrap(True)
                details.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(details)
                with self._lock:
                    self._tab = widget
                return widget
            except Exception:
                _logger.exception("[SETTINGS] Kunde inte bygga fallback-flik")
                return None


PLUGIN_INFO: dict[str, Any] = {
    "name": PLUGIN_NAME,
    "version": __version__,
    "author": __author__,
    "description": "Centrala inställningar med profiler, teman, genvägar, backup och diagnostik",
    "copyright": COPYRIGHT,
    "has_tab": True,
    "tab_priority": PLUGIN_PRIORITY,
    "priority": PLUGIN_PRIORITY,
    "requires": [],
    "optional": [],
    "platforms": ["windows", "darwin", "linux"],
    "min_python": "3.12",
    "core_loaded": CORE_LOADED,
    "plugin_loaded": PLUGIN_LOADED,
    "core_error": CORE_ERROR,
    "plugin_error": PLUGIN_ERROR,
    "events": {
        "emits": [
            "settings.changed",
            "settings.theme_changed",
            "settings.profile_loaded",
            "app.quit_confirmed",
        ],
        "listens_to": [
            "show_settings",
            "refresh_ui",
            "plugin.loaded",
            "plugin.unloaded",
            "settings.theme_changed",
        ],
    },
}


def get_plugin_class() -> type[Any]:
    """Returnera plugin-klassen för PluginManager discovery."""
    return Plugin


def get_copyright() -> str:
    return COPYRIGHT


def get_plugin_info() -> dict[str, Any]:
    """Returnera metadata med aktuell importstatus."""
    with _load_lock:
        info = dict(PLUGIN_INFO)
        info.update(
            {
                "core_loaded": CORE_LOADED,
                "plugin_loaded": PLUGIN_LOADED,
                "core_error": CORE_ERROR,
                "plugin_error": PLUGIN_ERROR,
                "fully_loaded": CORE_LOADED and PLUGIN_LOADED,
                "plugin_class": Plugin,
            }
        )
        return info


def is_fully_loaded() -> bool:
    with _load_lock:
        return CORE_LOADED and PLUGIN_LOADED


def is_core_loaded() -> bool:
    with _load_lock:
        return CORE_LOADED


def is_plugin_loaded() -> bool:
    with _load_lock:
        return PLUGIN_LOADED


def get_load_errors() -> dict[str, str]:
    with _load_lock:
        return {
            key: value
            for key, value in {"core": CORE_ERROR, "plugin": PLUGIN_ERROR}.items()
            if value
        }


__all__: list[str] = [
    "__version__",
    "__author__",
    "Plugin",
    "PLUGIN_NAME",
    "PLUGIN_PRIORITY",
    "COPYRIGHT",
    "PLUGIN_INFO",
    "get_plugin_class",
    "get_copyright",
    "get_plugin_info",
    "is_fully_loaded",
    "is_core_loaded",
    "is_plugin_loaded",
    "get_load_errors",
]

if CORE_LOADED:
    __all__ += [
        "Theme",
        "Language",
        "HealthStatus",
        "SettingSection",
        "SettingDefinition",
        "Profile",
        "HealthCheck",
        "Shortcut",
        "TabInfo",
        "ThemeManager",
        "ShortcutManager",
        "AutostartManager",
        "PlatformHelper",
    ]

_logger.debug(
    "[SETTINGS] v%s | core=%s | plugin=%s",
    __version__,
    "OK" if CORE_LOADED else f"FEL: {CORE_ERROR}",
    "OK" if PLUGIN_LOADED else f"FEL: {PLUGIN_ERROR}",
)
