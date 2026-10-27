from __future__ import annotations

# plugins/system_tray/models.py
# Sökväg: Intygsgenerator/plugins/system_tray/models.py
# IntygsDrive — Datamodeller, enums, konstanter och ikonhjälpare
# för system_tray-pluginet.
#
# Version: 9.0.0
# © Rickard Längkvist 2025–2026
#
# Nytt i v9.0.0:
#   • Cachad Platform.current() (beräknas en gång)
#   • _logger PEP8-konvention
#   • Ta bort encoding-kommentar
#   • Version synkad med plugin.py v9.0.0
#
# Färger i IconState/NotificationType är semantiska
# ikon-renderingsfärger enligt COLORS-001-undantaget.

import logging
import math
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    # PIL.Image är en modul — klassen heter också Image.
    # Importera klassen (inte modulen) så att typannoteringar är giltiga.
    from PIL.Image import Image as _PILImage

_logger: logging.Logger = logging.getLogger(__name__)

_START_YEAR: int = 2025
_CURRENT_YEAR: int = datetime.now().year
COPYRIGHT: str = (
    f"© Rickard Längkvist {_START_YEAR}–{_CURRENT_YEAR}"
    if _CURRENT_YEAR > _START_YEAR
    else f"© Rickard Längkvist {_START_YEAR}"
)

PLUGIN_NAME: str = "System Tray"
PLUGIN_VERSION: str = "9.3.0"
PLUGIN_AUTHOR: str = "Rickard Längkvist"
PLUGIN_DESCRIPTION: str = (
    "Systemfälts-integration med notifikationer, "
    "tab-hantering och snabbåtgärder"
)

MAX_RECENT_CERTIFICATES: int = 10
ANIMATION_SPEED_MS: int = 120
NOTIFICATION_DURATION_MS: int = 5000
TRAY_CLICK_THRESHOLD_MS: int = 300
ICON_SIZE: int = 64
ICON_SIZES: dict[str, int] = {
    "windows": 64,
    "darwin": 22,
    "linux": 48,
    "unknown": 64,
}
DEFAULT_HOTKEY: str = "ctrl+shift+i"
MIN_WINDOW_WIDTH: int = 400
MIN_WINDOW_HEIGHT: int = 300
DEFAULT_WINDOW_WIDTH: int = 1100
DEFAULT_WINDOW_HEIGHT: int = 750
WINDOW_SCREEN_SCALE: float = 0.8


def get_copyright() -> str:
    """Returnera dynamisk copyright-sträng."""
    return COPYRIGHT

# ─── Platform ────────────────────────────────────────────────────────────────


class Platform(Enum):
    """Operativsystem-plattformar med metadata."""

    WINDOWS = ("Windows", "🪟", "win32", 64)
    MACOS = ("macOS", "🍎", "darwin", 22)
    LINUX = ("Linux", "🐧", "linux", 48)
    UNKNOWN = ("Okänd", "❓", "unknown", 64)

    def __init__(
        self,
        display_name: str,
        emoji: str,
        sys_prefix: str,
        icon_size: int,
    ) -> None:
        self.display_name = display_name
        self.emoji = emoji
        self.sys_prefix = sys_prefix
        self.default_icon_size = icon_size

    # Endast annotering: ett tilldelat värde här skulle skapa ett enum-medlem
    # och krascha klassen. Cachen sätts i stället vid första current()-anropet.
    _cached_current: Platform | None

    @classmethod
    def current(cls) -> Platform:
        """Detektera plattform (cachad — beräknas en gång)."""
        cached = getattr(cls, "_cached_current", None)
        if cached is not None:
            return cached
        platform_str = sys.platform.lower()
        for platform in cls:
            if (
                platform != cls.UNKNOWN
                and platform_str.startswith(platform.sys_prefix)
            ):
                cls._cached_current = platform
                return platform
        cls._cached_current = cls.UNKNOWN
        return cls.UNKNOWN

    @classmethod
    def is_windows(cls) -> bool:
        return cls.current() == cls.WINDOWS

    @classmethod
    def is_macos(cls) -> bool:
        return cls.current() == cls.MACOS

    @classmethod
    def is_linux(cls) -> bool:
        return cls.current() == cls.LINUX

    @property
    def supports_autostart(self) -> bool:
        return self in (
            Platform.WINDOWS,
            Platform.MACOS,
            Platform.LINUX,
        )

    @property
    def supports_tray(self) -> bool:
        return self != Platform.UNKNOWN

    @property
    def supports_notifications(self) -> bool:
        return self != Platform.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "emoji": self.emoji,
            "sys_prefix": self.sys_prefix,
            "default_icon_size": self.default_icon_size,
            "supports_autostart": self.supports_autostart,
            "supports_tray": self.supports_tray,
            "supports_notifications": self.supports_notifications,
        }

