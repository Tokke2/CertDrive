from __future__ import annotations
# -*- coding: utf-8 -*-

"""
Fil:       plugins/system_tray/__init__.py
Sökväg:    Intygsgenerator/plugins/system_tray/__init__.py
Projekt:   Intygsgenerator
Version:   8.4.1
Ansvar:    Paketinitiering, metadata och säker export för system_tray-pluginet.

System tray-pluginet äger den enda QSystemTrayIcon-instansen i applikationen.
Detta paket exponerar modeller, metadata och Plugin-klassen med fail-safe
fallback om models.py eller plugin.py inte kan importeras.

Copyright hanteras dynamiskt via COPYRIGHT-konstanten.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

__version__: str = "9.3.0"
__author__: str = "Rickard Längkvist"

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

_LOGGER: logging.Logger = logging.getLogger(f"{__name__}.package")
_LOAD_LOCK = threading.RLock()
_LOAD_ERRORS: dict[str, str] = {}
_MODELS_LOADED: bool = False
_PLUGIN_LOADED: bool = False

# ═════════════════════════════════════════════════════════════════════════════
# MJUK IMPORT — MODELS
# ═════════════════════════════════════════════════════════════════════════════

try:
    from .models import (
        ANIMATION_SPEED_MS,
        DEFAULT_HOTKEY,
        DEFAULT_WINDOW_HEIGHT,
        DEFAULT_WINDOW_WIDTH,
        ICON_SIZE,
        ICON_SIZES,
        MAX_RECENT_CERTIFICATES,
        MIN_WINDOW_HEIGHT,
        MIN_WINDOW_WIDTH,
        NOTIFICATION_DURATION_MS,
        PLUGIN_AUTHOR,
        PLUGIN_DESCRIPTION,
        PLUGIN_NAME,
        PLUGIN_VERSION,
        TOOLTIP_BUSY,
        TOOLTIP_MINIMIZED,
        TOOLTIP_NORMAL,
        TRAY_CLICK_THRESHOLD_MS,
        WINDOW_SCREEN_SCALE,
        CloseAction,
        IconGenerator,
        IconState,
        Notification,
        NotificationType,
        Platform,
        RecentCertificate,
        TabInfo,
        TrayConfigHelper,
        get_tooltip_busy,
        get_tooltip_minimized,
        get_tooltip_normal,
    )

    _MODELS_LOADED = True
except Exception as exc:
    _LOAD_ERRORS["models"] = f"{type(exc).__name__}: {exc}"
    _LOGGER.exception("System tray models kunde inte laddas, fallback aktiveras")

    PLUGIN_NAME: str = "System Tray"
    PLUGIN_VERSION: str = __version__
    PLUGIN_AUTHOR: str = __author__
    PLUGIN_DESCRIPTION: str = "System tray, notiser och fönsterstyrning"

    MAX_RECENT_CERTIFICATES: int = 10
    ANIMATION_SPEED_MS: int = 180
    NOTIFICATION_DURATION_MS: int = 5000
    TRAY_CLICK_THRESHOLD_MS: int = 450
    ICON_SIZE: int = 64
    ICON_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)
    DEFAULT_HOTKEY: str = "Ctrl+Alt+I"

    MIN_WINDOW_WIDTH: int = 900
    MIN_WINDOW_HEIGHT: int = 650
    DEFAULT_WINDOW_WIDTH: int = 1200
    DEFAULT_WINDOW_HEIGHT: int = 800
    WINDOW_SCREEN_SCALE: float = 0.82

    TOOLTIP_NORMAL: str = "Intygsgenerator"
    TOOLTIP_MINIMIZED: str = "Intygsgenerator körs i bakgrunden"
    TOOLTIP_BUSY: str = "Intygsgenerator arbetar"

    class Platform(Enum):
        """Plattformar som stöds av tray-pluginet."""

        WINDOWS = "windows"
        MACOS = "darwin"
        LINUX = "linux"
        UNKNOWN = "unknown"

        @classmethod
        def current(cls) -> Platform:
            import sys

            if sys.platform.startswith("win"):
                return cls.WINDOWS
            if sys.platform == "darwin":
                return cls.MACOS
            if sys.platform.startswith("linux"):
                return cls.LINUX
            return cls.UNKNOWN

        @property
        def display_name(self) -> str:
            names = {
                Platform.WINDOWS: "Windows",
                Platform.MACOS: "macOS",
                Platform.LINUX: "Linux",
                Platform.UNKNOWN: "Okänd",
            }
            return names[self]

    class IconState(Enum):
        """Ikonstatus för tray-ikonen."""

        NORMAL = "normal"
        MINIMIZED = "minimized"
        BUSY = "busy"
        ERROR = "error"
        WARNING = "warning"

    class NotificationType(Enum):
        """Typer av systemnotiser."""

        INFO = "info"
        SUCCESS = "success"
        WARNING = "warning"
        ERROR = "error"

    class CloseAction(Enum):
        """Åtgärd när huvudfönstret stängs."""

        MINIMIZE_TO_TRAY = "minimize_to_tray"
        ASK = "ask"
        EXIT = "exit"

    @dataclass(slots=True)
    class Notification:
        """Fallback-modell för tray-notiser."""

        title: str
        message: str
        type: NotificationType = NotificationType.INFO
        duration_ms: int = NOTIFICATION_DURATION_MS
        timestamp: datetime = field(default_factory=datetime.now)

        def to_dict(self) -> dict[str, Any]:
            return {
                "title": self.title,
                "message": self.message,
                "type": self.type.value,
                "duration_ms": self.duration_ms,
                "timestamp": self.timestamp.isoformat(),
            }

    @dataclass(slots=True)
    class RecentCertificate:
        """Fallback-modell för senast genererade intyg."""

        name: str
        path: Path
        template: str = ""
        number: int = 0
        created_at: datetime = field(default_factory=datetime.now)

        def to_dict(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "path": str(self.path),
                "template": self.template,
                "number": self.number,
                "created_at": self.created_at.isoformat(),
            }

    @dataclass(slots=True)
    class TabInfo:
        """Fallback-modell för tabbinformation."""

        name: str
        label: str
        visible: bool = True
        icon: str = ""

        def to_dict(self) -> dict[str, Any]:
            return {
                "name": self.name,
                "label": self.label,
                "visible": self.visible,
                "icon": self.icon,
            }

    class TrayConfigHelper:
        """Fallback-hjälpare för tray-relaterade inställningar."""

        @staticmethod
        def get_bool(config: Any, key: str, default: bool = False) -> bool:
            getter = getattr(config, "get", None)
            if callable(getter):
                try:
                    return bool(getter(key, default))
                except Exception:
                    _LOGGER.exception("Kunde inte läsa tray-inställning %s", key)
            return default

        @staticmethod
        def set_bool(config: Any, key: str, value: bool) -> bool:
            setter = getattr(config, "set", None)
            if callable(setter):
                try:
                    setter(key, bool(value))
                    return True
                except Exception:
                    _LOGGER.exception("Kunde inte skriva tray-inställning %s", key)
            return False

        @staticmethod
        def should_start_minimized(config: Any) -> bool:
            return TrayConfigHelper.get_bool(config, "tray.start_minimized", True)

        @staticmethod
        def should_close_to_tray(config: Any) -> bool:
            return TrayConfigHelper.get_bool(config, "tray.close_to_tray", True)

    class IconGenerator:
        """Fallback-ikongenerator som använder QPainter när PySide6 finns."""

        @staticmethod
        def create_icon(size: int = ICON_SIZE) -> Any:
            try:
                from PySide6.QtCore import Qt
                from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

                pixmap = QPixmap(size, size)
                pixmap.fill(Qt.GlobalColor.transparent)

                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setBrush(QColor(0, 120, 212))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(2, 2, size - 4, size - 4)

                painter.setPen(QColor(255, 255, 255))
                font = painter.font()
                font.setBold(True)
                font.setPointSize(max(10, int(size * 0.45)))
                painter.setFont(font)
                painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "I")
                painter.end()

                return QIcon(pixmap)
            except Exception:
                _LOGGER.exception("Fallback-ikon kunde inte skapas")
                return None

    def get_tooltip_normal() -> str:
        return TOOLTIP_NORMAL

    def get_tooltip_minimized() -> str:
        return TOOLTIP_MINIMIZED

    def get_tooltip_busy() -> str:
        return TOOLTIP_BUSY

# Säkerställ att paketets copyright alltid följer aktuell filstandard.
PLUGIN_VERSION = __version__
PLUGIN_AUTHOR = "Rickard Längkvist"
COPYRIGHT = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

# ═════════════════════════════════════════════════════════════════════════════
# MJUK IMPORT — PLUGIN
# ═════════════════════════════════════════════════════════════════════════════

try:
    from .plugin import Plugin as _RealPlugin

    Plugin = _RealPlugin
    _PLUGIN_LOADED = True
except Exception as exc:
    _LOAD_ERRORS["plugin"] = f"{type(exc).__name__}: {exc}"
    _LOGGER.exception("System tray plugin kunde inte laddas, fallback aktiveras")

    class Plugin:
        """
        Fallback-plugin om .plugin inte kan laddas.

        Appen fortsätter alltid även om system tray misslyckas.
        """

        name: str = PLUGIN_NAME
        version: str = PLUGIN_VERSION
        description: str = f"{PLUGIN_DESCRIPTION} (fallback-läge)"
        priority: int = 5

        def __init__(self) -> None:
            self._context: Any = None
            self._log = logging.getLogger(f"{PLUGIN_NAME}.Fallback")
            self._unsub_fns: list[Any] = []
            self._lock = threading.RLock()

        def on_load(self, context: Any) -> None:
            with self._lock:
                self._context = context
                try:
                    self._log = context.get_logger("SystemTrayFallback")
                except Exception:
                    self._log.exception(
                        "[SYSTEM_TRAY] Kunde inte hämta context-logger"
                    )

                self._subscribe_fallback_events(context)
                self._log.error(
                    "[SYSTEM_TRAY] plugin.py kunde inte laddas: %s",
                    _LOAD_ERRORS.get("plugin", "okänt fel"),
                )

        def setup(self, context: Any) -> None:
            self.on_load(context)

        def on_unload(self) -> None:
            with self._lock:
                for unsubscribe in self._unsub_fns:
                    try:
                        unsubscribe()
                    except Exception:
                        self._log.exception(
                            "[SYSTEM_TRAY] Kunde inte avregistrera event"
                        )
                self._unsub_fns.clear()
                self._context = None

        def on_close(self) -> None:
            self.on_unload()

        def get_tab(self) -> Any | None:
            return None

        def get_statistics(self) -> dict[str, Any]:
            return {
                "available": False,
                "fallback": True,
                "models_loaded": _MODELS_LOADED,
                "plugin_loaded": _PLUGIN_LOADED,
                "load_errors": dict(_LOAD_ERRORS),
            }

        def _subscribe_fallback_events(self, context: Any) -> None:
            event_bus = getattr(context, "event_bus", None)
            subscribe = getattr(event_bus, "subscribe", None)
            if not callable(subscribe):
                return

            for event_name in (
                "tray.status_update",
                "tray.minimize",
                "tray.restore",
                "notification",
                "app.shutdown",
            ):
                try:
                    unsubscribe = subscribe(event_name, self._on_event)
                    if callable(unsubscribe):
                        self._unsub_fns.append(unsubscribe)
                except Exception:
                    self._log.exception(
                        "[SYSTEM_TRAY] Kunde inte prenumerera på %s",
                        event_name,
                    )

        def _on_event(self, data: dict[str, Any]) -> None:
            self._log.debug("[SYSTEM_TRAY] Fallback tog emot event: %s", data)

# ═════════════════════════════════════════════════════════════════════════════
# METADATA
# ═════════════════════════════════════════════════════════════════════════════

__description__: str = PLUGIN_DESCRIPTION
__copyright__: str = COPYRIGHT

PLUGIN_INFO: dict[str, Any] = {
    "id": "system_tray",
    "name": PLUGIN_NAME,
    "version": PLUGIN_VERSION,
    "author": PLUGIN_AUTHOR,
    "description": PLUGIN_DESCRIPTION,
    "copyright": COPYRIGHT,
    "plugin_class": Plugin,
    "has_tab": False,
    "is_service": True,
    "priority": getattr(Plugin, "priority", 5),
    "platforms": ["windows", "darwin", "linux"],
    "min_python": "3.12",
    "requires": ["PySide6"],
    "optional": ["Pillow"],
    "models_loaded": _MODELS_LOADED,
    "plugin_loaded": _PLUGIN_LOADED,
    "fully_loaded": _MODELS_LOADED and _PLUGIN_LOADED,
    "load_errors": dict(_LOAD_ERRORS),
    "events": {
        "listens_to": [
            "certificate.generated",
            "certificate.generation_failed",
            "certificates.batch.started",
            "certificates.batch.completed",
            "tray.status_update",
            "tray.minimize",
            "tray.restore",
            "app.minimize_to_tray",
            "app.restore_from_tray",
            "app.toggle_window",
            "app.shutdown",
            "show_settings",
            "notification",
            "window.show",
            "window.hide",
        ],
        "emits": [
            "tray.minimize",
            "tray.restore",
            "window.show",
            "window.hide",
            "app.quit_confirmed",
            "app.shutdown",
            "show_settings",
            "notification",
        ],
    },
}

# ═════════════════════════════════════════════════════════════════════════════
# HJÄLPFUNKTIONER
# ═════════════════════════════════════════════════════════════════════════════

def get_plugin_info() -> dict[str, Any]:
    """Returnera pluginmetadata inklusive aktuell laddningsstatus."""
    with _LOAD_LOCK:
        info = dict(PLUGIN_INFO)
        info["plugin_class"] = Plugin
        info["models_loaded"] = _MODELS_LOADED
        info["plugin_loaded"] = _PLUGIN_LOADED
        info["fully_loaded"] = _MODELS_LOADED and _PLUGIN_LOADED
        info["load_errors"] = dict(_LOAD_ERRORS)
        info["priority"] = getattr(Plugin, "priority", 5)
        return info


def get_plugin_class() -> type[Any]:
    """Returnera aktiv Plugin-klass."""
    return Plugin


def is_fully_loaded() -> bool:
    """True om både models.py och plugin.py laddades korrekt."""
    with _LOAD_LOCK:
        return _MODELS_LOADED and _PLUGIN_LOADED


def is_models_loaded() -> bool:
    """True om models.py laddades korrekt."""
    with _LOAD_LOCK:
        return _MODELS_LOADED


def is_plugin_loaded() -> bool:
    """True om plugin.py laddades korrekt."""
    with _LOAD_LOCK:
        return _PLUGIN_LOADED


def get_load_errors() -> dict[str, str]:
    """Returnera eventuella laddningsfel."""
    with _LOAD_LOCK:
        return dict(_LOAD_ERRORS)


def get_platform() -> Platform:
    """Returnera nuvarande plattform."""
    return Platform.current()


def is_tray_supported() -> bool:
    """
    Kontrollera om QSystemTrayIcon stöds på nuvarande system.

    Kräver inte pystray och skapar ingen tray-ikon.
    """
    try:
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        if QApplication.instance() is None:
            return False
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception:
        _LOGGER.exception("Kunde inte kontrollera system tray-stöd")
        return False


def create_notification(
    title: str,
    message: str,
    notification_type: NotificationType = NotificationType.INFO,
) -> Notification:
    """Skapa Notification-instans med kompatibel konstruktor."""
    try:
        return Notification(
            title=title,
            message=message,
            type=notification_type,
        )
    except TypeError:
        return Notification(
            title=title,
            message=message,
            notification_type=notification_type,  # type: ignore[call-arg]
        )


def create_recent_certificate(
    name: str,
    path: str | Path,
    template: str = "",
    number: int = 0,
) -> RecentCertificate:
    """Skapa RecentCertificate-instans med säker Path-konvertering."""
    return RecentCertificate(
        name=name,
        path=Path(path),
        template=template,
        number=number,
    )


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    return COPYRIGHT

# ═════════════════════════════════════════════════════════════════════════════
# EXPORTS
# ═════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    "Plugin",
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PLUGIN_AUTHOR",
    "PLUGIN_DESCRIPTION",
    "COPYRIGHT",
    "PLUGIN_INFO",
    "__version__",
    "__author__",
    "__copyright__",
    "__description__",
    "is_fully_loaded",
    "is_models_loaded",
    "is_plugin_loaded",
    "get_load_errors",
    "get_plugin_info",
    "get_plugin_class",
    "get_copyright",
    "MAX_RECENT_CERTIFICATES",
    "ANIMATION_SPEED_MS",
    "NOTIFICATION_DURATION_MS",
    "TRAY_CLICK_THRESHOLD_MS",
    "ICON_SIZE",
    "ICON_SIZES",
    "DEFAULT_HOTKEY",
    "MIN_WINDOW_WIDTH",
    "MIN_WINDOW_HEIGHT",
    "DEFAULT_WINDOW_WIDTH",
    "DEFAULT_WINDOW_HEIGHT",
    "WINDOW_SCREEN_SCALE",
    "TOOLTIP_NORMAL",
    "TOOLTIP_MINIMIZED",
    "TOOLTIP_BUSY",
    "get_tooltip_normal",
    "get_tooltip_minimized",
    "get_tooltip_busy",
    "Platform",
    "IconState",
    "NotificationType",
    "CloseAction",
    "Notification",
    "RecentCertificate",
    "TabInfo",
    "TrayConfigHelper",
    "IconGenerator",
    "get_platform",
    "is_tray_supported",
    "create_notification",
    "create_recent_certificate",
]