# ─── IconState ───────────────────────────────────────────────────────────────


class IconState(Enum):
    """Tray-ikonens visuella tillstånd (COLORS-001 undantag)."""

    NORMAL = "normal"
    BUSY = "busy"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    NOTIFICATION = "notification"
    PAUSED = "paused"
    MINIMIZED = "minimized"

    @property
    def color(self) -> tuple[int, int, int]:
        colors: dict[IconState, tuple[int, int, int]] = {
            IconState.NORMAL: (33, 150, 243),
            IconState.BUSY: (255, 152, 0),
            IconState.SUCCESS: (76, 175, 80),
            IconState.WARNING: (255, 193, 7),
            IconState.ERROR: (244, 67, 54),
            IconState.NOTIFICATION: (156, 39, 176),
            IconState.PAUSED: (158, 158, 158),
            IconState.MINIMIZED: (96, 125, 139),
        }
        return colors.get(self, colors[IconState.NORMAL])

    @property
    def color_dark(self) -> tuple[int, int, int]:
        colors: dict[IconState, tuple[int, int, int]] = {
            IconState.NORMAL: (25, 118, 210),
            IconState.BUSY: (245, 124, 0),
            IconState.SUCCESS: (56, 142, 60),
            IconState.WARNING: (255, 160, 0),
            IconState.ERROR: (211, 47, 47),
            IconState.NOTIFICATION: (123, 31, 162),
            IconState.PAUSED: (117, 117, 117),
            IconState.MINIMIZED: (69, 90, 100),
        }
        return colors.get(self, colors[IconState.NORMAL])

    @property
    def hex_color(self) -> str:
        r, g, b = self.color
        return f"#{r:02x}{g:02x}{b:02x}"

    @property
    def tooltip_suffix(self) -> str:
        suffixes: dict[IconState, str] = {
            IconState.NORMAL: "",
            IconState.BUSY: " - Arbetar...",
            IconState.SUCCESS: " - Klart!",
            IconState.WARNING: " - Varning",
            IconState.ERROR: " - Fel uppstod",
            IconState.NOTIFICATION: " - Ny notifikation",
            IconState.PAUSED: " - Pausad",
            IconState.MINIMIZED: " - Minimerad",
        }
        return suffixes.get(self, "")

    @classmethod
    def from_string(cls, value: str) -> IconState:
        normalized = str(value or "").strip().lower()
        for state in cls:
            if normalized in {state.name.lower(), state.value.lower()}:
                return state
        return cls.NORMAL

# ─── NotificationType ────────────────────────────────────────────────────────


class NotificationType(Enum):
    """Notifikationstyp (COLORS-001 undantag)."""

    INFO = ("info", "ℹ️", IconState.NOTIFICATION, "#2196F3")
    SUCCESS = ("success", "✅", IconState.SUCCESS, "#4CAF50")
    WARNING = ("warning", "⚠️", IconState.WARNING, "#FF9800")
    ERROR = ("error", "❌", IconState.ERROR, "#F44336")

    def __init__(
        self,
        value_key: str,
        emoji: str,
        icon_state: IconState,
        color: str,
    ) -> None:
        self._value_ = value_key
        self.emoji = emoji
        self.icon_state = icon_state
        self.color = color

    @classmethod
    def from_string(cls, value: str) -> NotificationType:
        normalized = str(value or "").strip().lower()
        for nt in cls:
            if normalized in {nt.name.lower(), nt.value.lower()}:
                return nt
        return cls.INFO

# ─── CloseAction ─────────────────────────────────────────────────────────────


class CloseAction(Enum):
    """Åtgärd när användaren stänger fönstret."""

    MINIMIZE_TO_TRAY = "minimize_to_tray"
    CLOSE_TO_TRAY = "close_to_tray"
    EXIT_APPLICATION = "exit_application"
    ASK_USER = "ask_user"

    @property
    def description(self) -> str:
        descriptions: dict[CloseAction, str] = {
            CloseAction.MINIMIZE_TO_TRAY: "Minimera till systemfältet",
            CloseAction.CLOSE_TO_TRAY: "Dölj till systemfältet",
            CloseAction.EXIT_APPLICATION: "Avsluta programmet",
            CloseAction.ASK_USER: "Fråga varje gång",
        }
        return descriptions.get(self, "Okänd åtgärd")

    @classmethod
    def from_string(cls, value: str) -> CloseAction:
        normalized = str(value or "").strip().lower()
        for action in cls:
            if normalized in {action.name.lower(), action.value.lower()}:
                return action
        return cls.MINIMIZE_TO_TRAY

# ─── Dataklasser ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Notification:
    """En notifikation att visa i systemfältet."""

    title: str
    message: str
    type: NotificationType = NotificationType.INFO
    duration_ms: int = NOTIFICATION_DURATION_MS
    callback: Callable[[], None] | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    shown: bool = False

    def __post_init__(self) -> None:
        self.title = str(self.title or "")
        self.message = str(self.message or "")
        self.duration_ms = max(0, int(self.duration_ms))
        if isinstance(self.type, str):
            self.type = NotificationType.from_string(self.type)
        if isinstance(self.timestamp, str):
            try:
                self.timestamp = datetime.fromisoformat(self.timestamp)
            except ValueError:
                self.timestamp = datetime.now()

    @property
    def emoji(self) -> str:
        return self.type.emoji

    @property
    def icon_state(self) -> IconState:
        return self.type.icon_state

    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.timestamp).total_seconds()

    @property
    def is_expired(self) -> bool:
        if self.duration_ms <= 0:
            return False
        return self.age_seconds > (self.duration_ms / 1000.0)

    def mark_shown(self) -> None:
        self.shown = True

    def trigger_callback(self) -> bool:
        if not callable(self.callback):
            return False
        try:
            self.callback()
            return True
        except Exception:
            _logger.exception("Notification callback fel")
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "message": self.message,
            "type": self.type.value,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "shown": self.shown,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Notification:
        ts_raw = data.get("timestamp", datetime.now().isoformat())
        ts = datetime.now()
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw)
            except ValueError:
                pass
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        return cls(
            title=str(data.get("title", "")),
            message=str(data.get("message", "")),
            type=NotificationType.from_string(str(data.get("type", "info"))),
            duration_ms=int(
                data.get("duration_ms", NOTIFICATION_DURATION_MS)
            ),
            timestamp=ts,
            shown=bool(data.get("shown", False)),
        )


@dataclass(slots=True)
class RecentCertificate:
    """Information om ett nyligen skapat intyg."""

    name: str
    path: Path
    template: str = ""
    number: int = 0
    created: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        self.name = str(self.name or "")
        self.path = Path(self.path)
        self.template = str(self.template or "")
        self.number = int(self.number or 0)
        if isinstance(self.created, str):
            try:
                self.created = datetime.fromisoformat(self.created)
            except ValueError:
                self.created = datetime.now()

    @property
    def created_at(self) -> datetime:
        return self.created

    @created_at.setter
    def created_at(self, value: datetime) -> None:
        self.created = value

    @property
    def display_name(self) -> str:
        if self.number:
            return f"#{self.number} - {self.template or self.name}"
        return self.name

    @property
    def short_name(self) -> str:
        display = self.display_name
        return f"{display[:37]}..." if len(display) > 40 else display

    @property
    def age_text(self) -> str:
        total = (datetime.now() - self.created).total_seconds()
        if total < 60:
            return "Just nu"
        if total < 3600:
            return f"{int(total / 60)} min sedan"
        if total < 86400:
            return f"{int(total / 3600)} tim sedan"
        days = int(total / 86400)
        return f"{days} dag{'ar' if days > 1 else ''} sedan"

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "template": self.template,
            "number": self.number,
            "created": self.created.isoformat(),
            "created_at": self.created.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecentCertificate:
        created_raw = data.get("created") or data.get("created_at")
        created = datetime.now()
        if isinstance(created_raw, str):
            try:
                created = datetime.fromisoformat(created_raw)
            except ValueError:
                pass
        elif isinstance(created_raw, datetime):
            created = created_raw
        return cls(
            name=str(data.get("name", "")),
            path=Path(data.get("path", "")),
            template=str(data.get("template", "")),
            number=int(data.get("number", 0) or 0),
            created=created,
        )


@dataclass(slots=True)
class TabInfo:
    """Information om en tab/flik."""

    name: str
    plugin_name: str
    visible: bool = True
    order: int = 0
    frame: Any = None
    icon: str = "📄"

    def __post_init__(self) -> None:
        self.name = str(self.name or "")
        self.plugin_name = str(self.plugin_name or "")
        self.visible = bool(self.visible)
        self.order = int(self.order or 0)
        self.icon = str(self.icon or "📄")

    @property
    def display_name(self) -> str:
        return f"{self.icon} {self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "plugin_name": self.plugin_name,
            "visible": self.visible,
            "order": self.order,
            "icon": self.icon,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TabInfo:
        return cls(
            name=str(data.get("name", "")),
            plugin_name=str(data.get("plugin_name", "")),
            visible=bool(data.get("visible", True)),
            order=int(data.get("order", 0) or 0),
            icon=str(data.get("icon", "📄")),
        )

# ─── Tooltips ────────────────────────────────────────────────────────────────


def get_tooltip_normal(app_name: str = "IntygsDrive") -> str:
    return f"{app_name} - Klicka för att öppna"


def get_tooltip_minimized(app_name: str = "IntygsDrive") -> str:
    return f"{app_name} - Körs i bakgrunden"


def get_tooltip_busy(app_name: str = "IntygsDrive") -> str:
    return f"{app_name} - Arbetar..."


TOOLTIP_NORMAL: str = get_tooltip_normal()
TOOLTIP_MINIMIZED: str = get_tooltip_minimized()
TOOLTIP_BUSY: str = get_tooltip_busy()

# ─── TrayConfigHelper ────────────────────────────────────────────────────────


class TrayConfigHelper:
    """Hjälpklass för tray-konfiguration via ConfigManager."""

    DEFAULTS: dict[str, Any] = {
        "start_minimized": True,
        "start_with_os": False,
        "close_to_tray": True,
        "minimize_to_tray": True,
        "show_notifications": True,
        "show_minimize_notification": True,
        "single_click_open": True,
        "global_hotkey": DEFAULT_HOTKEY,
        "tab_visibility": {},
        "recent_certificates": [],
    }

    def __init__(self, config_manager: Any) -> None:
        self._config = config_manager
        self._cache: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._load_cache()

    def _load_cache(self) -> None:
        with self._lock:
            for key, default in self.DEFAULTS.items():
                self._cache[key] = self._get_raw(f"tray.{key}", default)

    def reload(self) -> None:
        self._load_cache()

    def _get_raw(self, key: str, default: Any = None) -> Any:
        if self._config is None:
            return default
        getter = getattr(self._config, "get", None)
        if not callable(getter):
            return default
        try:
            value = getter(key, default)
            return value if value is not None else default
        except Exception:
            return default

    def _set_raw(self, key: str, value: Any) -> None:
        if self._config is None:
            return
        full_key = key if key.startswith("tray.") else f"tray.{key}"
        setter = getattr(self._config, "set", None)
        if not callable(setter):
            return
        try:
            setter(full_key, value)
            saver = getattr(self._config, "save", None)
            if callable(saver):
                saver()
        except Exception:
            _logger.exception("Tray config skrivfel: %s", full_key)

    def _get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            return self._get_raw(f"tray.{key}", default)

    def _set(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = value
        self._set_raw(key, value)

    @staticmethod
    def get_bool(config: Any, key: str, default: bool = False) -> bool:
        helper = TrayConfigHelper(config)
        return bool(helper._get(key.removeprefix("tray."), default))

    @staticmethod
    def set_bool(config: Any, key: str, value: bool) -> bool:
        try:
            helper = TrayConfigHelper(config)
            helper._set(key.removeprefix("tray."), bool(value))
            return True
        except Exception:
            return False

    @staticmethod
    def should_start_minimized(config: Any) -> bool:
        return TrayConfigHelper.get_bool(config, "tray.start_minimized", True)

    @staticmethod
    def should_close_to_tray(config: Any) -> bool:
        return TrayConfigHelper.get_bool(config, "tray.close_to_tray", True)

    # Properties

    @property
    def start_minimized(self) -> bool:
        return bool(self._get("start_minimized", True))

    @start_minimized.setter
    def start_minimized(self, value: bool) -> None:
        self._set("start_minimized", bool(value))

    @property
    def start_with_os(self) -> bool:
        return bool(self._get("start_with_os", False))

    @start_with_os.setter
    def start_with_os(self, value: bool) -> None:
        self._set("start_with_os", bool(value))

    @property
    def close_to_tray(self) -> bool:
        return bool(self._get("close_to_tray", True))

    @close_to_tray.setter
    def close_to_tray(self, value: bool) -> None:
        self._set("close_to_tray", bool(value))

    @property
    def minimize_to_tray(self) -> bool:
        return bool(self._get("minimize_to_tray", True))

    @minimize_to_tray.setter
    def minimize_to_tray(self, value: bool) -> None:
        self._set("minimize_to_tray", bool(value))

    @property
    def single_click_open(self) -> bool:
        return bool(self._get("single_click_open", True))

    @single_click_open.setter
    def single_click_open(self, value: bool) -> None:
        self._set("single_click_open", bool(value))

    @property
    def show_notifications(self) -> bool:
        return bool(self._get("show_notifications", True))

    @show_notifications.setter
    def show_notifications(self, value: bool) -> None:
        self._set("show_notifications", bool(value))

    @property
    def show_minimize_notification(self) -> bool:
        return bool(self._get("show_minimize_notification", True))

    @show_minimize_notification.setter
    def show_minimize_notification(self, value: bool) -> None:
        self._set("show_minimize_notification", bool(value))

    @property
    def global_hotkey(self) -> str:
        return str(self._get("global_hotkey", DEFAULT_HOTKEY))

    @global_hotkey.setter
    def global_hotkey(self, value: str) -> None:
        self._set("global_hotkey", str(value))

    @property
    def tab_visibility(self) -> dict[str, bool]:
        value = self._get("tab_visibility", {})
        if isinstance(value, dict):
            return {str(k): bool(v) for k, v in value.items()}
        return {}

    @tab_visibility.setter
    def tab_visibility(self, value: dict[str, bool]) -> None:
        self._set(
            "tab_visibility",
            {str(k): bool(v) for k, v in value.items()},
        )

    def get_tab_visible(self, tab_name: str, default: bool = True) -> bool:
        return self.tab_visibility.get(tab_name, default)

    def set_tab_visible(self, tab_name: str, visible: bool) -> None:
        vis = self.tab_visibility.copy()
        vis[tab_name] = bool(visible)
        self.tab_visibility = vis

    @property
    def recent_certificates(self) -> list[dict[str, Any]]:
        value = self._get("recent_certificates", [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    @recent_certificates.setter
    def recent_certificates(self, value: list[dict[str, Any]]) -> None:
        cleaned = [item for item in value if isinstance(item, dict)]
        self._set(
            "recent_certificates",
            cleaned[:MAX_RECENT_CERTIFICATES],
        )

    def add_recent_certificate(self, cert: RecentCertificate) -> None:
        cert_dict = cert.to_dict()
        certs = [
            item
            for item in self.recent_certificates
            if item.get("path") != str(cert.path)
        ]
        certs.insert(0, cert_dict)
        self.recent_certificates = certs

    def get_recent_certificates(self) -> list[RecentCertificate]:
        certs: list[RecentCertificate] = []
        for item in self.recent_certificates:
            try:
                certs.append(RecentCertificate.from_dict(item))
            except Exception:
                pass
        return certs

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_minimized": self.start_minimized,
            "start_with_os": self.start_with_os,
            "close_to_tray": self.close_to_tray,
            "minimize_to_tray": self.minimize_to_tray,
            "show_notifications": self.show_notifications,
            "show_minimize_notification": self.show_minimize_notification,
            "single_click_open": self.single_click_open,
            "global_hotkey": self.global_hotkey,
            "tab_visibility": self.tab_visibility,
            "recent_certificates": self.recent_certificates,
        }

# ─── IconGenerator ───────────────────────────────────────────────────────────


class IconGenerator:
    """Genererar tray-ikoner via Pillow eller QPainter."""

    @classmethod
    def create_icon(
        cls,
        state: IconState = IconState.NORMAL,
        size: int = ICON_SIZE,
    ) -> _PILImage | None:
        """Skapa PIL-ikon. Returnerar None om Pillow saknas."""
        try:
            from PIL import Image, ImageDraw

            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            bg = (*state.color, 255)
            border = (*state.color_dark, 255)
            symbol = (255, 255, 255, 255)
            margin = max(size // 16, 2)
            bw = max(size // 24, 1)
            draw.ellipse(
                [margin, margin, size - margin, size - margin],
                fill=bg,
                outline=border,
                width=bw,
            )
            draw_map: dict[
                IconState,
                Callable[[Any, int, tuple[int, ...]], None],
            ] = {
                IconState.BUSY: cls._draw_busy_symbol,
                IconState.ERROR: cls._draw_error_symbol,
                IconState.SUCCESS: cls._draw_success_symbol,
                IconState.WARNING: cls._draw_warning_symbol,
                IconState.NOTIFICATION: cls._draw_notification_symbol,
                IconState.PAUSED: cls._draw_paused_symbol,
                IconState.MINIMIZED: cls._draw_minimized_symbol,
            }
            draw_fn = draw_map.get(state, cls._draw_normal_symbol)
            draw_fn(draw, size, symbol)
            return image
        except ImportError:
            return None
        except Exception:
            _logger.exception("PIL-ikon kunde inte skapas")
            return None

    @classmethod
    def create_qicon(
        cls,
        state: IconState = IconState.NORMAL,
        size: int = ICON_SIZE,
    ) -> Any:
        """Skapa QIcon via QPainter."""
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtGui import (
                QColor,
                QIcon,
                QPainter,
                QPen,
                QPixmap,
            )

            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(
                QPainter.RenderHint.Antialiasing, True
            )
            r, g, b = state.color
            dr, dg, db = state.color_dark
            margin = max(size // 16, 2)
            bw = max(size // 24, 1)
            painter.setBrush(QColor(r, g, b))
            painter.setPen(QPen(QColor(dr, dg, db), bw))
            painter.drawEllipse(
                margin, margin,
                size - 2 * margin, size - 2 * margin,
            )
            painter.setPen(
                QPen(QColor(255, 255, 255), max(size // 12, 3))
            )
            cls._draw_qicon_symbol(painter, state, size)
            painter.end()
            return QIcon(pixmap)
        except Exception:
            _logger.exception("QPainter-ikon fel")
            try:
                from PySide6.QtGui import QIcon

                return QIcon()
            except Exception:
                return None

    @classmethod
    def _draw_qicon_symbol(
        cls,
        painter: Any,
        state: IconState,
        size: int,
    ) -> None:
        from PySide6.QtCore import QPoint, QRect, Qt

        if state == IconState.ERROR:
            m = int(size * 0.30)
            painter.drawLine(m, m, size - m, size - m)
            painter.drawLine(size - m, m, m, size - m)
            return
        if state == IconState.SUCCESS:
            points = [
                QPoint(int(size * 0.25), int(size * 0.52)),
                QPoint(int(size * 0.42), int(size * 0.68)),
                QPoint(int(size * 0.75), int(size * 0.32)),
            ]
            painter.drawPolyline(points)
            return
        if state == IconState.WARNING:
            cx = size // 2
            painter.drawLine(cx, int(size * 0.25), cx, int(size * 0.58))
            painter.drawPoint(cx, int(size * 0.70))
            return
        if state in (IconState.BUSY, IconState.NOTIFICATION):
            rect = QRect(
                int(size * 0.28),
                int(size * 0.28),
                int(size * 0.44),
                int(size * 0.44),
            )
            painter.drawArc(rect, 25 * 16, 290 * 16)
            return
        if state == IconState.PAUSED:
            top = int(size * 0.28)
            h = int(size * 0.44)
            w = max(int(size * 0.12), 4)
            color = painter.pen().color()
            painter.fillRect(int(size * 0.36), top, w, h, color)
            painter.fillRect(int(size * 0.52), top, w, h, color)
            return
        # Normal / Minimized: "I" bokstav
        font = painter.font()
        font.setBold(True)
        font.setPointSize(max(10, int(size * 0.45)))
        painter.setFont(font)
        painter.drawText(
            QRect(0, 0, size, size),
            Qt.AlignmentFlag.AlignCenter,
            "I",
        )

    @classmethod
    def _draw_normal_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        stem_w = max(int(size * 0.12), 4)
        serif_h = max(int(size * 0.08), 3)
        cx = size // 2
        top = int(size * 0.23)
        bottom = int(size * 0.77)
        draw.rectangle(
            [
                cx - stem_w // 2, top + serif_h,
                cx + stem_w // 2, bottom - serif_h,
            ],
            fill=color,
        )
        draw.rectangle(
            [
                int(size * 0.31), top,
                int(size * 0.69), top + serif_h,
            ],
            fill=color,
        )
        draw.rectangle(
            [
                int(size * 0.31), bottom - serif_h,
                int(size * 0.69), bottom,
            ],
            fill=color,
        )

    @classmethod
    def _draw_busy_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        cx = size // 2
        cy = size // 2
        radius = int(size * 0.25)
        w = max(size // 14, 2)
        bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
        draw.arc(bbox, 0, 120, fill=color, width=w)
        draw.arc(bbox, 180, 300, fill=color, width=w)

    @classmethod
    def _draw_error_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        m = int(size * 0.30)
        w = max(size // 10, 3)
        draw.line([m, m, size - m, size - m], fill=color, width=w)
        draw.line([size - m, m, m, size - m], fill=color, width=w)

    @classmethod
    def _draw_success_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        w = max(size // 10, 3)
        points = [
            (int(size * 0.25), int(size * 0.52)),
            (int(size * 0.42), int(size * 0.68)),
            (int(size * 0.75), int(size * 0.32)),
        ]
        draw.line(points[:2], fill=color, width=w)
        draw.line(points[1:], fill=color, width=w)

    @classmethod
    def _draw_warning_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        cx = size // 2
        w = max(size // 14, 2)
        draw.rectangle(
            [cx - w, int(size * 0.25), cx + w, int(size * 0.58)],
            fill=color,
        )
        radius = w + 1
        cy = int(size * 0.70)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=color,
        )

    @classmethod
    def _draw_notification_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        cx = size // 2
        cy = size // 2
        radius = int(size * 0.23)
        w = max(size // 16, 2)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=color,
            width=w,
        )
        draw.line(
            [cx, cy - radius + 4, cx, cy],
            fill=color, width=w,
        )
        draw.line(
            [cx, cy, cx + radius - 6, cy + 4],
            fill=color, width=w,
        )

    @classmethod
    def _draw_paused_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        bar_w = max(int(size * 0.12), 4)
        gap = max(int(size * 0.10), 4)
        cx = size // 2
        top = int(size * 0.28)
        bottom = int(size * 0.72)
        draw.rectangle(
            [cx - gap - bar_w, top, cx - gap, bottom],
            fill=color,
        )
        draw.rectangle(
            [cx + gap, top, cx + gap + bar_w, bottom],
            fill=color,
        )

    @classmethod
    def _draw_minimized_symbol(
        cls,
        draw: Any,
        size: int,
        color: tuple[int, ...],
    ) -> None:
        w = max(size // 10, 3)
        y = int(size * 0.62)
        draw.line(
            [int(size * 0.30), y, int(size * 0.70), y],
            fill=color, width=w,
        )

    @classmethod
    def create_animation_frames(
        cls,
        state: IconState = IconState.BUSY,
        frame_count: int = 8,
        size: int = ICON_SIZE,
    ) -> list[_PILImage]:
        """Skapa animationsramar för busy-ikon."""
        frames: list[Any] = []
        total = max(1, int(frame_count))
        try:
            from PIL import Image, ImageDraw

            bg = state.color
            border = state.color_dark
            for idx in range(total):
                image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                draw = ImageDraw.Draw(image)
                margin = max(size // 16, 2)
                bw = max(size // 24, 1)
                draw.ellipse(
                    [margin, margin, size - margin, size - margin],
                    fill=(*bg, 255),
                    outline=(*border, 255),
                    width=bw,
                )
                cx = size // 2
                cy = size // 2
                radius = int(size * 0.25)
                start = (idx * 360 // total) % 360
                end = start + 90
                arc_w = max(size // 14, 2)
                draw.arc(
                    [cx - radius, cy - radius, cx + radius, cy + radius],
                    start,
                    end,
                    fill=(255, 255, 255, 255),
                    width=arc_w,
                )
                dot_angle = start + 180
                dot_x = cx + int(
                    (radius - 4) * math.cos(math.radians(dot_angle))
                )
                dot_y = cy + int(
                    (radius - 4) * math.sin(math.radians(dot_angle))
                )
                dot_r = max(size // 20, 2)
                draw.ellipse(
                    [dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
                    fill=(255, 255, 255, 200),
                )
                frames.append(image)
        except ImportError:
            pass
        except Exception:
            _logger.exception("Animationsramar fel")
        return frames

    @classmethod
    def load_custom_icon(
        cls,
        path: Path,
        size: int = ICON_SIZE,
    ) -> _PILImage | None:
        """Ladda anpassad ikon från fil."""
        try:
            from PIL import Image

            icon_path = Path(path)
            if not icon_path.exists():
                return None
            image = Image.open(icon_path)
            if image.size != (size, size):
                image = image.resize(
                    (size, size), Image.Resampling.LANCZOS
                )
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            return image
        except ImportError:
            return None
        except Exception:
            _logger.exception("Custom icon fel: %s", path)
            return None

    @classmethod
    def get_platform_icon_size(cls) -> int:
        """Lämplig ikonstorlek för plattformen."""
        return Platform.current().default_icon_size

    @classmethod
    def find_icon_file(cls, icon_dir: Path) -> Path | None:
        """Hitta första ikonfil i Icon/."""
        for filename in (
            "icon.png", "icon.ico", "app.png",
            "app.ico", "tray.png", "logo.png",
            "icon.icns",
        ):
            path = icon_dir / filename
            if path.exists() and path.is_file():
                return path
        return None

# ─── Export ──────────────────────────────────────────────────────────────────

__all__: list[str] = [
    "PLUGIN_NAME",
    "PLUGIN_VERSION",
    "PLUGIN_AUTHOR",
    "PLUGIN_DESCRIPTION",
    "COPYRIGHT",
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
]
